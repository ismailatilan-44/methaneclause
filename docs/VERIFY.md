# Verify it yourself

Two questions a technical judge asks first, answered from the code and then from the chain: who signs what, what goes
on chain and what the contract decides; and does the Ethereum Sepolia record say what the README says. The second
needs only Foundry's `cast` and a public RPC: no `make setup`, no key, and no trust in our page or an explorer.

## Who signs what, what goes on chain, what the contract decides

Read against [`contracts/src/MethaneCovenant.sol`](../contracts/src/MethaneCovenant.sol),
[`contracts/src/KeyRegistry.sol`](../contracts/src/KeyRegistry.sol) and
[`contracts/src/IncentivePool.sol`](../contracts/src/IncentivePool.sol).
Front words map to code names: witness = observer organisation, sighting = episode, the seller's appeal = `contest`,
anyone's objection = `challenge`, appeal window = challenge window, earn-back = refund, earn-back step = installment.

| Who | Sends | What goes on chain | What the contract checks, then decides |
|---|---|---|---|
| **Seller** [SIM] | `createCovenant`: the buyer, a Merkle root of the covered sources, `policyHash`, k, the windows, the rebate per tier, and the bond as `msg.value` | the covenant, its real `createdAt`, the bond | seller and buyer hold registered SELLER and BUYER keys of two different organisations; the parameters are complete; a backdated `start` stays within the lookback and is stored next to `createdAt` |
| **Witness** (observer organisation), k of them | an EIP-712 `Attestation`, secp256k1 ECDSA, binding `evidenceHash` (SHA-256 of the RFC 8785 JCS bundle), the rate, its uncertainty, the time, instrument, provider, a nonce and an expiry; hybrid observers (algId 2) add ML-DSA-44 (FIPS 204) over the same bundle bytes, checked off chain | the attestation fields and signatures; the bundle itself stays with the providers and the observers | each signature recovers to a key `KeyRegistry` holds as OBSERVER and valid now; its organisation is neither the seller's nor the buyer's; the nonce is unused; the expiry is between now and one day ahead; all attestations carry the same fields; at least k distinct organisations. One bad attestation reverts the whole call |
| **Any account** | `proposeEpisode`: the k attestations, a Merkle proof that the source is covered, the proposer bond | episode PROPOSED with the tier's rebate held from the bond, or LINKED (recorded, no payout) inside the link window of a live or confirmed episode at that source | the source is in the covenant's set; the `evidenceHash` was never used; the detection falls inside the term; the rate is at least the minimum |
| **Anyone** | `challenge(episodeId, reasonCode)` with the challenger bond, inside the challenge window, on an objective reason (hash mismatch, source not covered, duplicate plume, revoked key, stale) | CHALLENGED | k witnesses answer with `submitResolution`: k CONFIRM gives CONFIRMED (the challenger's bond goes to the buyer); k WITHDRAW gives VOID (the rebate returns to the bond; the challenger gets both bonds) |
| **The seller** (its appeal) | `contest(episodeId)`, inside the challenge window | CONTESTED; the rebate stays held | k + 1 CONFIRM gives CONFIRMED; with no answer in the contest window, anyone's `finalize` gives EXPIRED: the rebate returns to the bond and no one is slashed |
| **Anyone**, after the window | `finalize(episodeId)` | PROPOSED becomes CONFIRMED and the rebate is credited to the buyer; an unanswered CHALLENGED or CONTESTED becomes EXPIRED | the deadline has passed |
| **Buyer** [SIM] | `withdraw()` (a pull) | the credited rebate leaves the contract | something is credited to the caller |
| **Sponsor** [SIM] | `IncentivePool.fund` | the pool balance, its `policyHash` and parameters | the first call comes from the covenant's buyer or seller and fixes the terms; a top-up, from anyone, must match them. The pool reads the core's verdicts and never writes the core |
| **Anyone**, then k witnesses | `openClaim` on a CONFIRMED episode; `attestCleanPass` with k EIP-712 `CleanPass` signatures and the pass bond; `countPass` after the clean window | the claim; each pass PENDING, then counted | the scene rules (provider's cloud flag, publication, instrument floor at most the episode's rate, a new scene, no later detection under review); `release` credits the seller's installment (verdict-only: a credit note); `relapse` with a later CONFIRMED or EXPIRED episode at the source cancels what is unpaid |
| **The bond rule** (off chain) | the bond size, fixed before the covenant | `policyHash` only | no forecast triggers, blocks or changes a payout; only k-of-n attested positive detections do ([`MODEL_CARD`](../pipeline/model/MODEL_CARD.md)) |

No contract has an owner, an upgrade path or a pause; the threats, mechanisms and tests behind each row are in
[`SECURITY`](SECURITY.md).

## Re-check the chain in 60 seconds, Foundry only

Install Foundry with `foundryup -i v1.8.5` (the same toolchain `make test` uses). The addresses come from
[`contracts/deployments/11155111.json`](../contracts/deployments/11155111.json); the operator's run is recorded in
[`data/derived/testnet_run.json`](../data/derived/testnet_run.json). The RPC must serve old receipts and state: a
pruned node (for example publicnode) answers no receipt for these blocks, and `--async` makes `cast` stop instead of
waiting.

```sh
RPC=https://sepolia.gateway.tenderly.co          # keyless public Sepolia RPC that keeps history
COV=0xcAcBD7E8d995EFeB70a8b82Aa525726A3797f5f8   # MethaneCovenant
REG=0x8f96340b051ac19D92976cC14aAEA0461Eaf0fc7   # KeyRegistry
TAMPER=0x93a4c8bffda75e6b2ddedc37619e98ab1457d6038ac0872d66b49751b7a85a3f
PROPOSE=0x263e9321e985bee7086c4da45bbb57c0059c8853716c5905e31a2dcf09af7508
FINALIZE=0x470bcc95bfc58a9eb316c1da588f6628156ba51c6330bf078acfaa71029a43cd
WITHDRAW=0xe15306ee30432c7e92f9ccfec14ee9ea4f799cd6324f704723694159e4a819e4

# 1. Refused: the signed record with its rate edited, signatures kept
cast receipt --async $TAMPER status --rpc-url $RPC
cast run --quick $TAMPER --rpc-url $RPC 2>&1 | grep -A1 -E 'ecrecover|keyStatus|Revert'

# 2. Who signed the real record
cast run --quick $PROPOSE --rpc-url $RPC 2>&1 | grep -A1 -E 'ecrecover|keyStatus'
cast parse-bytes32-string 0x4f42535f41000000000000000000000000000000000000000000000000000000

# 3. The verdict and the payout
cast receipt --async $FINALIZE status --rpc-url $RPC
cast call $COV 'getEpisode(uint256)((bytes32,bytes32,bytes32,address,uint96,address,uint96,uint64,uint64,uint64,uint32,uint8,uint8,uint8,uint8,uint64,uint128))' 1 --rpc-url $RPC
cast receipt --async $WITHDRAW status --rpc-url $RPC
cast tx $WITHDRAW from --rpc-url $RPC
cast call $COV 'withdrawable(address)(uint256)' 0x6FEfe70603a9d90Aa77bedEc076bdCd2E9A01743 --rpc-url $RPC

# 4. No administrator: the registry the covenant trusts has renounced its admin
cast call $COV 'registry()(address)' --rpc-url $RPC
cast call $REG 'admin()(address)' --rpc-url $RPC

# 5. The clause as stored: start, createdAt, k, bond, paid
cast call $COV 'getCovenant(bytes32)((address,uint64,uint32,bool,address,uint64,uint64,uint8,uint32,bytes32,bytes32,bytes32,bytes32,uint64,uint64,uint64,uint128[3],uint256,uint256,uint256,uint256,uint256))' 0x0000000000000000000000000000000000000000000000000000000000000001 --rpc-url $RPC
```

What each step prints (run against Sepolia with `cast` 1.8.5):

| Step | Prints | What it shows |
|---|---|---|
| 1 | `false`; then `ecrecover` returns `0x96EbE8C1776DE0d14036d281EEF310f288f370f1`, `keyStatus` returns zeros, `[Revert] InvalidSignature()` | receipt status 0. The first signature is OBS_A's, with the same v, r and s as in step 2, but over the edited data it recovers to an address no organisation registered, so the call reverts and the bond does not move |
| 2 | `ecrecover` returns `0xc208f0a1F357B89d611cbb77F9b7630EC246b7Ae` and `0xf1A111C3bf2638bD00B17C89abD834f3C4F01D11`; each `keyStatus` returns an organisation (`0x4f42535f41…`, `0x4f42535f42…`), role 1 (OBSERVER) and valid 1; `OBS_A` | the two signatures are `observerA` and `observerB` of the deployment record, two distinct organisations |
| 3 | `true`; a 17-field tuple whose third field is `evidenceHash` `0x07865dea…c2c9` and whose fields 11 to 14 are `2548` (kg/h), `2` (tier), `2` (organisations), `4` (CONFIRMED), with the rebate `3000000000000000` wei last; `true`; `0x6FEfe70603a9d90Aa77bedEc076bdCd2E9A01743`; `0` | finalized CONFIRMED with the values of `data/derived/testnet_run.json`; the covenant's buyer [SIM] sent the withdrawal and nothing is left credited to it |
| 4 | `0x8f96340b051ac19D92976cC14aAEA0461Eaf0fc7`; `0x0000000000000000000000000000000000000000` | the covenant's registry is `$REG`, and its admin is the zero address |
| 5 | start `1767225600` (2026-01-01, backdated [SIM]), `createdAt` `1792021200` (2026-10-14), k `2`, bond `15000000000000000` wei funded, `3000000000000000` paid | the start was backdated in the open: the chain records when the clause was written, after the plume it settles ([Limits](../README.md#limits)) |

## Deeper checks

- `make verify-evidence` (after `make setup`) re-reads every recorded Sepolia transaction, the deployed code, its
  public source verification, each pool's opener and terms, and the signed bundle, read-only through public RPCs.
- `make verify-bundle` checks both halves of each observer's hybrid signature (secp256k1 ECDSA and ML-DSA-44) on the
  last `make demo` run, against the key hash `KeyRegistry` anchors.
- In the browser: the [page with the edit applied](https://ismailatilan-44.github.io/methaneclause/?tamper=A)
  recomputes the JCS hash and both signatures.
