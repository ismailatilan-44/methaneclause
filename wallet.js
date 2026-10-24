// MethaneClause — browser-wallet back end for the judge sandbox (no DOM code; any UI can call it).
//
// A judge connects an injected EIP-1193 wallet (MetaMask or any other), becomes a registered observer key of an
// observer org in the judge sandbox on Ethereum Sepolia, signs the EIP-712 attestation over a canonical evidence bundle
// with their own key, submits it, and reads the result back from the chain. The judge's private key never leaves the
// wallet: the page only asks the wallet to sign typed data (eth_signTypedData_v4) and to send transactions
// (eth_sendTransaction).
//
// The judge sandbox is simulated on purpose: its seller, buyer, two observers and two org controllers use keys that are
// published (private key = keccak256 of a public string, app/data/wallet.json `sandboxKeys`), so a fresh wallet can
// play every other role. They protect nothing and live in their own KeyRegistry; the main deployment's keys are not
// published. Every step that uses one says so (`sim: true`). The page derives those keys from the public strings at
// run time and refuses one whose address differs from the deployment record copied into wallet.json or that the
// registry does not name. Without an explicit `network` override it works on Ethereum Sepolia only, and only with the
// three sandbox contracts pinned below (SEPOLIA_SANDBOX): a swapped wallet.json cannot point it anywhere else.
//
// The evidence bundles come from the observer pipeline (scripts/build_wallet_data.py, from the committed case files):
// Carbon Mapper sends no CORS header, so the browser cannot fetch the provider. The page fills in the ids that only
// exist on chain (your covenant, your claim), re-hashes (RFC 8785 + SHA-256) and builds the typed data; the parity
// vector in wallet.json pins every hash and digest to Python's (scripts/tests/wallet.test.mjs checks them).
//
// Every function returns a plain result object, never throws for an expected failure:
//   { ok, action, steps: [{ id, label, status: "done"|"failed"|"info", sim?, tx?: { hash, link, status } }],
//     error?: { code, message }, ...payload }
// Amounts and other uint256 values are decimal strings; times are unix seconds (numbers).
import {
  createPublicClient, decodeErrorResult, decodeEventLog, defineChain, encodeFunctionData, getAddress, hashTypedData,
  http, keccak256, recoverTypedDataAddress, stringToHex,
} from "https://cdn.jsdelivr.net/npm/viem@2.57.1/+esm";
import { privateKeyToAccount } from "https://cdn.jsdelivr.net/npm/viem@2.57.1/accounts/+esm";

// ---------------------------------------------------------------------------------------------- constants
export const SEPOLIA_CHAIN_ID = 11155111;
// The judge sandbox [SIM] on Ethereum Sepolia (contracts/deployments/11155111.json `sandbox`; a lane test checks they
// agree). createWallet refuses a wallet.json that names another chain or other contracts unless `network` is passed.
export const SEPOLIA_SANDBOX = Object.freeze({
  registry: "0x9BEb9Ca5a2BC166e0C3231ee8E65295b8d4F0682",
  methaneCovenant: "0x12B9f286Fc5B0fD0006F1B31335E78fe7E0f0aDa",
  incentivePool: "0x5D323582991A0e11B9Acf144a29d8CC206c9D982",
});
// SEPOLIA_SANDBOX entry -> its field in wallet.json `network.sandbox` (the registry's field is `keyRegistry`)
const SANDBOX_FIELD = { registry: "keyRegistry", methaneCovenant: "methaneCovenant", incentivePool: "incentivePool" };
const ZERO32 = "0x" + "00".repeat(32);
const KIND_DETECT = 1, KIND_CLEAN = 4;
const ROLE_NAMES = ["NONE", "OBSERVER", "SELLER", "BUYER"];
const EPISODE_STATES = ["NONE", "PROPOSED", "CHALLENGED", "CONTESTED", "CONFIRMED", "VOID", "EXPIRED", "LINKED"];
const PASS_STATES = ["NONE", "PENDING", "CHALLENGED", "COUNTED", "VOID", "EXPIRED"];
const POOL_STATES = ["NONE", "OPEN", "CLOSING", "CLOSED"];
const CLAIM_STATES = ["NONE", "OPEN", "CANCELLED", "CLOSED"];
const MODE_ONCHAIN = 0;
const INSTRUMENTS = { tan: 1, emi: 2, av3: 3, ang: 4, gao: 5 };
const MAX_RATE_KGH = 100_000;
const RADIUS_KM = 1.5, EARTH_R_KM = 6371.0088;
const REVERT_GAS = 3_000_000n;          // a deliberately reverting call is sent with a fixed limit (as chain.py)
const GAS_BUDGET = 3_000_000n;          // when an estimate is refused by the RPC
const PRIORITY_FEE_WEI = 1_000_000n;    // 0.001 gwei tip for the published-key transactions (as chain.py)
const EXPIRY_SECONDS = 3600n;           // attestations expire one hour after signing (contract: <= 1 day)
const SECP256K1_N = 0xfffffffffffffffffffffffffffffffebaaedce6af48a03bbfd25e8cd0364141n;
const LAG = ["header not found", "unknown block", "block not found"];

const DOMAIN_FIELDS = [
  { name: "name", type: "string" }, { name: "version", type: "string" },
  { name: "chainId", type: "uint256" }, { name: "verifyingContract", type: "address" },
];
// Byte-compatible with contracts/src/lib/Attestation.sol and pipeline/observer/attest.py TYPES
export const ATTESTATION_TYPES = {
  Attestation: [
    { name: "covenantId", type: "bytes32" }, { name: "sourceId", type: "bytes32" },
    { name: "evidenceHash", type: "bytes32" }, { name: "modelHash", type: "bytes32" },
    { name: "emissionKgH", type: "uint32" }, { name: "uncertaintyKgH", type: "uint32" },
    { name: "detectedAt", type: "uint64" }, { name: "instrumentId", type: "uint8" },
    { name: "providerId", type: "bytes32" }, { name: "kind", type: "uint8" },
    { name: "nonce", type: "uint64" }, { name: "expiry", type: "uint64" },
  ],
};
// Byte-compatible with contracts/src/lib/CleanPass.sol and pipeline/observer/attest.py POOL_TYPES
export const CLEANPASS_TYPES = {
  CleanPass: [
    { name: "claimId", type: "bytes32" }, { name: "sourceId", type: "bytes32" },
    { name: "sceneId", type: "bytes32" }, { name: "sceneHash", type: "bytes32" },
    { name: "sceneAt", type: "uint64" }, { name: "publishedAt", type: "uint64" },
    { name: "instrumentId", type: "uint8" }, { name: "providerId", type: "bytes32" },
    { name: "cloudBp", type: "uint16" }, { name: "notCloudy", type: "bool" },
    { name: "floorKgH", type: "uint32" }, { name: "rateFloorKgH", type: "uint32" },
    { name: "kind", type: "uint8" }, { name: "nonce", type: "uint64" }, { name: "expiry", type: "uint64" },
  ],
};

// ---------------------------------------------------------------------------------------------- pure helpers
const enc = new TextEncoder();
const hex = (bytes) => "0x" + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
const short = (h, a = 10, b = 4) => (h && h.length > a + b + 1 ? `${h.slice(0, a)}…${h.slice(-b)}` : h);
const utc = (t) => new Date(Number(t) * 1000).toISOString().replace("T", " ").replace(/\.\d+Z$/, " UTC");
export const formatEth = (wei) => {
  const w = BigInt(wei), whole = w / 10n ** 18n, frac = (w % 10n ** 18n).toString().padStart(18, "0").replace(/0+$/, "");
  return `${whole}${frac ? "." + frac : ""} ETH`;
};

/** RFC 8785 (JCS) for the JSON values the bundles contain (strings, finite numbers, booleans, null, arrays, objects). */
export function jcs(v) {
  if (typeof v === "number" && !Number.isFinite(v)) throw new Error("non-finite number is not valid JSON");
  if (v === null || typeof v !== "object") return JSON.stringify(v);
  if (Array.isArray(v)) return "[" + v.map(jcs).join(",") + "]";
  return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + jcs(v[k])).join(",") + "}";
}
export async function sha256Hex(text) {
  return hex(new Uint8Array(await crypto.subtle.digest("SHA-256", enc.encode(text))));
}
/** evidenceHash / sceneHash / policyHash: SHA-256 of the RFC 8785 bytes (pipeline/canon.py evidence_hash). */
export const canonicalHash = (obj) => sha256Hex(jcs(obj));

/** Short ASCII -> right-padded bytes32 (Solidity `bytes32 c = "carbonmapper"`); 0x-hex of 32 bytes passes through. */
export function b32(x) {
  if (typeof x === "string" && x.startsWith("0x")) {
    if (x.length !== 66) throw new Error("bytes32 hex must be 32 bytes");
    return x.toLowerCase();
  }
  const b = enc.encode(x);
  if (b.length > 32) throw new Error("longer than 32 bytes");
  return hex(b).padEnd(66, "0");
}
const keccakText = (s) => keccak256(stringToHex(s));
const iso = (s) => Math.floor(Date.parse(s) / 1000);

