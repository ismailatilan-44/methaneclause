# Browser wallet back end (`app/wallet.js`)

A judge connects MetaMask (or any injected EIP-1193 wallet) and becomes an observer in the **judge sandbox [SIM]** on
Ethereum Sepolia: their own key is registered in the sandbox KeyRegistry, they sign the EIP-712 attestation over a
canonical evidence bundle, submit the episode from their wallet, and read the result back from the chain. The module
has no DOM code; any page can call it. The wallet's private key never reaches the page: the page only asks the wallet
to sign typed data (`eth_signTypedData_v4`) and to send transactions (`eth_sendTransaction`).

```js
import { createWallet, loadWalletData } from "./wallet.js";
const w = createWallet({ ethereum: window.ethereum, data: await loadWalletData("data/wallet.json") });
await w.connect();                         // accounts + chain check; offers wallet_switchEthereumChain
await w.joinAsObserver();                  // { org: "OBS_JUDGE" } (default) or { org: "OBS_INDEPENDENT" }
await w.openCovenant();                    // your sandbox covenant; you post the bond
const a = await w.signAttestation({ case: "TKM_S_276" });   // your signature
const b = await w.cosignAttestation({ case: "TKM_S_276" }); // published sandbox observer 1
await w.submitEpisode({ bundle: a.bundle, signatures: [a, b] });
await w.finalizeEpisode(episodeId);        // after the 10-minute challenge window
// the two-sided clause (as make judge-cure): relapse episode on DZA_S_012, then
await w.openRefundClaim(episodeId);
const p = await w.signCleanPass({ case: "DZA_S_012" }), q = await w.cosignCleanPass({ case: "DZA_S_012" });
await w.submitCleanPass({ bundle: p.bundle, signatures: [p, q] });
await w.countPass(passId);                 // after the 10-minute clean window
await w.status();                          // live reads: your key, covenant, episodes, pool, claim, passes
```

Every call resolves to `{ ok, action, steps: [{ id, label, status, sim?, tx? }], error?: { code, message }, ... }` and
never throws for an expected failure. `label` is a plain-language sentence for a story view ("You signed the EIP-712
attestation in your wallet … Nothing is on chain yet; no money moved."); `sim: true` marks a step done with a published
sandbox key; `tx` carries `{ hash, link, status }` with the Etherscan link. Error codes a UI can branch on: `NO_WALLET`,
`NOT_CONNECTED`, `WRONG_CHAIN`, `USER_REJECTED`, `ALREADY_IN_REGISTRY`, `NO_COVENANT`, `NO_CLAIM`, `VALIDATION_REFUSED`,
`WOULD_REVERT` (with `revert.name` decoded from the contracts' ABIs; nothing sent), `WINDOW_OPEN` (with `secondsLeft`),
`KEY_REVOKED`, `KEY_MISMATCH`, `SWEPT`. `createWallet` itself throws (`error.code` `WRONG_CHAIN` or `WRONG_CONTRACTS`)
when, without an explicit `network` override, `app/data/wallet.json` names a chain other than Ethereum Sepolia or a sandbox
contract other than the three `SEPOLIA_SANDBOX` pins. A tamper demonstration passes `expectRevert: "InvalidSignature"` to `submitEpisode` /
`submitCleanPass`: the transaction is sent with a fixed gas limit, the receipt is status 0, the reason is read from the
mined transaction, and the bond and episode count are compared before and after (`stateUnchanged`). Amounts are decimal
strings (wei); times are unix seconds. The session (covenant, episodes, claim, passes) is kept in memory, or in the
`store` passed to `createWallet` (`{ get, set }`, e.g. a localStorage adapter).

`app/data/wallet.json` is written by `make wallet-data` (`scripts/build_wallet_data.py`) from committed inputs: the
sandbox deployment record, the observer's bundle templates (built and validated by `pipeline/observer` from the
committed case files), the sandbox covenant and pool policies, the contract ABIs and a parity vector of Python's own
hashes and digests. Tests: `make test-lane-c` (the file is current, holds no key, its builder fails closed),
`make wallet-local` (end to end on Anvil with an injected provider that signs with MetaMask's own library; once
`make wallet-setup`), `make e2e` (the module loads from the pinned CDN in Chrome and reproduces the digests).

## Which keys are published, and why that is honest

The judge sandbox is a separate KeyRegistry + MethaneCovenant + IncentivePool whose other roles use **published**
keys: private key = keccak256 of `"MethaneClause judge sandbox v1 [SIM] testnet-only / " + role`
(`contracts/script/SandboxBase.sol`, `pipeline/observer/sandbox.py`). Roles: `SELLER`, `BUYER`, `OBS_1`, `OBS_2`
(registered keys) and `JUDGE_CONTROLLER`, `INDEPENDENT_CONTROLLER` (the controllers of org `OBS_JUDGE` and of the open
observer slot `OBS_INDEPENDENT`). Anyone can sign with them; they hold only what their next transaction needs, briefly, and
exist so a fresh wallet can play every other role. Every covenant trusts every observer org of its registry, which is
why they live in their own registry: the main deployment's keys are not published. The page derives a published key
only when a step needs it and refuses one whose address differs from the deployment record (or, for a controller,
that the registry does not name).

## Decisions that differ from the spec

- **Published sandbox keys are derived in the page.** The specification says the page never holds a private key. The
  judge's own key never reaches the page. The page does derive the published [SIM] sandbox keys from their public
  strings, because `registerKey` must come from the org's controller (`KeyRegistry._onlyAdminOrController`), the
  covenant is created by its seller, the pool's first funding must come from a covenant party, and the k = 2 quorum needs
  a second observer org. What it costs: anyone can do the same with these keys (revoke them, sweep gas money sent to
  them, race a judge's evidence); the module checks each of these and says what happened, as `make judge-attest` does.
- **The typed data is built from the observer's committed snapshot, not from a live terminal receipt.** The browser
  cannot fetch Carbon Mapper (no CORS header), and a live receipt would need a terminal step in the middle of the
  browser flow. The bundle the judge signs is the one `make judge-attest` builds with `LIVE=0`; the Fetch step says so.
  What it costs: the browser path does not re-fetch the provider; `LIVE=1 make judge-attest` still does.
