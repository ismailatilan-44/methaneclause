// Browser-wallet back end (app/wallet.js) end to end against a local Anvil judge sandbox. Run: make wallet-local
// (scripts/wallet_local.sh starts Anvil, deploys the core + sandbox + sandbox pool, then runs this file).
//
// app/wallet.js is the exact module the page loads; its two pinned CDN imports (viem 2.57.1) are mapped here to the same
// viem version installed in out/e2e-tools (make wallet-setup), so this runs offline. The injected EIP-1193 provider is
// backed by an Anvil test key and signs typed data with MetaMask's own library (@metamask/eth-sig-util, V4), so the
// typed data the module hands a wallet is checked against the code MetaMask runs, then against the contract on chain.
//
// Covers: parity with the Python signer (evidenceHash, Attestation digest, sceneHash, CleanPass digest, policy hash, the
// step-0 CleanPass vector, the JCS vectors) and with the contracts' own digest views; a swapped wallet.json (another
// chain, another covenant) refused by createWallet, a wrong published-key address refused (KEY_MISMATCH); connect (chain switch, declined
// switch, no wallet); join as observer (OBS_JUDGE, again = no-op, the OBS_INDEPENDENT open slot); covenant; sign ->
// submit episode -> finalize after the window; tamper (edited bundle: refused before sending, then a status-0
// InvalidSignature with state unchanged); refused validation signs nothing; the cure (DZA relapse -> claim -> clean
// pass signed by the wallet -> counted after the clean window, tampered scene refused); status. Exit 1 on a failure.
import { registerHooks } from "node:module";
import { readFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = resolve(fileURLToPath(import.meta.url), "../../..");
const TOOLS = process.env.WALLET_TOOLS || join(ROOT, "out", "e2e-tools");
const VIEM = "2.57.1";
const CDN = {
  [`https://cdn.jsdelivr.net/npm/viem@${VIEM}/+esm`]: "viem",
  [`https://cdn.jsdelivr.net/npm/viem@${VIEM}/accounts/+esm`]: "viem/accounts",
};
const pkgVersion = (name) => {
  const p = join(TOOLS, "node_modules", ...name.split("/"), "package.json");
  return existsSync(p) ? JSON.parse(readFileSync(p, "utf8")).version : null;
};
if (pkgVersion("viem") !== VIEM || !pkgVersion("@metamask/eth-sig-util")) {
  console.error(`wallet test: needs viem ${VIEM} and @metamask/eth-sig-util in ${TOOLS} (make wallet-setup)`);
  process.exit(1);
}
const toolsParent = pathToFileURL(join(TOOLS, "resolve.mjs")).href;
registerHooks({
  resolve(spec, ctx, next) {
    if (CDN[spec]) return next(CDN[spec], { ...ctx, parentURL: toolsParent });
    if (spec === "viem" || spec.startsWith("viem/")) return next(spec, { ...ctx, parentURL: toolsParent });
    if (spec.startsWith("https://")) throw new Error(`unmapped network import ${spec}`);
    return next(spec, ctx);
  },
  load(url, ctx, next) {  // app/ has no package.json: wallet.js is an ES module, as the page loads it
    return url.endsWith("/app/wallet.js") ? next(url, { ...ctx, format: "module" }) : next(url, ctx);
  },
});

const W = await import(pathToFileURL(join(ROOT, "app", "wallet.js")).href);
const { createPublicClient, createWalletClient, http, hashTypedData, toHex, getAddress, encodeFunctionData } = await import("viem");
const { mnemonicToAccount } = await import("viem/accounts");
const sigUtil = await import(pathToFileURL(join(TOOLS, "node_modules", "@metamask", "eth-sig-util", "dist", "index.cjs")).href)
  .catch(() => import(pathToFileURL(join(TOOLS, "node_modules", "@metamask", "eth-sig-util", "dist", "index.js")).href));
const { signTypedData, SignTypedDataVersion, TypedDataUtils } = sigUtil.default ?? sigUtil;

const RPC = process.env.RPC_URL || "http://127.0.0.1:8548";
const NET = JSON.parse(readFileSync(join(ROOT, "out", "wallet", "network-31337.json"), "utf8"));
const data = JSON.parse(readFileSync(join(ROOT, "app", "data", "wallet.json"), "utf8"));
const MNEMONIC = "test test test test test test test test test test test junk";
const local = { chainId: 31337, name: "Anvil (local)", explorer: null, rpc: RPC, pollMs: 50, sandbox: NET };
const pub = createPublicClient({ transport: http(RPC), pollingInterval: 50 });

let n = 0;
const t0 = Date.now();
const ok = (cond, msg, extra) => {
  if (!cond) { console.log(`   FAIL  ${msg}`); if (extra) console.log(JSON.stringify(extra, null, 1).slice(0, 3000)); process.exit(1); }
  n++; console.log(`   ok    ${msg}`);
};
const rpc = (method, params = []) => pub.request({ method, params });
const advance = async (secs) => { await rpc("evm_increaseTime", [secs]); await rpc("evm_mine"); };

/** An EIP-1193 provider like MetaMask, backed by one Anvil test key. Counts what it was asked to do. */
function walletProvider(index, { startChain = 1, declineSwitch = false } = {}) {
  const account = mnemonicToAccount(MNEMONIC, { addressIndex: index });
  const pk = Buffer.from(account.getHdKey().privateKey);
  const wc = createWalletClient({ account, transport: http(RPC), chain: { id: 31337, name: "anvil",
    nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 }, rpcUrls: { default: { http: [RPC] } } } });
  let chainId = startChain;
  const calls = { sign: 0, send: 0 };
  return {
    address: account.address, calls,
    async request({ method, params = [] }) {
      switch (method) {
        case "eth_requestAccounts": case "eth_accounts": return [account.address.toLowerCase()];
        case "eth_chainId": return toHex(chainId);
        case "wallet_switchEthereumChain":
          if (declineSwitch) throw Object.assign(new Error("User rejected the request."), { code: 4001 });
          if (params[0].chainId !== toHex(31337)) throw Object.assign(new Error("Unrecognized chain"), { code: 4902 });
          chainId = 31337; return null;
        case "eth_signTypedData_v4": {
          if (getAddress(params[0]) !== account.address) throw new Error("wrong signer");
          calls.sign++;
          return signTypedData({ privateKey: pk, data: JSON.parse(params[1]), version: SignTypedDataVersion.V4 });
        }
        case "eth_sendTransaction": {
          const tx = params[0];
          if (chainId !== 31337) throw new Error("wallet is on another chain");
          calls.send++;
          return wc.sendTransaction({ to: tx.to, data: tx.data, value: BigInt(tx.value || 0), ...(tx.gas ? { gas: BigInt(tx.gas) } : {}) });
        }
        default: return rpc(method, params);
      }
    },
  };
}
const show = (r) => r.steps.map((s) => `${s.status} ${s.label}`).join("\n");