function haversineKm(lat1, lon1, lat2, lon2) {
  const r = (d) => (d * Math.PI) / 180, p1 = r(lat1), p2 = r(lat2), dphi = p2 - p1, dl = r(lon2) - r(lon1);
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * EARTH_R_KM * Math.asin(Math.sqrt(a));
}

/** EIP-712 typed data for an Attestation under the covenant's domain (name "MethaneClause", version "1"). */
export function attestationTypedData(att, chainId, verifyingContract) {
  return {
    domain: { name: "MethaneClause", version: "1", chainId: BigInt(chainId), verifyingContract: getAddress(verifyingContract) },
    types: ATTESTATION_TYPES, primaryType: "Attestation", message: asUints(att, ATTESTATION_TYPES.Attestation),
  };
}
/** EIP-712 typed data for a CleanPass under the pool's domain. */
export function cleanPassTypedData(msg, chainId, pool) {
  return {
    domain: { name: "MethaneClause", version: "1", chainId: BigInt(chainId), verifyingContract: getAddress(pool) },
    types: CLEANPASS_TYPES, primaryType: "CleanPass", message: asUints(msg, CLEANPASS_TYPES.CleanPass),
  };
}
function asUints(msg, fields) {
  const out = {};
  for (const f of fields) {
    const v = msg[f.name];
    if (v === undefined || v === null) throw new Error(`typed data field ${f.name} is missing`);
    out[f.name] = f.type.startsWith("uint") ? BigInt(v) : f.type === "bytes32" ? b32(v) : v;
  }
  return out;
}
/** The JSON string eth_signTypedData_v4 takes: EIP712Domain listed, uints as decimal strings. */
export function typedDataJson(td) {
  return JSON.stringify({ types: { EIP712Domain: DOMAIN_FIELDS, ...td.types }, primaryType: td.primaryType,
    domain: td.domain, message: td.message }, (_, v) => (typeof v === "bigint" ? v.toString() : v));
}

/** The Attestation for a detection bundle (pipeline/observer/service.py Observer.attest), given the evidenceHash. */
export function attestationFor(bundle, evidenceHash, { nonce, expiry, kind = KIND_DETECT }) {
  const instrumentId = INSTRUMENTS[bundle.instrument];
  if (!instrumentId) throw new Error(`unknown instrument ${bundle.instrument}`);
  return {
    covenantId: b32(bundle.covenantId), sourceId: b32(bundle.sourceId), evidenceHash: b32(evidenceHash),
    modelHash: ZERO32, emissionKgH: BigInt(bundle.emissionKgH), uncertaintyKgH: BigInt(bundle.uncertaintyKgH),
    detectedAt: BigInt(bundle.detectedAt), instrumentId: BigInt(instrumentId), providerId: b32(bundle.providerId),
    kind: BigInt(kind), nonce: BigInt(nonce), expiry: BigInt(expiry),
  };
}
const cloudBp = (pct, assessed) => {
  const v = [pct, assessed].filter((x) => x !== null && x !== undefined);
  return v.length ? 100 * Math.max(...v) : null;
};
/** The CleanPass message for a clean-pass bundle (pipeline/observer/cleanpass.py message), given the sceneHash. */
export function cleanPassFor(bundle, sceneHash, { nonce, expiry }) {
  const instrumentId = INSTRUMENTS[bundle.instrument];
  const cbp = cloudBp(bundle.cloudCoverPct, bundle.cloudCoverPctAssessed);
  if (!instrumentId || cbp === null || bundle.notCloudy === null || bundle.floorKgH === null || bundle.publishedAt === null) {
    throw new Error("the scene lacks a field the CleanPass message needs (instrument, cloud cover, notCloudy, floor, publishedAt)");
  }
  return {
    claimId: b32(bundle.claimId), sourceId: b32(bundle.sourceId), sceneId: b32(bundle.sceneId), sceneHash: b32(sceneHash),
    sceneAt: BigInt(bundle.sceneAt), publishedAt: BigInt(bundle.publishedAt), instrumentId: BigInt(instrumentId),
    providerId: b32(bundle.providerId), cloudBp: BigInt(cbp), notCloudy: bundle.notCloudy,
    floorKgH: BigInt(bundle.floorKgH), rateFloorKgH: BigInt(bundle.rateFloorKgH), kind: BigInt(KIND_CLEAN),
    nonce: BigInt(nonce), expiry: BigInt(expiry),
  };
}

/**
 * The checks the terminal observer runs before signing a detection (pipeline/observer/bundle.py validate), against the
 * live covenant: returns [] when signable, else a list of { code, message } refusals.
 */
export function validateDetection(b, template, facility, { covenantStart, now, minRateKgH }) {
  const out = [];
  const no = (code, message) => out.push({ code, message });
  const keys = Object.keys(template).sort().join(","), have = Object.keys(b).sort().join(",");
  if (keys !== have) no("SCHEMA", "the bundle's fields differ from the observer schema");
  for (const [k, v] of Object.entries(template)) {
    if (k !== "covenantId" && JSON.stringify(b[k]) !== JSON.stringify(v)) no("NOT_FROM_OBSERVER", `${k} differs from the observer's bundle`);
  }
  if (!/^0x[0-9a-f]{64}$/.test(b.covenantId || "")) no("SCHEMA", "covenantId is not a 32-byte hex id");
  if (!(b.emissionKgH >= 1 && b.emissionKgH < MAX_RATE_KGH)) no("RANGE", `emissionKgH ${b.emissionKgH} out of range`);
  if (b.emissionKgH < minRateKgH) no("BELOW_FLOOR", `emissionKgH ${b.emissionKgH} < covenant minimum ${minRateKgH}`);
  if (iso(b.sceneTimestamp) !== b.detectedAt) no("TIME", "detectedAt != sceneTimestamp");
  if (b.detectedAt <= covenantStart) no("TIME", "detected before the covenant started");
  const pub = iso(b.publishedAt);
  if (b.detectedAt > now || pub > now) no("TIME", "detection or publication is in the future");
  if (pub < b.detectedAt) no("TIME", "published before the scene was acquired");
  if (b.sourceId !== facility.sourceId || b.sourceName !== facility.sourceName) no("NOT_COVERED", "not the covered facility");
  if (!facility.coveredCmSources.includes(b.cmSourceName)) no("NOT_COVERED", `${b.cmSourceName} is not linked to the facility`);
  const d = haversineKm(facility.lat, facility.lon, b.plumeLat, b.plumeLon);
  if (d > RADIUS_KM) no("DISTANCE", `plume origin ${d.toFixed(2)} km from the facility > ${RADIUS_KM} km`);
  return out;
}

/** 65-byte r‖s‖v with v in {27, 28} and low s (what SigLib accepts); wallets differ on v and some on s. */
export function normalizeSignature(sig) {
  if (!/^0x[0-9a-fA-F]{130}$/.test(sig)) throw new Error("the wallet returned a signature that is not 65 bytes");
  let r = sig.slice(2, 66), s = BigInt("0x" + sig.slice(66, 130)), v = parseInt(sig.slice(130, 132), 16);
  if (v < 27) v += 27;
  if (s > SECP256K1_N / 2n) { s = SECP256K1_N - s; v = v === 27 ? 28 : 27; }
  return "0x" + r + s.toString(16).padStart(64, "0") + v.toString(16);
}

/** The published judge-sandbox key of `role` (generation n > 1: "<role>#n"). Public by design: it protects nothing. */
export function publishedKey(prefix, role, generation = 1) {
  return keccakText(prefix + (generation === 1 ? role : `${role}#${generation}`));
}

export async function loadWalletData(url = "data/wallet.json", fetchFn = globalThis.fetch) {
  const r = await fetchFn(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  const d = await r.json();
  if (d.schema !== "methaneclause.wallet/1") throw new Error(`${url}: unknown schema ${d.schema}`);
  return d;
}

/** Plain JSON for a UI: bigint -> decimal string, arrays and objects copied (viem returns bigints for uints). */
export function plain(v) {
  if (typeof v === "bigint") return v.toString();
  if (Array.isArray(v)) return v.map(plain);
  if (v && typeof v === "object") return Object.fromEntries(Object.entries(v).map(([k, x]) => [k, plain(x)]));
  return v;
}

// ---------------------------------------------------------------------------------------------- the wallet session
class Refusal extends Error {
  constructor(code, message, extra = {}) { super(message); this.code = code; this.extra = extra; }
}

/**
 * createWallet({ ethereum, data, network?, store? })
 *   ethereum  an EIP-1193 provider (default window.ethereum)
 *   data      app/data/wallet.json (loadWalletData())
 *   network   override of data.network (the local Anvil test passes { chainId: 31337, rpc, explorer: null, sandbox });
 *             without it, data.network must be Ethereum Sepolia with the SEPOLIA_SANDBOX contracts, else createWallet
 *             throws an Error whose `code` is WRONG_CHAIN or WRONG_CONTRACTS (nothing is read or sent)
 *   store     optional { get(key), set(key, value) } to keep the session (covenant, episodes, claim, passes) across
 *             reloads, e.g. a localStorage adapter; default in memory
 */
export function createWallet({ ethereum = globalThis.ethereum, data, network, store } = {}) {
  if (!data) throw new Error("createWallet: pass data (loadWalletData())");
  if (!network) {
    const dn = data.network || {}, sb = dn.sandbox || {};
    if (dn.chainId !== SEPOLIA_CHAIN_ID) {
      throw new Refusal("WRONG_CHAIN", `wallet.json names chain ${dn.chainId}; this page works on Ethereum Sepolia (${SEPOLIA_CHAIN_ID}) only.`);
    }
    for (const [k, want] of Object.entries(SEPOLIA_SANDBOX)) {
      const have = sb[SANDBOX_FIELD[k]];
      if (typeof have !== "string" || have.toLowerCase() !== want.toLowerCase()) {
        throw new Refusal("WRONG_CONTRACTS", `wallet.json names ${SANDBOX_FIELD[k]} ${have}; the judge sandbox's is ${want}. Refusing to use it.`);
      }
    }
  }
  const net = { ...data.network, ...(network || {}) };
  net.sandbox = { ...data.network.sandbox, ...((network || {}).sandbox || {}) };
  const chain = defineChain({ id: net.chainId, name: net.name || `chain ${net.chainId}`,
    nativeCurrency: { name: "Ether", symbol: "ETH", decimals: 18 }, rpcUrls: { default: { http: [net.rpc] } } });
  const pub = createPublicClient({ chain, transport: http(net.rpc), pollingInterval: net.pollMs ?? 2000 });
  const A = data.abi;
  const ALL_ERRORS = [...A.MethaneCovenant, ...A.KeyRegistry, ...A.IncentivePool].filter((e) => e.type === "error");
  const C = { registry: net.sandbox.keyRegistry, covenant: net.sandbox.methaneCovenant, pool: net.sandbox.incentivePool };
  const mem = new Map();
  const kv = store || { get: (k) => mem.get(k), set: (k, v) => mem.set(k, v) };
  const sessionKey = () => `methaneclause.wallet.${net.chainId}.${C.covenant}.${account}`;
  const session = () => { try { return JSON.parse(kv.get(sessionKey()) || "{}"); } catch { return {}; } };
  const saveSession = (s) => kv.set(sessionKey(), JSON.stringify(s));
  let account = null, seenBlock = 0n;

  const link = (hash) => (net.explorer ? `${net.explorer}/tx/${hash}` : hash);
  const addrLink = (a) => (net.explorer ? `${net.explorer}/address/${a}` : a);

  // ------------------------------------------------------------------ result plumbing
  async function run(action, fn) {
    const steps = [];
    const step = (id, label, extra = {}) => { const s = { id, label, status: "done", ...extra }; steps.push(s); return s; };
    try {
      const payload = await fn(step);
      return { ok: true, action, steps: plain(steps), ...plain(payload || {}) };
    } catch (e) {
      const code = e instanceof Refusal ? e.code : walletErrorCode(e);
      const message = e instanceof Refusal ? e.message : humanWalletError(e);
      steps.push({ id: "error", label: message, status: "failed" });
      return { ok: false, action, steps: plain(steps), error: { code, message }, ...plain(e.extra || {}) };
    }
  }
  function walletErrorCode(e) {
    const c = e?.code ?? e?.cause?.code;
    if (c === 4001) return "USER_REJECTED";
    if (c === 4100) return "UNAUTHORIZED";
    if (c === 4900 || c === 4901) return "DISCONNECTED";
    return "ERROR";
  }
  function humanWalletError(e) {
    const c = walletErrorCode(e);
    if (c === "USER_REJECTED") return "You declined the request in your wallet; nothing was sent.";
    return `The request failed: ${String(e?.shortMessage || e?.message || e).slice(0, 200)}`;
  }

  // ------------------------------------------------------------------ chain reads (never older than our own writes)
  // Public RPCs are load-balanced: a read right after a write can reach a node a block behind. Reads and simulations
  // run at the newest block, but never below the newest block one of our receipts was in.
  async function tip() {
    for (let i = 0; i < 30; i++) {
      const bn = await pub.getBlockNumber({ cacheTime: 0 });
      if (bn >= seenBlock) return bn;
      await sleep(1000);
    }
    return seenBlock;
  }
  async function read(address, abi, functionName, args = []) {
    for (let i = 0; ; i++) {
      try {
        return await pub.readContract({ address, abi, functionName, args, blockNumber: await tip() });
      } catch (e) {
        if (i < 20 && LAG.some((m) => String(e?.message).toLowerCase().includes(m))) { await sleep(1000); continue; }
        throw e;
      }
    }
  }
  const now = async () => (await pub.getBlock({ blockTag: "latest" })).timestamp;
  async function fees() {
    const base = (await pub.getBlock({ blockTag: "latest" })).baseFeePerGas ?? 0n;
    return { maxFeePerGas: 2n * base + PRIORITY_FEE_WEI, maxPriorityFeePerGas: PRIORITY_FEE_WEI };
  }
  function revertData(err) {
    for (let e = err, i = 0; e && i < 10; e = e.cause, i++) {
      for (const d of [e.data, e.data?.data, e.error?.data]) if (typeof d === "string" && /^0x[0-9a-fA-F]{8,}$/.test(d)) return d;
    }
    const m = String(err?.details || err?.message || "").match(/0x[0-9a-fA-F]{8,}/);
    return m ? m[0] : null;
  }
  /** Revert reason by name from the contracts' ABIs (core, registry, pool and its linked libraries). */
  function decodeRevert(dataHex) {
    if (!dataHex) return { name: "Reverted", args: [] };
    try { const r = decodeErrorResult({ abi: ALL_ERRORS, data: dataHex }); return { name: r.errorName, args: plain(r.args || []) }; }
    catch { return { name: dataHex.slice(0, 10), args: [] }; }
  }
  /** eth_call the transaction; null if it would succeed, else the decoded revert. */
  async function simulate({ from, to, data, value = 0n }, blockNumber, stateOverride) {
    try {
      await pub.call({ account: from, to, data, value, blockNumber: blockNumber ?? (await tip()),
        ...(stateOverride ? { stateOverride } : {}) });
      return null;
    } catch (e) {
      const d = revertData(e);
      if (!d && !/revert/i.test(String(e?.message))) throw e;  // an RPC failure, not a revert
      return decodeRevert(d);
    }
  }
  async function receipt(hash) {
    const r = await pub.waitForTransactionReceipt({ hash, timeout: 300_000 });
    if (r.blockNumber > seenBlock) seenBlock = r.blockNumber;
    return r;
  }
  const txOf = (r, label) => ({ hash: r.transactionHash, link: link(r.transactionHash), status: r.status === "success" ? 1 : 0,
    gasUsed: r.gasUsed, block: r.blockNumber, label });

  // ------------------------------------------------------------------ wallet access
  async function ensureReady() {
    if (!ethereum) throw new Refusal("NO_WALLET", "No browser wallet found. Install MetaMask (or another EIP-1193 wallet), or use the terminal commands instead.");
    const accts = await ethereum.request({ method: "eth_accounts" });
    if (!accts || !accts.length) throw new Refusal("NOT_CONNECTED", "Connect your wallet first.");
    account = getAddress(accts[0]);
    const id = Number(BigInt(await ethereum.request({ method: "eth_chainId" })));
    if (id !== net.chainId) throw new Refusal("WRONG_CHAIN", `Your wallet is on chain ${id}; this page works on ${net.name} (${net.chainId}) only. Nothing was sent.`);
    return account;
  }
  /** Send from the judge's wallet (eth_sendTransaction; the wallet asks the judge to confirm). */
  async function walletSend({ to, data = "0x", value = 0n, gas }) {
    const tx = { from: account, to, data, value: "0x" + BigInt(value).toString(16) };
    if (gas) tx.gas = "0x" + BigInt(gas).toString(16);
    const hash = await ethereum.request({ method: "eth_sendTransaction", params: [tx] });
    return receipt(hash);
  }
  /** Sign and send from a published sandbox key (the page holds it only because it is public). */
  async function publishedSend(pk, { to, data, value = 0n }) {
    const acct = privateKeyToAccount(pk);
    const f = await fees();
    let gas;
    try { gas = (await pub.estimateGas({ account: acct.address, to, data, value })) * 2n; } catch { gas = GAS_BUDGET; }
    const nonce = await pub.getTransactionCount({ address: acct.address, blockTag: "pending" });
    const signed = await acct.signTransaction({ chainId: net.chainId, type: "eip1559", to, data, value, gas, nonce, ...f });
    const hash = await pub.sendRawTransaction({ serializedTransaction: signed });
    return receipt(hash);
  }
  /** Top up a published key's gas money from the judge's wallet, only by what the next transaction needs. */
  async function fundGas(step, addr, call, extraWei = 0n, who = "the published key") {
    const f = await fees();
    let gas;
    try { gas = (await pub.estimateGas({ account: addr, ...call })) * 2n; } catch { gas = GAS_BUDGET; }
    const need = gas * f.maxFeePerGas + extraWei, have = await pub.getBalance({ address: addr });
    if (have >= need) { step("gas", `${who} already holds enough gas money`, { status: "info", sim: true }); return; }
    const r = await walletSend({ to: addr, value: need - have });
    if (r.status !== "success") throw new Refusal("TRANSFER_FAILED", "Your transfer to the published key failed.", { tx: txOf(r) });
    step("gas", `You sent ${formatEth(need - have)} to ${who} (${short(addr)}), so it can pay for its transaction`,
      { sim: true, tx: txOf(r, "gas money") });
  }

  // ------------------------------------------------------------------ published sandbox keys
  const PREFIX = data.sandboxKeys.prefix;
  function sandboxAccount(role, generation = 1) {
    const pk = publishedKey(PREFIX, role, generation);
    const acct = privateKeyToAccount(pk);
    const want = data.sandboxKeys.addresses[role];
    if (generation === 1 && want && getAddress(want) !== acct.address) {
      throw new Refusal("KEY_MISMATCH", `The published ${role} key derives to ${acct.address}, the deployment record copied into wallet.json says ${want}; refusing to use it.`);
    }
    return { pk, address: acct.address, account: acct };
  }
  /** First generation of a published registered key that the registry reports valid now (sandbox.py current). */
  async function currentGeneration(role) {
    const want = data.sandboxKeys.registryRole[role === "SELLER" ? "SELLER" : role === "BUYER" ? "BUYER" : "OBSERVER"];
    for (let g = 1; g <= data.sandboxKeys.maxGeneration; g++) {
      const a = sandboxAccount(role, g).address;
      const [, r, valid] = await read(C.registry, A.KeyRegistry, "keyStatus", [a, 2n ** 63n]);
      if (Number(r) === 0) break;
      if (valid && Number(r) === want) return g;
    }
    throw new Refusal("KEY_REVOKED", `Every published ${role} key has been revoked by someone (anyone holding a published key can). The operator registers the next generation with \`make sandbox-rekey ROLE=${role}\`.`);
  }

  // ------------------------------------------------------------------ evidence
  const detectionCase = (b) => Object.values(data.detections).find((d) => d.facility.sourceName === b.sourceName && d.plumeId === b.plumeId);
  const cleanCase = (b) => Object.values(data.cleanPasses).find((c) => c.bundleTemplate.sceneProviderId === b.sceneProviderId);
  const randomNonce = () => { const a = new Uint8Array(8); crypto.getRandomValues(a); return BigInt(hex(a)); };

  async function covenantRead(cid) {
    const c = await read(C.covenant, A.MethaneCovenant, "getCovenant", [b32(cid)]);
    if (BigInt(c.seller) === 0n) throw new Refusal("NO_COVENANT", `Covenant ${cid} does not exist in the sandbox on ${net.name}.`);
    return c;
  }
  async function episodeRead(eid) {
    const e = await read(C.covenant, A.MethaneCovenant, "getEpisode", [BigInt(eid)]);
    return { ...e, id: BigInt(eid), stateName: EPISODE_STATES[Number(e.state)] };
  }
  const episodeView = (e, t) => ({ id: e.id, state: e.stateName, sourceId: e.sourceId, evidenceHash: e.evidenceHash,
    emissionKgH: Number(e.emissionKgH), tier: Number(e.tier), rebateWei: e.rebate, detectedAt: Number(e.detectedAt),
    deadline: Number(e.deadline), secondsLeft: Math.max(0, Number(e.deadline) - Number(t)), proposer: e.proposer,
    parentEpisode: e.parentEpisode });

  async function signWith(kind, typed, signer) {
    const digest = hashTypedData(typed);
    let signature;
    if (signer === "wallet") {
      signature = normalizeSignature(await ethereum.request({ method: "eth_signTypedData_v4", params: [account, typedDataJson(typed)] }));
    } else {
      signature = normalizeSignature(await signer.account.signTypedData(typed));
    }
    const recovered = await recoverTypedDataAddress({ ...typed, signature });
    const expect = signer === "wallet" ? account : signer.address;
    if (getAddress(recovered) !== getAddress(expect)) {
      throw new Refusal("SIGNATURE_MISMATCH", `The ${kind} signature recovers to ${recovered}, not ${expect}; it was not used.`);
    }
    return { digest, signature, signer: getAddress(expect) };
  }

  async function detectionBundle(input) {
    const s = session();
    if (input?.bundle) return { bundle: input.bundle, det: detectionCase(input.bundle) };
    const det = data.detections[input?.case || "TKM_S_276"];
    if (!det) throw new Refusal("UNKNOWN_CASE", `No observer bundle for ${input?.case}; available: ${Object.keys(data.detections).join(", ")}.`);
    const cid = input?.covenantId || s.covenantId;
    if (!cid) throw new Refusal("NO_COVENANT", "Open your sandbox covenant first (openCovenant).");
    return { bundle: { ...det.bundleTemplate, covenantId: cid }, det };
  }

  async function signDetection(input, signer, who, sim) {
    return run(sim ? "cosignAttestation" : "signAttestation", async (step) => {
      if (signer === "wallet") await ensureReady();
      const { bundle, det } = await detectionBundle(input);
      if (!det) throw new Refusal("NOT_FROM_OBSERVER", "This bundle is not one the observer pipeline built; nothing was signed.");
      step("fetch", `Evidence: Carbon Mapper plume ${det.plumeId} at ${det.facility.sourceName}, from the observer's committed snapshot (content hash ${short("0x" + det.receipt.contentSha256)}); your browser cannot fetch Carbon Mapper itself (no CORS)`);
      const c = await covenantRead(bundle.covenantId);
      const t = await now();
      const refusals = validateDetection(bundle, det.bundleTemplate, det.facility,
        { covenantStart: Number(c.start), now: Number(t), minRateKgH: Number(c.minRateKgH) });
      if (refusals.length) throw new Refusal("VALIDATION_REFUSED", `Validation refused it (${refusals.map((r) => r.code).join(", ")}): ${refusals[0].message}. Nothing was signed.`, { refusals });
      step("validate", `Checked against your covenant on chain: ${bundle.emissionKgH} kg/h is above its ${c.minRateKgH} kg/h minimum, detected ${utc(bundle.detectedAt)} after its start, already published, within ${RADIUS_KM} km of the facility`);
      const canonical = jcs(bundle), evidenceHash = await sha256Hex(canonical);
      step("seal", `Bundle sealed: ${enc.encode(canonical).length} bytes of RFC 8785 JSON, SHA-256 = evidenceHash ${short(evidenceHash)}`);
      const att = attestationFor(bundle, evidenceHash, { nonce: input?.nonce ?? randomNonce(), expiry: input?.expiry ?? t + EXPIRY_SECONDS });
      const typed = attestationTypedData(att, net.chainId, C.covenant);
      const sig = await signWith("attestation", typed, signer);
      step("sign", signer === "wallet"
        ? `You signed the EIP-712 attestation in your wallet (digest ${short(sig.digest)}). Nothing is on chain yet; no money moved.`
        : `${who} signed the same bundle (digest ${short(sig.digest)}). Nothing is on chain yet; no money moved.`, { sim });
      step("recover", `Signature checked in this browser: it recovers to ${sig.signer}`);
      return { case: det.case, bundle, evidenceHash, attestation: att, typedData: typed, ...sig };
    });
  }

  async function cleanBundle(input) {
    const s = session();
    if (input?.bundle) return { bundle: input.bundle, cp: cleanCase(input.bundle) };
    const cp = data.cleanPasses[input?.case || "DZA_S_012"];
    if (!cp) throw new Refusal("UNKNOWN_CASE", `No clean-pass bundle for ${input?.case}.`);
    const cid = input?.covenantId || s.covenantId, claim = input?.claimId || s.claimId;
    if (!cid || !claim) throw new Refusal("NO_CLAIM", "Open a refund claim first (openRefundClaim).");
    return { bundle: { ...cp.bundleTemplate, covenantId: cid, claimId: claim }, cp };
  }

  async function signClean(input, signer, who, sim) {
    return run(sim ? "cosignCleanPass" : "signCleanPass", async (step) => {
      if (signer === "wallet") await ensureReady();
      const { bundle, cp } = await cleanBundle(input);
      if (!cp) throw new Refusal("NOT_FROM_OBSERVER", "This scene is not one the observer pipeline built; nothing was signed.");
      step("fetch", `Scene ${cp.sceneName} (${bundle.instrument}, ${utc(bundle.sceneAt)}) at ${cp.case}, from the observer's committed case file; your browser cannot fetch Carbon Mapper itself (no CORS)`);
      const diff = Object.keys(cp.bundleTemplate).filter((k) => !["covenantId", "claimId"].includes(k) && JSON.stringify(bundle[k]) !== JSON.stringify(cp.bundleTemplate[k]));
      const keysOk = Object.keys(bundle).sort().join(",") === Object.keys(cp.bundleTemplate).sort().join(",");
      if (diff.length || !keysOk) throw new Refusal("VALIDATION_REFUSED", `The scene differs from the one the observer validated (${diff.join(", ") || "fields"}). Nothing was signed.`);
      if (bundle.sceneId !== keccakText(bundle.sceneProviderId)) throw new Refusal("VALIDATION_REFUSED", "sceneId != keccak256(sceneProviderId). Nothing was signed.");
      const claim = await read(C.pool, A.IncentivePool, "getClaim", [b32(bundle.claimId)]);
      if (Number(claim.state) !== 1 || claim.covenantId.toLowerCase() !== bundle.covenantId.toLowerCase()) throw new Refusal("NO_CLAIM", `Claim ${short(bundle.claimId)} is not an open claim of covenant ${short(bundle.covenantId)}.`);
      if (bundle.sceneAt <= Number(claim.since)) throw new Refusal("VALIDATION_REFUSED", `The scene (${utc(bundle.sceneAt)}) is before the claim's clean run starts (${utc(claim.since)}). Nothing was signed.`);
      step("validate", `The observer pipeline checked this scene against rules 1-6 (cloud, coverage, origin, no plume, MARS, floor) when the page data was built; this is that scene, after the claim's clean run starts (${utc(claim.since)})`);
      const canonical = jcs(bundle), sceneHash = await sha256Hex(canonical);
      step("seal", `Scene sealed: ${enc.encode(canonical).length} bytes of RFC 8785 JSON, SHA-256 = sceneHash ${short(sceneHash)}`);
      const t = await now();
      const msg = cleanPassFor(bundle, sceneHash, { nonce: input?.nonce ?? randomNonce(), expiry: input?.expiry ?? t + EXPIRY_SECONDS });
      const typed = cleanPassTypedData(msg, net.chainId, C.pool);
      const sig = await signWith("clean-pass", typed, signer);
      step("sign", signer === "wallet"
        ? `You signed the EIP-712 clean pass in your wallet (digest ${short(sig.digest)}). A clean pass is not a clean site; nothing is on chain yet.`
        : `${who} signed the same scene (digest ${short(sig.digest)}). Nothing is on chain yet.`, { sim });
      step("recover", `Signature checked in this browser: it recovers to ${sig.signer}`);
      return { case: cp.case, bundle, sceneHash, message: msg, typedData: typed, ...sig };
    });
  }

  // ------------------------------------------------------------------ public API
  return {
    get account() { return account; },
    get network() { return { chainId: net.chainId, name: net.name, explorer: net.explorer, contracts: { ...C } }; },
    session,

    /** Ask the wallet for an account and make sure it is on the sandbox's network (offers wallet_switchEthereumChain). */
    connect: ({ switchChain = true } = {}) => run("connect", async (step) => {
      if (!ethereum) throw new Refusal("NO_WALLET", "No browser wallet found. Install MetaMask (or another EIP-1193 wallet), or use the terminal commands instead.");
      const accts = await ethereum.request({ method: "eth_requestAccounts" });
      if (!accts?.length) throw new Refusal("NOT_CONNECTED", "The wallet shared no account.");
      account = getAddress(accts[0]);
      step("account", `Your wallet shared its address ${account}; the page never sees its private key`);
      let id = Number(BigInt(await ethereum.request({ method: "eth_chainId" })));
      if (id !== net.chainId) {
        if (!switchChain) throw new Refusal("WRONG_CHAIN", `Your wallet is on chain ${id}; switch it to ${net.name} (${net.chainId}).`, { account, chainId: id });
        const want = "0x" + net.chainId.toString(16);
        try {
          await ethereum.request({ method: "wallet_switchEthereumChain", params: [{ chainId: want }] });
        } catch (e) {
          if ((e?.code ?? e?.data?.originalError?.code) !== 4902) throw e;
          await ethereum.request({ method: "wallet_addEthereumChain", params: [{ chainId: want, chainName: net.name,
            nativeCurrency: { name: "Sepolia Ether", symbol: "ETH", decimals: 18 }, rpcUrls: [net.rpc],
            blockExplorerUrls: net.explorer ? [net.explorer] : [] }] });
        }
        id = Number(BigInt(await ethereum.request({ method: "eth_chainId" })));
        if (id !== net.chainId) throw new Refusal("WRONG_CHAIN", `The wallet is still on chain ${id}; this page works on ${net.name} only.`, { account, chainId: id });
        step("switch", `Your wallet switched to ${net.name}`);
      }
      const bal = await pub.getBalance({ address: account });
      step("network", `Network: ${net.name} (chain ${net.chainId}); your balance ${formatEth(bal)} (test ETH, no monetary value)`);
      return { account, chainId: id, balanceWei: bal, session: session() };
    }),

    /**
     * Register the judge's wallet as an OBSERVER key of an observer org in the judge sandbox: OBS_JUDGE (the path
     * `make judge-attest` uses) or OBS_INDEPENDENT (the open observer slot). registerKey must come from the org's
     * controller; both controllers are published sandbox keys, so the page signs that one call with it and the
     * judge's wallet pays its gas. Same registry state as the terminal path.
     */
    joinAsObserver: ({ org = "OBS_JUDGE" } = {}) => run("joinAsObserver", async (step) => {
      await ensureReady();
      const role = { OBS_JUDGE: "JUDGE_CONTROLLER", OBS_INDEPENDENT: "INDEPENDENT_CONTROLLER" }[org];
      if (!role) throw new Refusal("UNKNOWN_ORG", "org must be OBS_JUDGE or OBS_INDEPENDENT");
      const [o, r, valid] = await read(C.registry, A.KeyRegistry, "keyStatus", [account, await now()]);
      if (Number(r) === 1 && valid && o === b32(org)) {
        step("check", `Your wallet is already a registered observer key of org ${org}; nothing to do`);
        return { org, account, alreadyRegistered: true };
      }
      if (Number(r) !== 0) {
        throw new Refusal("ALREADY_IN_REGISTRY", `Your address is already in this registry (role ${ROLE_NAMES[Number(r)]}, ${valid ? "valid" : "no longer valid"}); a key is registered once and never reused. Use a fresh wallet account.`);
      }
      step("check", `The sandbox registry does not know your address yet`, { status: "info" });
      const ctl = sandboxAccount(role);
      const onChainCtl = await read(C.registry, A.KeyRegistry, "orgController", [b32(org)]);
      if (getAddress(onChainCtl) !== ctl.address) throw new Refusal("KEY_MISMATCH", `The registry names ${onChainCtl} as ${org}'s controller, not the published key ${ctl.address}.`);
      step("controller", `The registry names the published key ${short(ctl.address)} as org ${org}'s controller (its private key is keccak256 of a public string, so anyone can use it)`, { sim: true });
      const call = { to: C.registry, data: encodeFunctionData({ abi: A.KeyRegistry, functionName: "registerKey", args: [b32(org), 1, account, 1, ZERO32] }) };
      const why = await simulate({ from: ctl.address, ...call });
      if (why) throw new Refusal("WOULD_REVERT", `The registry would refuse the registration (${why.name}); nothing was sent.`, { revert: why });
      step("simulate", "Simulated registerKey from the published controller: the registry accepts it", { sim: true });
      await fundGas(step, ctl.address, call, 0n, `the published ${org} controller`);
      const rc = await publishedSend(ctl.pk, call);
      if (rc.status !== "success") throw new Refusal("REVERTED", "The registration transaction reverted.", { tx: txOf(rc) });
      step("register", `The page signed registerKey with the published controller key and sent it: your address is now an OBSERVER key of org ${org}`, { sim: true, tx: txOf(rc, "registerKey") });
      const [o2, r2, v2] = await read(C.registry, A.KeyRegistry, "keyStatus", [account, await now()]);
      if (!(Number(r2) === 1 && v2 && o2 === b32(org))) throw new Refusal("NOT_REGISTERED", "The registry does not report your key as a valid observer after the transaction.");
      step("verify", `Read back from the chain: org ${org}, role OBSERVER, valid from block ${rc.blockNumber}. One signature in your episode's quorum will be yours.`);
      return { org, account, alreadyRegistered: false, tx: txOf(rc, "registerKey"), registrationLink: link(rc.transactionHash) };
    }),

    /**
     * Your own sandbox covenant: created by the published seller key (start backdated to 2026-01-01; the chain records
     * createdAt so the backdating is visible), the bond posted by your wallet. Same policy and hash rule as
     * `make judge-attest`.
     */
    openCovenant: () => run("openCovenant", async (step) => {
      await ensureReady();
      const s = session();
      if (s.covenantId) {
        const c = await covenantRead(s.covenantId);
        step("check", `Your sandbox covenant already exists (bond remaining ${formatEth(c.remaining)}); reusing it`);
        return { covenantId: s.covenantId, existing: true };
      }
      const cov = data.covenant, root = data.detections["TKM_S_276"].facility.root;
      const policy = { ...cov.policy, bondWei: cov.bondWei, judge: account, sourceSetRoot: root };
      const policyHash = await canonicalHash(policy);
      step("policy", `Covenant policy hashed (RFC 8785 + SHA-256): ${short(policyHash)}; it names you as the judge and fixes the rule, windows and rebate tiers`);
      const gen = await currentGeneration("SELLER"), seller = sandboxAccount("SELLER", gen);
      const bgen = await currentGeneration("BUYER"), buyer = sandboxAccount("BUYER", bgen);
      const p = cov.params;
      const params = { buyer: buyer.address, sourceSetRoot: b32(root), policyHash: b32(policyHash), term: BigInt(p.term),
        start: BigInt(p.start), k: p.k, challengeWindow: BigInt(p.challengeWindow), contestWindow: BigInt(p.contestWindow),
        linkWindow: BigInt(p.linkWindow), minRateKgH: p.minRateKgH, tierAmounts: p.tierAmounts.map(BigInt) };
      const call = { to: C.covenant, data: encodeFunctionData({ abi: A.MethaneCovenant, functionName: "createCovenant", args: [params] }) };
      const why = await simulate({ from: seller.address, ...call });
      if (why) throw new Refusal("WOULD_REVERT", `createCovenant would revert (${why.name}); nothing was sent.`, { revert: why });
      await fundGas(step, seller.address, call, 0n, "the published seller key");
      const rc = await publishedSend(seller.pk, call);
      if (rc.status !== "success") throw new Refusal("REVERTED", "createCovenant reverted.", { tx: txOf(rc) });
      const ev = rc.logs.map((l) => { try { return decodeEventLog({ abi: A.MethaneCovenant, ...l }); } catch { return null; } })
        .find((x) => x?.eventName === "CovenantCreated");
      const covenantId = ev.args.covenantId;
      saveSession({ covenantId, episodes: [] });
      const c0 = await covenantRead(covenantId);
      step("create", `The seller (published key) created covenant ${BigInt(covenantId)}: start ${utc(c0.start)}, backdated before its creation at ${utc(c0.createdAt)} — the chain shows the evidence was public before the covenant existed`, { sim: true, tx: txOf(rc, "createCovenant") });
      const fundData = encodeFunctionData({ abi: A.MethaneCovenant, functionName: "fund", args: [covenantId] });
      const why2 = await simulate({ from: account, to: C.covenant, data: fundData, value: BigInt(cov.bondWei) });
      if (why2) throw new Refusal("WOULD_REVERT", `fund would revert (${why2.name}); nothing was sent.`, { revert: why2, covenantId });
      const rf = await walletSend({ to: C.covenant, data: fundData, value: BigInt(cov.bondWei) });
      if (rf.status !== "success") throw new Refusal("REVERTED", "fund reverted.", { tx: txOf(rf), covenantId });
      const c = await covenantRead(covenantId);
      step("fund", `You posted the seller's bond: ${formatEth(c.remaining)} held by the contract (the published seller key never holds the bond)`, { sim: true, tx: txOf(rf, "fund") });
      return { covenantId, policyHash, buyer: buyer.address, seller: seller.address, bondWei: c.remaining,
        txs: [txOf(rc, "createCovenant"), txOf(rf, "fund")] };
    }),

    /** You sign the detection attestation (eth_signTypedData_v4). Input: { case } (default TKM_S_276) or { bundle }. */
    signAttestation: (input = {}) => signDetection(input, "wallet", "You", false),

    /** Published sandbox observer 1 co-signs the same bundle (the second org in the k = 2 quorum). */
    cosignAttestation: async (input = {}) => {
      try {
        const g = await currentGeneration("OBS_1");
        return signDetection(input, sandboxAccount("OBS_1", g), "Published sandbox observer 1", true);
      } catch (e) {
        return { ok: false, action: "cosignAttestation", steps: [{ id: "error", label: e.message, status: "failed" }], error: { code: e.code || "ERROR", message: e.message } };
      }
    },

    /**
     * Propose the episode from your wallet: { bundle, signatures: [signed, ...], expectRevert? }. Each attestation is
     * rebuilt from the bundle as submitted (evidenceHash, rate, time), so an edited bundle no longer matches the
     * signatures and the chain refuses it. Without expectRevert a refused call is not sent (the reason is shown); with
     * expectRevert: "InvalidSignature" it is sent anyway with a fixed gas limit, so the explorer shows the status-0
     * transaction, and the bond and episode count are read before and after.
     */
    submitEpisode: ({ bundle, signatures, expectRevert } = {}) => run("submitEpisode", async (step) => {
      await ensureReady();
      if (!bundle || !signatures?.length) throw new Refusal("BAD_INPUT", "Pass the bundle and the signed attestations.");
      const det = detectionCase(bundle);
      if (!det) throw new Refusal("NOT_FROM_OBSERVER", "No facility proof for this bundle's facility and plume.");
      const evidenceHash = await canonicalHash(bundle);
      const atts = signatures.map((s) => attestationFor(bundle, evidenceHash, { nonce: s.attestation.nonce, expiry: s.attestation.expiry, kind: s.attestation.kind }));
      const value = await read(C.covenant, A.MethaneCovenant, "proposerBond");
      const args = [b32(bundle.covenantId), b32(det.facility.sourceId), det.facility.proof.map(b32), b32(evidenceHash),
        atts.map((a) => asUints(a, ATTESTATION_TYPES.Attestation)), signatures.map((s) => s.signature)];
      const data_ = encodeFunctionData({ abi: A.MethaneCovenant, functionName: "proposeEpisode", args });
      step("seal", `Recomputed from the bundle you submit: evidenceHash ${short(evidenceHash)}${evidenceHash !== signatures[0].evidenceHash ? " (differs from the one signed)" : " (the one signed)"}`);
      const why = await simulate({ from: account, to: C.covenant, data: data_, value });
      if (why && why.name !== expectRevert) {
        throw new Refusal("WOULD_REVERT", `The chain would refuse it (${why.name}); nothing was sent, no gas spent.`, { revert: why });
      }
      if (!why && expectRevert) throw new Refusal("EXPECTED_REVERT_MISSING", `Expected ${expectRevert}, but the call succeeds in simulation; not sending it.`);
      const cid = b32(bundle.covenantId);
      const before = { remaining: (await covenantRead(cid)).remaining, episodes: await read(C.covenant, A.MethaneCovenant, "episodeCount") };
      if (why) {
        step("simulate", `Simulated: the contract refuses it (${why.name}); sending it anyway so the refusal is on chain`, { status: "info" });
        const rc = await walletSend({ to: C.covenant, data: data_, value, gas: REVERT_GAS });
        const reason = rc.status === "success" ? null : rc.gasUsed >= REVERT_GAS ? { name: "OutOfGas", args: [] }
          : await simulate({ from: account, to: C.covenant, data: data_, value }, rc.blockNumber - 1n);
        const after = { remaining: (await covenantRead(cid)).remaining, episodes: await read(C.covenant, A.MethaneCovenant, "episodeCount") };
        const unchanged = after.remaining === before.remaining && after.episodes === before.episodes;
        if (rc.status === "success" || reason?.name !== expectRevert) throw new Refusal("UNEXPECTED", `Expected a status-0 ${expectRevert}, got status ${rc.status} ${reason?.name}.`, { tx: txOf(rc) });
        step("refused", `The chain refused it (receipt status 0): ${reason.name}${reason.name === "InvalidSignature" ? " — the signatures no longer match the edited bundle" : ""}`, { tx: txOf(rc, "proposeEpisode (refused)") });
        if (!unchanged) throw new Refusal("STATE_MOVED", "State moved after a reverted transaction.");
        step("unchanged", `Bond unchanged (${formatEth(after.remaining)}), no episode created; you paid only the gas used up to the revert`);
        return { status: 0, revert: reason, tx: txOf(rc, "proposeEpisode"), stateUnchanged: true };
      }
      step("simulate", "Simulated: the contract accepts both signatures (distinct observer orgs, valid keys, fresh nonces)");
      const rc = await walletSend({ to: C.covenant, data: data_, value });
      if (rc.status !== "success") throw new Refusal("REVERTED", "proposeEpisode reverted.", { tx: txOf(rc) });
      const ev = rc.logs.map((l) => { try { return decodeEventLog({ abi: A.MethaneCovenant, ...l }); } catch { return null; } })
        .find((x) => x?.eventName === "EpisodeProposed" || x?.eventName === "DetectionLinked");
      const e = await episodeRead(ev.args.episodeId);
      const s = session(); s.episodes = [...new Set([...(s.episodes || []), e.id.toString()])]; saveSession(s);
      const t = await now();
      if (e.stateName === "LINKED") {
        step("linked", `Recorded as LINKED to episode ${e.parentEpisode}: a detection at this facility was already proposed on your covenant (the sandbox keys are public). Recorded, not paid; your proposer bond is credited back.`, { status: "info", tx: txOf(rc, "proposeEpisode") });
      } else {
        step("proposed", `The chain checked both signatures and opened episode ${e.id} (${e.stateName}, tier ${e.tier}). The rebate (${formatEth(e.rebate)}) is held from the seller's bond; nothing is paid until the challenge window closes at ${utc(e.deadline)}`, { tx: txOf(rc, "proposeEpisode") });
      }
      return { status: 1, episodeId: e.id, episode: episodeView(e, t), tx: txOf(rc, "proposeEpisode"), proposerBondWei: value,
        bondBeforeWei: before.remaining, bondAfterWei: (await covenantRead(cid)).remaining };
    }),

    /** After the challenge window: anyone confirms the episode; the rebate is credited to the buyer. */
    finalizeEpisode: (episodeId) => run("finalizeEpisode", async (step) => {
      await ensureReady();
      const e = await episodeRead(episodeId), t = await now();
      if (e.stateName !== "PROPOSED") {
        if (e.stateName === "CONFIRMED") { step("check", `Episode ${episodeId} is already CONFIRMED`); return { episode: episodeView(e, t), alreadyFinal: true }; }
        throw new Refusal("NOT_PROPOSED", `Episode ${episodeId} is ${e.stateName}; only a PROPOSED episode can be finalized.`, { episode: episodeView(e, t) });
      }
      if (t <= e.deadline) throw new Refusal("WINDOW_OPEN", `The challenge window is open until ${utc(e.deadline)} (${Number(e.deadline - t)} s from now); finalize after that. Nothing was sent.`, { secondsLeft: Number(e.deadline - t) + 1, deadline: Number(e.deadline) });
      const d = encodeFunctionData({ abi: A.MethaneCovenant, functionName: "finalize", args: [BigInt(episodeId)] });
      const why = await simulate({ from: account, to: C.covenant, data: d });
      if (why) throw new Refusal("WOULD_REVERT", `finalize would revert (${why.name}); nothing was sent.`, { revert: why });
      const rc = await walletSend({ to: C.covenant, data: d });
      const e2 = await episodeRead(episodeId);
      if (rc.status !== "success" || e2.stateName !== "CONFIRMED") throw new Refusal("REVERTED", "finalize failed.", { tx: txOf(rc) });
      const c = await covenantRead(e2.covenantId);
      const buyerCredit = await read(C.covenant, A.MethaneCovenant, "withdrawable", [c.buyer]);
      step("confirmed", `Episode ${episodeId} CONFIRMED: the buyer is credited ${formatEth(e2.rebate)} from the seller's bond (buyer's withdrawable ${formatEth(buyerCredit)}); your proposer bond is back in your withdrawable balance`, { tx: txOf(rc, "finalize") });
      return { episode: episodeView(e2, await now()), tx: txOf(rc, "finalize"), buyerWithdrawableWei: buyerCredit };
    }),

    /**
     * The refund pool for your covenant, then a refund claim on a CONFIRMED episode. The pool's first funding must come
     * from a covenant party: the published buyer key opens it with the episode's rebate + one observer fee, which your
     * wallet sends it first (judge-cure does the same).
     */
    openRefundClaim: (episodeId) => run("openRefundClaim", async (step) => {
      await ensureReady();
      const cp = data.cleanPasses["DZA_S_012"].pool;
      const e = await episodeRead(episodeId);
      if (e.stateName !== "CONFIRMED") throw new Refusal("NOT_CONFIRMED", `Episode ${episodeId} is ${e.stateName}; a refund claim opens only on a CONFIRMED episode.`);
      const cid = e.covenantId;
      const [pl] = await read(C.pool, A.IncentivePool, "getPool", [cid]);
      if (Number(pl.state) === 0) {
        const value = e.rebate + BigInt(cp.observerFeeWei);
        const g = await currentGeneration("BUYER"), buyer = sandboxAccount("BUYER", g);
        const p = cp.params;
        const params = { publicationLag: p.publicationLag, cleanWindow: p.cleanWindow, resolveWindow: p.resolveWindow,
          holdWindow: p.holdWindow, cureTerm: p.cureTerm, passBond: BigInt(p.passBond), challengerBond: BigInt(p.challengerBond),
          observerFeeWei: BigInt(p.observerFeeWei), unitTierAmounts: p.unitTierAmounts.map(BigInt),
          installments: p.installments, upperBoundBp: p.upperBoundBp };
        const call = { to: C.pool, value, data: encodeFunctionData({ abi: A.IncentivePool, functionName: "fund", args: [cid, b32(cp.policyHash), MODE_ONCHAIN, params] }) };
        // simulated as if the published buyer key already held the value (it holds none until your transfer)
        const why = await simulate({ from: buyer.address, ...call }, undefined, [{ address: buyer.address, balance: value * 2n }]);
        if (why) throw new Refusal("WOULD_REVERT", `The pool would refuse its opening (${why.name}); nothing was sent.`, { revert: why });
        await fundGas(step, buyer.address, { to: call.to, data: call.data }, value, "the published buyer key");
        if ((await pub.getBalance({ address: buyer.address })) < value) {
          throw new Refusal("SWEPT", `The test ETH you just sent to the published buyer key ${buyer.address} was taken before it could open the pool (anyone holding a published sandbox key can move it). You lost only that amount; run openRefundClaim again.`);
        }
        const rc = await publishedSend(buyer.pk, call);
        if (rc.status !== "success") throw new Refusal("REVERTED", "The pool opening reverted.", { tx: txOf(rc) });
        step("pool", `The buyer (published key) opened your covenant's refund pool with the episode's rebate + one observer fee (${formatEth(value)}), under the sandbox pool policy ${short(cp.policyHash)}`, { sim: true, tx: txOf(rc, "fund pool") });
      } else {
        step("pool", `Your covenant's refund pool is already ${POOL_STATES[Number(pl.state)]}`, { status: "info" });
      }
      const d = encodeFunctionData({ abi: A.IncentivePool, functionName: "openClaim", args: [BigInt(episodeId)] });
      const why = await simulate({ from: account, to: C.pool, data: d });
      if (why) throw new Refusal("WOULD_REVERT", `openClaim would revert (${why.name}); nothing was sent.`, { revert: why });
      const rc = await walletSend({ to: C.pool, data: d });
      if (rc.status !== "success") throw new Refusal("REVERTED", "openClaim reverted.", { tx: txOf(rc) });
      const ev = rc.logs.map((l) => { try { return decodeEventLog({ abi: A.IncentivePool, ...l }); } catch { return null; } }).find((x) => x?.eventName === "ClaimOpened");
      const claimId = ev.args.claimId;
      const claim = await read(C.pool, A.IncentivePool, "getClaim", [claimId]);
      const s = session(); s.claimId = claimId; s.covenantId = s.covenantId || cid; saveSession(s);
      step("claim", `Refund claim opened on episode ${episodeId}: the clean run counts after ${utc(claim.since)} (detection + link window); capped at ${formatEth(claim.cap)}, paid only in installments, cancelled by the next attested detection`, { tx: txOf(rc, "openClaim") });
      return { claimId, since: Number(claim.since), capWei: claim.cap, tx: txOf(rc, "openClaim") };
    }),

    /** You sign a qualified clean pass (EIP-712 CleanPass, pool domain). Input: { case } (default DZA_S_012) or { bundle }. */
    signCleanPass: (input = {}) => signClean(input, "wallet", "You", false),

    /** Published sandbox observer 1 co-signs the same scene. */
    cosignCleanPass: async (input = {}) => {
      try {
        const g = await currentGeneration("OBS_1");
        return signClean(input, sandboxAccount("OBS_1", g), "Published sandbox observer 1", true);
      } catch (e) {
        return { ok: false, action: "cosignCleanPass", steps: [{ id: "error", label: e.message, status: "failed" }], error: { code: e.code || "ERROR", message: e.message } };
      }
    },

    /**
     * Submit the clean pass from your wallet with the pass bond: { bundle, signatures, expectRevert? }. The sceneHash is
     * recomputed from the bundle as submitted (an edited cloud field changes it, and both signatures fail).
     */
    submitCleanPass: ({ bundle, signatures, expectRevert } = {}) => run("submitCleanPass", async (step) => {
      await ensureReady();
      if (!bundle || !signatures?.length) throw new Refusal("BAD_INPUT", "Pass the scene bundle and the signed clean passes.");
      const cp = cleanCase(bundle);
      if (!cp) throw new Refusal("NOT_FROM_OBSERVER", "Unknown scene.");
      const sceneHash = await canonicalHash(bundle);
      const msgs = signatures.map((s) => cleanPassFor(bundle, sceneHash, { nonce: s.message.nonce, expiry: s.message.expiry }));
      step("seal", `Recomputed from the scene you submit: sceneHash ${short(sceneHash)}${sceneHash !== signatures[0].sceneHash ? " (differs from the one signed)" : " (the one signed)"}`);
      const value = BigInt(cp.pool.passBondWei);
      const d = encodeFunctionData({ abi: A.IncentivePool, functionName: "attestCleanPass",
        args: [b32(bundle.claimId), msgs.map((m) => asUints(m, CLEANPASS_TYPES.CleanPass)), signatures.map((s) => s.signature)] });
      const why = await simulate({ from: account, to: C.pool, data: d, value });
      if (why && why.name !== expectRevert) throw new Refusal("WOULD_REVERT", `The pool would refuse it (${why.name}); nothing was sent, no gas spent.`, { revert: why });
      if (!why && expectRevert) throw new Refusal("EXPECTED_REVERT_MISSING", `Expected ${expectRevert}, but the call succeeds in simulation; not sending it.`);
      if (why) {
        const before = await read(C.pool, A.IncentivePool, "getClaim", [b32(bundle.claimId)]);
        const rc = await walletSend({ to: C.pool, data: d, value, gas: REVERT_GAS });
        const reason = rc.status === "success" ? null : rc.gasUsed >= REVERT_GAS ? { name: "OutOfGas", args: [] }
          : await simulate({ from: account, to: C.pool, data: d, value }, rc.blockNumber - 1n);
        const after = await read(C.pool, A.IncentivePool, "getClaim", [b32(bundle.claimId)]);
        if (rc.status === "success" || reason?.name !== expectRevert) throw new Refusal("UNEXPECTED", `Expected a status-0 ${expectRevert}, got status ${rc.status} ${reason?.name}.`, { tx: txOf(rc) });
        step("refused", `The pool refused it (receipt status 0): ${reason.name}`, { tx: txOf(rc, "attestCleanPass (refused)") });
        const unchanged = after.pending === before.pending && after.counted === before.counted;
        if (!unchanged) throw new Refusal("STATE_MOVED", "Claim state moved after a reverted transaction.");
        step("unchanged", "Claim unchanged: no pass recorded, no refund moved");
        return { status: 0, revert: reason, tx: txOf(rc, "attestCleanPass"), stateUnchanged: true };
      }
      step("simulate", "Simulated: the pool accepts both signatures and the scene's fields");
      const rc = await walletSend({ to: C.pool, data: d, value });
      if (rc.status !== "success") throw new Refusal("REVERTED", "attestCleanPass reverted.", { tx: txOf(rc) });
      const ev = rc.logs.map((l) => { try { return decodeEventLog({ abi: A.IncentivePool, ...l }); } catch { return null; } }).find((x) => x?.eventName === "CleanPassAttested");
      const passId = ev.args.passId;
      const ps = await read(C.pool, A.IncentivePool, "getPass", [passId]);
      const s = session(); s.passes = [...new Set([...(s.passes || []), passId.toString()])]; saveSession(s);
      step("pending", `Pass ${passId} recorded as PENDING with your ${formatEth(value)} pass bond: anyone may challenge it until ${utc(ps.window)}; no refund moves until it is counted, and the next attested detection cancels the claim`, { tx: txOf(rc, "attestCleanPass") });
      return { status: 1, passId, window: Number(ps.window), tx: txOf(rc, "attestCleanPass") };
    }),

    /** After the clean window: anyone counts the pass. */
    countPass: (passId) => run("countPass", async (step) => {
      await ensureReady();
      const ps = await read(C.pool, A.IncentivePool, "getPass", [BigInt(passId)]), t = await now();
      if (Number(ps.state) === 3) { step("check", `Pass ${passId} is already counted`); return { alreadyCounted: true }; }
      if (Number(ps.state) !== 1) throw new Refusal("NOT_PENDING", `Pass ${passId} is ${PASS_STATES[Number(ps.state)]}.`);
      if (t < ps.window) throw new Refusal("WINDOW_OPEN", `The clean window is open until ${utc(ps.window)} (${Number(ps.window - t)} s from now). Nothing was sent.`, { secondsLeft: Number(ps.window - t), window: Number(ps.window) });
      const d = encodeFunctionData({ abi: A.IncentivePool, functionName: "countPass", args: [BigInt(passId)] });
      const why = await simulate({ from: account, to: C.pool, data: d });
      if (why) throw new Refusal("WOULD_REVERT", `countPass would revert (${why.name}); nothing was sent.`, { revert: why });
      const rc = await walletSend({ to: C.pool, data: d });
      const ev = rc.logs.map((l) => { try { return decodeEventLog({ abi: A.IncentivePool, ...l }); } catch { return null; } }).find((x) => x?.eventName === "CleanPassCounted");
      if (rc.status !== "success" || !ev) throw new Refusal("REVERTED", "countPass failed.", { tx: txOf(rc) });
      step("counted", `Counted: ${ev.args.counted} qualified clean pass(es) on this claim; u(N) ${Number(ev.args.upperBoundBp) / 100} % is an upper bound on how often a plume could be present on such passes, not a statement that the leak is fixed`, { tx: txOf(rc, "countPass") });
      return { counted: Number(ev.args.counted), upperBoundBp: Number(ev.args.upperBoundBp), tx: txOf(rc, "countPass") };
    }),

    /** Live state of your sandbox run: your key, covenant, episodes, pool, claim and passes. */
    status: (opts = {}) => run("status", async (step) => {
      const s = session();
      const covenantId = opts.covenantId || s.covenantId;
      const t = await now();
      const out = { network: { chainId: net.chainId, name: net.name }, chainTime: Number(t), account, contracts: { ...C } };
      if (account) {
        const [o, r, valid] = await read(C.registry, A.KeyRegistry, "keyStatus", [account, t]);
        const org = Number(r) ? new TextDecoder().decode(Uint8Array.from(o.slice(2).match(/../g).map((x) => parseInt(x, 16)))).replace(/\0+$/, "") : null;
        out.observer = { registered: Number(r) !== 0, org, role: ROLE_NAMES[Number(r)], valid };
        out.balanceWei = await pub.getBalance({ address: account });
        out.withdrawable = { covenantWei: await read(C.covenant, A.MethaneCovenant, "withdrawable", [account]),
          poolWei: C.pool ? await read(C.pool, A.IncentivePool, "withdrawable", [account]) : 0n };
        step("observer", out.observer.registered ? `Your key: org ${org}, role ${out.observer.role}, ${valid ? "valid now" : "not valid now"}` : "Your key is not in the sandbox registry", { status: "info" });
      }
      if (covenantId) {
        const c = await covenantRead(covenantId);
        out.covenant = { id: covenantId, seller: c.seller, buyer: c.buyer, start: Number(c.start), end: Number(c.end),
          createdAt: Number(c.createdAt), k: Number(c.k), minRateKgH: Number(c.minRateKgH), policyHash: c.policyHash,
          fundedWei: c.funded, remainingWei: c.remaining, heldWei: c.held, paidWei: c.paid, openEpisodes: Number(c.openEpisodes) };
        step("covenant", `Covenant ${BigInt(covenantId)}: bond remaining ${formatEth(c.remaining)}, held ${formatEth(c.held)}, paid ${formatEth(c.paid)}`, { status: "info" });
        const ids = opts.episodeIds || s.episodes || [];
        out.episodes = [];
        for (const id of ids) out.episodes.push(episodeView(await episodeRead(id), t));
        for (const e of out.episodes) step("episode", `Episode ${e.id}: ${e.state}${e.state === "PROPOSED" ? `, challenge window ${e.secondsLeft} s left` : ""}`, { status: "info" });
        if (C.pool) {
          const [pl] = await read(C.pool, A.IncentivePool, "getPool", [b32(covenantId)]);
          out.pool = { state: POOL_STATES[Number(pl.state)], balanceWei: pl.balance, reservedWei: pl.reserved, policyHash: pl.policyHash };
        }
      }
      const claimId = opts.claimId || s.claimId;
      if (claimId && C.pool) {
        const cl = await read(C.pool, A.IncentivePool, "getClaim", [b32(claimId)]);
        out.claim = { id: claimId, state: CLAIM_STATES[Number(cl.state)], episodeId: cl.episodeId, since: Number(cl.since),
          capWei: cl.cap, paidWei: cl.paid, reservedWei: cl.reserved, counted: Number(cl.counted), pending: Number(cl.pending) };
        step("claim", `Refund claim: ${out.claim.state}, ${out.claim.counted} pass(es) counted, ${out.claim.pending} pending`, { status: "info" });
        out.passes = [];
        for (const id of opts.passIds || s.passes || []) {
          const p = await read(C.pool, A.IncentivePool, "getPass", [BigInt(id)]);
          out.passes.push({ id, state: PASS_STATES[Number(p.state)], sceneAt: Number(p.sceneAt), window: Number(p.window),
            secondsLeft: Math.max(0, Number(p.window) - Number(t)), submitter: p.submitter });
        }
      }
      out.links = { registry: addrLink(C.registry), covenant: addrLink(C.covenant), pool: C.pool ? addrLink(C.pool) : null };
      return out;
    }),

    /** For a UI or a test: decode revert data (0x…) by error name from the contracts' ABIs. */
    decodeRevert,
  };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