try {
  // ------------------------------------------------------------------ parity with the Python signer and the contracts
  console.log("== parity: page module vs Python signer vs contracts vs MetaMask's signing library");
  const vecs = JSON.parse(readFileSync(join(ROOT, "app", "data", "vectors", "expected.json"), "utf8"));
  for (const [name, v] of Object.entries(vecs)) {
    const doc = JSON.parse(readFileSync(join(ROOT, "app", "data", "vectors", name), "utf8"));
    ok(W.jcs(doc) === v.canonical && (await W.canonicalHash(doc)) === v.evidenceHash, `JCS + SHA-256 vector ${name}`);
  }
  const P = data.parity, I = P.inputs;
  const lead = { ...data.detections.TKM_S_276.bundleTemplate, covenantId: I.covenantId };
  const ev = await W.canonicalHash(lead);
  ok(ev === P.evidenceHash, `evidenceHash equals Python's (${ev.slice(0, 18)}…)`);
  const att = W.attestationFor(lead, ev, { nonce: I.nonce, expiry: I.expiry });
  const td = W.attestationTypedData(att, I.chainId, I.covenant);
  ok(hashTypedData(td) === P.attestationDigest, "Attestation EIP-712 digest equals Python's (pipeline/observer/attest.py)");
  ok("0x" + TypedDataUtils.eip712Hash(JSON.parse(W.typedDataJson(td)), SignTypedDataVersion.V4).toString("hex") === P.attestationDigest,
    "the JSON handed to eth_signTypedData_v4 hashes to the same digest in MetaMask's library");
  const cb = { ...data.cleanPasses.DZA_S_012.bundleTemplate, covenantId: I.covenantId, claimId: I.claimId };
  const sh = await W.canonicalHash(cb);
  ok(sh === P.sceneHash, "sceneHash equals Python's");
  const msg = W.cleanPassFor(cb, sh, { nonce: I.nonce, expiry: I.expiry });
  ok(JSON.stringify(W.plain(msg)) === JSON.stringify(Object.fromEntries(Object.keys(msg).map((k) => [k, typeof P.cleanPassMessage[k] === "number" ? String(P.cleanPassMessage[k]) : P.cleanPassMessage[k]]))),
    "CleanPass message fields equal Python's (cloudBp, instrument, floor, kind)");
  const ctd = W.cleanPassTypedData(msg, I.chainId, I.pool);
  ok(hashTypedData(ctd) === P.cleanPassDigest, "CleanPass EIP-712 digest equals Python's");
  ok("0x" + TypedDataUtils.eip712Hash(JSON.parse(W.typedDataJson(ctd)), SignTypedDataVersion.V4).toString("hex") === P.cleanPassDigest,
    "CleanPass JSON for eth_signTypedData_v4 hashes the same in MetaMask's library");
  const v1 = JSON.parse(readFileSync(join(ROOT, "pipeline", "tests", "vectors", "cleanpass_v1.json"), "utf8"));
  ok(hashTypedData(W.cleanPassTypedData(v1.cleanPass, v1.chainId, v1.verifyingContract)) === v1.cleanPassDigest,
    "step-0 CleanPass vector (pipeline/tests/vectors/cleanpass_v1.json) reproduced");
  const pol = { ...data.covenant.policy, bondWei: data.covenant.bondWei, judge: I.judge, sourceSetRoot: data.detections.TKM_S_276.facility.root };
  ok((await W.canonicalHash(pol)) === P.policyHash, "covenant policy hash equals Python's rule (judge.py cmd_attest)");
  for (const role of Object.keys(data.sandboxKeys.addresses)) {
    const a = (await import("viem/accounts")).privateKeyToAccount(W.publishedKey(data.sandboxKeys.prefix, role)).address;
    ok(a === getAddress(data.sandboxKeys.addresses[role]), `published key ${role} derives to the recorded address ${a.slice(0, 10)}…`);
  }
  const abiCov = data.abi.MethaneCovenant, abiPool = data.abi.IncentivePool;
  const onchain = await pub.readContract({ address: NET.methaneCovenant, abi: abiCov, functionName: "attestationDigest",
    args: [{ ...att }] });
  ok(onchain === hashTypedData(W.attestationTypedData(att, 31337, NET.methaneCovenant)), "the covenant contract computes the same Attestation digest on Anvil");
  const onchainCp = await pub.readContract({ address: NET.incentivePool, abi: abiPool, functionName: "cleanPassDigest", args: [{ ...msg }] });
  ok(onchainCp === hashTypedData(W.cleanPassTypedData(msg, 31337, NET.incentivePool)), "the pool contract computes the same CleanPass digest on Anvil");

  // ------------------------------------------------------------------ connect
  console.log("\n== connect");
  const refusedData = (d) => { try { W.createWallet({ ethereum: null, data: d }); return null; } catch (e) { return e.code || String(e); } };
  ok(refusedData(data) === null, "the committed wallet.json passes the pinned Sepolia sandbox check (no network override)");
  ok(refusedData({ ...data, network: { ...data.network, chainId: 1 } }) === "WRONG_CHAIN",
    "a swapped wallet.json naming another chain: createWallet refuses with WRONG_CHAIN");
  ok(refusedData({ ...data, network: { ...data.network, sandbox: { ...data.network.sandbox, methaneCovenant: getAddress("0x" + "22".repeat(20)) } } }) === "WRONG_CONTRACTS",
    "a swapped wallet.json naming another covenant: createWallet refuses with WRONG_CONTRACTS");
  const none = await W.createWallet({ ethereum: null, data, network: local }).connect();
  ok(!none.ok && none.error.code === "NO_WALLET", "no injected wallet: a clear NO_WALLET result, nothing thrown");
  const declined = await W.createWallet({ ethereum: walletProvider(8, { declineSwitch: true }), data, network: local }).connect();
  ok(!declined.ok && declined.error.code === "USER_REJECTED", "wallet on another chain and the switch declined: USER_REJECTED");
  const judge = walletProvider(8);
  const w = W.createWallet({ ethereum: judge, data, network: local });
  const early = await w.joinAsObserver();
  ok(!early.ok && ["NOT_CONNECTED", "WRONG_CHAIN"].includes(early.error.code) && judge.calls.send === 0, `before connect: ${early.error.code}, nothing sent`);
  const c = await w.connect();
  ok(c.ok && c.account === judge.address && c.chainId === 31337, `connect: account ${judge.address.slice(0, 10)}…, wallet switched from chain 1 to 31337`, c);
  ok(c.steps.some((s) => s.id === "switch"), "the switch is a labelled step");

  // ------------------------------------------------------------------ join as observer
  console.log("\n== join as observer (OBS_JUDGE, as make judge-attest)");
  const badKeys = { ...data, sandboxKeys: { ...data.sandboxKeys,
    addresses: { ...data.sandboxKeys.addresses, JUDGE_CONTROLLER: getAddress("0x" + "11".repeat(20)) } } };
  const sendsBefore = judge.calls.send;
  const km = await W.createWallet({ ethereum: judge, data: badKeys, network: local }).joinAsObserver();
  ok(!km.ok && km.error.code === "KEY_MISMATCH" && judge.calls.send === sendsBefore,
    "a wallet.json whose controller address differs from the published key's: KEY_MISMATCH, nothing sent", km);
  const j = await w.joinAsObserver();
  ok(j.ok && j.org === "OBS_JUDGE" && !j.alreadyRegistered && j.tx.status === 1, "joinAsObserver: registered as OBSERVER of OBS_JUDGE", j);
  ok(j.steps.filter((s) => s.sim).length >= 2, "the published-controller steps carry sim: true");
  const [org, role, valid] = await pub.readContract({ address: NET.keyRegistry, abi: data.abi.KeyRegistry, functionName: "keyStatus",
    args: [judge.address, BigInt(Math.floor(Date.now() / 1000) + 10 ** 7)] });
  ok(org === W.b32("OBS_JUDGE") && role === 1 && valid, "the registry reports the wallet as an OBS_JUDGE observer key");
  const j2 = await w.joinAsObserver();
  ok(j2.ok && j2.alreadyRegistered, "joining again is a no-op (no transaction)");

  // ------------------------------------------------------------------ covenant
  console.log("\n== your sandbox covenant");
  const cov = await w.openCovenant();
  ok(cov.ok && /^0x[0-9a-f]{64}$/.test(cov.covenantId), `openCovenant: covenant ${BigInt(cov.covenantId)}, bond posted from the wallet`, cov);
  const onCov = await pub.readContract({ address: NET.methaneCovenant, abi: abiCov, functionName: "getCovenant", args: [cov.covenantId] });
  ok(onCov.policyHash === cov.policyHash && onCov.remaining === BigInt(data.covenant.bondWei), "on chain: the policy hash the page computed, the full bond");
  ok(onCov.policyHash === await W.canonicalHash({ ...data.covenant.policy, bondWei: data.covenant.bondWei, judge: judge.address, sourceSetRoot: data.detections.TKM_S_276.facility.root }),
    "the covenant's policy hash names this wallet as the judge");

  // ------------------------------------------------------------------ sign -> submit episode
  console.log("\n== sign -> submit episode (TKM_S_276 2026-06-11)");
  const signsBefore = judge.calls.sign;
  const s1 = await w.signAttestation({ case: "TKM_S_276" });
  ok(s1.ok && s1.signer === judge.address && judge.calls.sign === signsBefore + 1, "signAttestation: one eth_signTypedData_v4 request, recovers to the wallet", s1);
  ok(s1.steps.map((s) => s.id).join(",") === "fetch,validate,seal,sign,recover", "five labelled steps: fetch, validate, seal, sign, recover");
  const s2 = await w.cosignAttestation({ case: "TKM_S_276" });
  ok(s2.ok && s2.evidenceHash === s1.evidenceHash && s2.signer === getAddress(data.sandboxKeys.addresses.OBS_1), "published observer 1 co-signs the identical bundle");
  const sub = await w.submitEpisode({ bundle: s1.bundle, signatures: [s1, s2] });
  ok(sub.ok && sub.status === 1 && sub.episode.state === "PROPOSED", `submitEpisode: episode ${sub.episodeId} PROPOSED, rebate held`, sub);
  ok(BigInt(sub.bondBeforeWei) - BigInt(sub.bondAfterWei) === BigInt(sub.episode.rebateWei), "the rebate moved from the bond to held (no payment yet)");

  // ------------------------------------------------------------------ tamper
  console.log("\n== tamper: fresh signatures, then 2,548 -> 548 kg/h in the bundle");
  const t1 = await w.signAttestation({ case: "TKM_S_276" });
  const t2 = await w.cosignAttestation({ case: "TKM_S_276" });
  const edited = { ...t1.bundle, emissionKgH: t1.bundle.emissionKgH - 2000 };
  const sends = judge.calls.send;
  const pre = await w.submitEpisode({ bundle: edited, signatures: [t1, t2] });
  ok(!pre.ok && pre.error.code === "WOULD_REVERT" && pre.revert.name === "InvalidSignature" && judge.calls.send === sends,
    "refused before sending: InvalidSignature decoded from the ABI, no transaction, no gas", pre);
  const tam = await w.submitEpisode({ bundle: edited, signatures: [t1, t2], expectRevert: "InvalidSignature" });
  const rc = await pub.getTransactionReceipt({ hash: tam.tx?.hash });
  ok(tam.ok && tam.status === 0 && tam.revert.name === "InvalidSignature" && rc.status === "reverted", "sent on purpose: receipt status 0, InvalidSignature read from the mined transaction", tam);
  ok(tam.stateUnchanged, "bond and episode count unchanged after the reverted transaction");
  const sneaky = await w.signAttestation({ bundle: edited });
  ok(!sneaky.ok && sneaky.error.code === "VALIDATION_REFUSED" && judge.calls.sign === signsBefore + 2, "an edited bundle is refused at Validate: the wallet is never asked to sign it");

  // ------------------------------------------------------------------ finalize after the window
  console.log("\n== finalize after the challenge window");
  const early2 = await w.finalizeEpisode(sub.episodeId);
  ok(!early2.ok && early2.error.code === "WINDOW_OPEN" && early2.secondsLeft > 0, `window open: WINDOW_OPEN, ${early2.secondsLeft} s left, nothing sent`);
  await advance(early2.secondsLeft + 1);
  console.log(`   [SIM: Anvil clock advanced ${early2.secondsLeft + 1} s — challenge window]`);
  const fin = await w.finalizeEpisode(sub.episodeId);
  ok(fin.ok && fin.episode.state === "CONFIRMED" && BigInt(fin.buyerWithdrawableWei) === BigInt(sub.episode.rebateWei), "finalizeEpisode: CONFIRMED, the buyer is credited the rebate", fin);

  // ------------------------------------------------------------------ cure: relapse -> claim -> clean pass
  console.log("\n== cure: DZA_S_012 relapse -> refund claim -> clean pass signed by the wallet");
  const d1 = await w.signAttestation({ case: "DZA_S_012" });
  const d2 = await w.cosignAttestation({ case: "DZA_S_012" });
  const dsub = await w.submitEpisode({ bundle: d1.bundle, signatures: [d1, d2] });
  ok(dsub.ok && dsub.episode.state === "PROPOSED", `relapse episode ${dsub.episodeId} PROPOSED`, dsub);
  await advance(dsub.episode.secondsLeft + 1);
  const dfin = await w.finalizeEpisode(dsub.episodeId);
  ok(dfin.ok && dfin.episode.state === "CONFIRMED", "relapse episode CONFIRMED after the window", dfin);
  const noClaim = await w.signCleanPass({ case: "DZA_S_012" });
  ok(!noClaim.ok && noClaim.error.code === "NO_CLAIM", "signCleanPass before a claim exists: NO_CLAIM, nothing signed");
  const cl = await w.openRefundClaim(dsub.episodeId);
  ok(cl.ok && /^0x[0-9a-f]{64}$/.test(cl.claimId), "openRefundClaim: pool opened by the published buyer key, claim opened from the wallet", cl);
  const p1 = await w.signCleanPass({ case: "DZA_S_012" });
  const p2 = await w.cosignCleanPass({ case: "DZA_S_012" });
  ok(p1.ok && p2.ok && p1.sceneHash === p2.sceneHash && p1.signer === judge.address, "clean pass signed by the wallet and co-signed by published observer 1", p1.ok ? p2 : p1);
  const cloud = { ...p1.bundle, cloudCoverPct: (p1.bundle.cloudCoverPct ?? 0) + 30 };
  const ptam = await w.submitCleanPass({ bundle: cloud, signatures: [p1, p2] });
  ok(!ptam.ok && ptam.error.code === "WOULD_REVERT", `edited cloud field: the pool refuses it (${ptam.revert?.name}); nothing sent`, ptam);
  const psub = await w.submitCleanPass({ bundle: p1.bundle, signatures: [p1, p2] });
  ok(psub.ok && psub.status === 1, `submitCleanPass: pass ${psub.passId} PENDING with the pass bond`, psub);
  const early3 = await w.countPass(psub.passId);
  ok(!early3.ok && early3.error.code === "WINDOW_OPEN", "countPass inside the clean window: WINDOW_OPEN, nothing sent");
  await advance(early3.secondsLeft + 1);
  const cnt = await w.countPass(psub.passId);
  ok(cnt.ok && cnt.counted === 1, `countPass: ${cnt.counted} qualified clean pass counted, u(N) ${cnt.upperBoundBp / 100} %`, cnt);

  // ------------------------------------------------------------------ status
  console.log("\n== status (live reads)");
  const st = await w.status();
  ok(st.ok && st.observer.org === "OBS_JUDGE" && st.observer.valid, "status: your key, org OBS_JUDGE, valid", st);
  ok(st.covenant.id === cov.covenantId && st.episodes.length === 2 && st.episodes.every((e) => e.state === "CONFIRMED"), "status: your covenant and both episodes CONFIRMED");
  ok(st.claim.state === "OPEN" && st.claim.counted === 1 && st.passes[0].state === "COUNTED" && st.pool.state === "OPEN", "status: claim OPEN, 1 pass COUNTED, pool OPEN");
  ok(BigInt(st.withdrawable.covenantWei) > 0n, "status: your proposer bonds are back in your withdrawable balance");

  // ------------------------------------------------------------------ the open observer slot
  console.log("\n== a second wallet joins the open observer slot (OBS_INDEPENDENT)");
  const friend = walletProvider(9);
  const w2 = W.createWallet({ ethereum: friend, data, network: local });
  await w2.connect();
  const jo = await w2.joinAsObserver({ org: "OBS_INDEPENDENT" });
  ok(jo.ok && jo.org === "OBS_INDEPENDENT", "joinAsObserver({ org: 'OBS_INDEPENDENT' }): registered through the open slot", jo);
  const again = await w2.joinAsObserver({ org: "OBS_JUDGE" });
  ok(!again.ok && again.error.code === "ALREADY_IN_REGISTRY", "the same key cannot join a second org (a key is registered once)");
  console.log(`\nWALLET OK: ${n} checks in ${((Date.now() - t0) / 1000).toFixed(1)} s`);
} catch (e) {
  console.log(`   FAIL  ${e?.stack || e}`);
  process.exit(1);
}
