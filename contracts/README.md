# MethaneClause contracts

Foundry 1.8.5, solc 0.8.37. Spec: [`docs/spec.md`](../docs/spec.md) §3 (mechanism), §6 (on-chain design), §10 (security tests).

| File | Purpose |
|---|---|
| `src/lib/Attestation.sol` | EIP-712 `Attestation` struct and hashing, domain `{name: "MethaneClause", version: "1", chainId, verifyingContract}` |
| `src/lib/SigLib.sol` | ECDSA recovery (65-byte, low-s only) and sorted-pair Merkle proof |
| `src/KeyRegistry.sol` | Org-scoped keys (OBSERVER / SELLER / BUYER) with full history, rotation, revocation; the admin renounces after setup |
| `src/MethaneCovenant.sol` | Covenants, episodes, k-of-n distinct-org quorum, challenge / contest / finalize, pull payments, settle |
| `src/lib/CleanPass.sol` | EIP-712 `CleanPass` and MARS `Corroboration` structs and hashing for the two-sided clause |
| `src/IncentivePool.sol` | v2 two-sided clause: k-of-n qualified clean passes → capped, cancellable refund installments; reads the core, never writes it |
| `src/lib/Pool{Setup,Verify,Episodes,Installments,Corroboration}.sol` | the pool's five external (linked) libraries: opening checks, attestation checks, reads of core episodes, installment scheduling, the MARS corroboration cycle |
| `src/SuretyBond.sol` | A surety posts the seller's bond and receives everything the bond contract is paid (Anvil only) |
| `src/KeyRegistryV2.sol` | Observer organisations admit and remove members by majority vote, no admin (Anvil only) |
| `script/Deploy.s.sol` | Deploy + register keys + renounce the admin; writes `deployments/<chainId>.json` |
| `script/DeployPool.s.sol`, `script/DeploySandbox.s.sol`, `script/SandboxBase.sol` | Deploy the pool and link its libraries; deploy the judge sandbox [SIM] pair with published keys |

```sh
export PATH="$HOME/.foundry/bin:$PATH"
forge build
forge test -vv                                        # unit + invariants (what `make test-contracts` runs)
forge test --match-path 'test/invariants/*'           # core I1–I7 + pool P1–P9
forge test --match-contract KeyRegistryV2Invariants   # KeyRegistryV2 invariants (in test/unit/KeyRegistryV2.t.sol)
forge script script/Deploy.s.sol --rpc-url http://127.0.0.1:8545 --broadcast   # Anvil, uses the public test mnemonic
```

Public testnet: `make deploy-sepolia` (Ethereum Sepolia, 11155111; Etherscan-verified) or `make deploy-base`
(Base Sepolia, 84532). Set `PRIVATE_KEY_DEPLOYER`, `SELLER_ADDR`, `BUYER_ADDR`, `OBSERVER_{A,B,C}_ADDR` in `.env`.
Nothing is defaulted off-Anvil. See the header of `script/Deploy.s.sol`. The deployed addresses are in
`deployments/11155111.json`; Foundry's receipts in `broadcast/Deploy.s.sol/11155111/`.

## Decisions that differ from the spec (Sepolia deployment)

- **Chain: Ethereum Sepolia, not Base Sepolia.** On 2026-10-15 every Base Sepolia faucet we tried needed a browser
  wallet or a mainnet balance; Google's Sepolia faucet funded a pasted address. This is the spec's own fallback
  (§6, §12). Same bytecode; `make deploy-base` still works. Cost: 12-second slots instead of 2, so the judge's
  challenge window is 10 minutes, not seconds.
- **A second deployment, the judge sandbox [SIM].** `Deploy.s.sol` also deploys a separate `KeyRegistry` +
  `MethaneCovenant` whose seller, buyer and two observer keys are derived from public strings
  (`keccak256("MethaneClause judge sandbox v1 [SIM] testnet-only / <ROLE>")`), so a judge with a fresh wallet can play
  every role (`scripts/judge.sh`). Why separate: every covenant trusts every OBSERVER org of its registry, so
  published observer keys in the main registry would let anyone reach the main covenant's quorum. Sandbox amounts are
  the main policy / 100 and its windows are 10 minutes. The controllers of SANDBOX_SELLER, SANDBOX_BUYER,
  SANDBOX_OBS_1 and SANDBOX_OBS_2 are the deployer (so a griefer holding a published key can revoke it, but cannot take
  the org over); OBS_JUDGE's controller key is published so any judge can register (next item), and so is the
  OBS_INDEPENDENT open slot's; both registries' admins are renounced.
  Cost: the sandbox proves the mechanism, not observer independence — the main deployment's keys stay private.
  Anyone holding a published key can also use it against the sandbox: revoke it (the deployer then registers the next
  key generation, `make sandbox-rekey`; the scripts pick the first valid generation), propose on a judge's covenant
  first (the script detects it; `make judge-reset`), or contest with the seller key (confirming a contested sandbox
  episode needs k+1 = 3 observer orgs: OBS_1, OBS_2 and OBS_JUDGE; otherwise it expires and nobody is slashed). The
  published seller key only ever receives gas money; the judge's own wallet posts the bond with `fund()`.
- **OBS_JUDGE, a judge's own observer key (approved 2026-10-15).** A sandbox observer org whose *controller* key is published: `judge-attest`
  uses it to `registerKey` the judge's own wallet as an OBSERVER of OBS_JUDGE (register, not rotate, so several judges
  hold valid keys at once), and the judge's episode is signed by the judge's key + published observer 1. The live
  answer to "all observer keys are yours". Adding an org needs the registry admin, which was renounced, so the sandbox
  pair was redeployed once (`script/DeploySandbox.s.sol`, `make deploy-sandbox-sepolia`); the first sandbox pair stays
  on chain unused, and the main deployment was not touched.
- **Sepolia gas is not Anvil gas.** Sepolia currently prices state creation far above the Cancun schedule Foundry
  simulates locally (e.g. `proposeEpisode` ≈ 1.44 M gas on Sepolia vs ≈ 368 k on Anvil; a value transfer to a fresh
  address ≈ 205 k instead of 21 k). The first deploy attempt ran out of gas on Foundry's estimate; the deploy target
  now multiplies estimates, and the Python scripts ask the node (`eth_estimateGas`) instead of using constants.
  `data/derived/measurements.json` reports both schedules.

## Decisions that differ from the spec (post-quantum keys)

- **Hybrid keys (algId 2) on Anvil; the Sepolia main observers are algId 1.** The local demo registers OBS_A/B/C with
  `pqPubKeyHash = SHA-256(ML-DSA-44 public key)` (`pipeline/observer/pq.py`; public test seeds, never derived from the
  ECDSA key) and `scripts/verify_bundle.py` checks both signatures. The Sepolia deployment predates the hybrid keys and its observer
  keys are ECDSA-only; moving them is `rotateKey(old, new, 2, pqHash)` by each org controller, not a redeploy.

## Decisions that differ from the spec (v2 IncentivePool)

Each entry: what, why, what it costs. The frozen surface of spec §11 is unchanged by all of them.

- **A VERDICT_ONLY pool holds no ETH and takes no top-up.** spec §11 says "VERDICT_ONLY accepts 0" and "later
  calls are top-ups by anyone". The pool opens a VERDICT_ONLY pool only with `msg.value == 0`; any later `fund` call
  (and any VERDICT_ONLY call carrying ETH) reverts `BadParams`. Why: in that mode cap, reserved and paid are policy
  units with no balance (§3 "Settlement modes"), so ETH sent there could only sit until `sponsorWithdraw`, and a
  zero-value top-up changes nothing but adds a `PoolFunded` log. Cost: a sponsor cannot pre-fund observer fees in a
  VERDICT_ONLY pool (the spec pays the fee in ONCHAIN only).
- **Extra fail-closed `fund` checks.** Besides the §3 rules (`holdWindow ≥ publicationLag + challengeWindow +
  contestWindow`, `resolveWindow ≥ cleanWindow`, thresholds strictly increasing, Σ shareBp = 10000, `upperBoundBp`
  non-increasing), `fund` also refuses `policyHash == 0`, `cleanWindow == 0`, a zero pass or challenger bond, a first
  threshold of 0, a zero share, `upperBoundBp[0] > 10000`, any installment set where the third rule is not the
  only `isFinal` one, and a final installment without `twoInstruments` (R21; `test_fund_badParams_finalWithoutTwoInstruments`). Why: a zero bond makes clean-pass spam or challenges free (R16, R17), and P2 / R27 hang on exactly
  one final installment at the end. Cost: none for POLICY_V2 / sandbox values (they satisfy every rule).
- **Error choices the spec leaves open.** `WrongCovenant` = the episode's covenant has no pool in this contract;
  `PoolClosed` = the pool exists but is not OPEN (openClaim) or is CLOSED (fund); `closeClaim` on an unknown claim
  reverts `ClaimNotOpen`, a repeat reverts `InvalidState`; `receive()` reverts `InvalidState`; a repeated
  `sponsorWithdraw` reverts `NothingToWithdraw`; `fund` that would open a pool at or after its closing time reverts
  `PoolClosed`. Clean-pass batches: a signature count that differs from the attestation count, or more than 16
  attestations, reverts `BadParams` (the core says `InvalidParams`); an `instrumentId` outside 1..7 reverts `BadParams`
  because the claim's `instrumentsSeen` is an 8-bit set (the observer's ids are 1..5); an unknown or already-ended pass
  reverts `InvalidState` in `challengePass`, `resolvePass`, `countPass`, `finalizePass` and `voidPass` (also on a pass
  whose claim is not CANCELLED). Installments: `release` of anything but a PENDING installment reverts `InvalidState`; then a later detection
  live or confirmed reverts `SourceUnderReview` / `RelapsePending`, even before `claimableAt`; only then does an early
  call revert `WindowOpen` (spec §8, §20).
  `relapse` on a claim that is not OPEN reverts `ClaimNotOpen`; with a live episode (it may still end VOID)
  `SourceUnderReview`; with anything that is not a CONFIRMED / EXPIRED episode at the claim's source detected after
  `since` `NotARelapse`. Corroboration: `windowFrom > since` or `windowTo > now` reverts `SceneOutOfRange`; a window
  that does not reach the latest counted scene reverts `NotCorroborated`; one that does not move `corroboratedTo`
  forward, or a second one while one is PENDING, reverts `InvalidState`; a kind other than 7 or a provider other than
  `"unep-mars"` reverts `BadKind`; an answer to a challenge with another snapshot or window reverts
  `AttestationMismatch(0)`, after `resolveBy` `WindowClosed`. `challengeCorroboration` with nothing PENDING reverts
  `InvalidState`, after the clean window `WindowClosed`, on a reason other than 5 `InvalidReason`, with another bond
  `BadBond`. `finalizeCorroboration` with nothing pending reverts `InvalidState`, before its time `WindowOpen`.
- **Clean-pass details the spec leaves open.** `MAX_PENDING` counts PENDING and CHALLENGED passes (both are open and
  both block `closeClaim`). Pass and challenger bonds are held per pass and never enter `pool.balance`, so
  `sponsorWithdraw` can never hand them out. The observer fee is paid only when the unreserved balance covers the
  whole fee; an org without a controller forfeits its share to the pool, and division dust stays in the pool.
  `resolvePass` binds `claimId`, `sourceId` and `sceneId` to the pass and requires the attestations to agree with each
  other on the other scene fields; a CONFIRM counts the pass at once (it re-checks P9, so it cannot count past a live
  detection); after review it also binds `sceneHash`. Cost: none of these changes a frozen signature.
- **Corroboration cycle (spec §20).** An attested corroboration is PENDING for `cleanWindow`; anyone may
  challenge it on reason 5 with `challengerBond`; k orgs answering with the same snapshot and window before
  `resolveBy` make it effective and the challenger's bond goes to the seller; unanswered, `finalizeCorroboration` drops
  it and refunds the challenger (nobody slashed); unchallenged, `finalizeCorroboration` makes it effective after
  `cleanWindow`. An answer is not re-checked against passes counted after the original attestation (that coverage
  was checked then). `attestCorroboration` also applies P9. Events (spec §20): `CorroborationPending(claimId,
  marsSnapshotHash, effectiveAt)` when it is attested, `CorroborationChallenged(claimId, challenger, resolveBy)` when it
  is challenged, so observers can subscribe to a challenge they must answer; `ClaimCorroborated` still marks effect.
  Cost: the final share waits one more clean window.
- **The corroboration gate sits at scheduling, not at release.** spec §11 requires `corroboratedTo ≥` the final
  installment's triggering pass. The pool records the triggering pass when the final's count and two-instrument rules
  are met and schedules (reserves) the final share only once a MARS corroboration reaches it, at that count or when the
  corroboration arrives. Why: gating at `release` would reserve sponsor money that can never be paid if MARS truly shows
  a plume, so the claim, and the pool, could never close. Cost: none to the rule; `NotCorroborated` is raised where it
  is reachable (a corroboration that does not reach the latest counted pass).
- **UNFUNDED installments.** They wait in a per-pool first-in-first-out queue; each ONCHAIN `fund` schedules up to 8 from
  the head while the unreserved balance covers them (a top-up of 1 wei continues). A new installment first lets the
  queue drain with the current balance and queues behind anything still waiting, so it never skips an older one. A PENDING or UNFUNDED installment
  keeps its claim from closing (spec §20): sponsors cannot walk away from an earned installment. Cost: a
  pool with an unfunded installment stays CLOSING until a top-up covers it or a relapse voids it.
- **`relapse` accepts CONFIRMED or EXPIRED only.** A PROPOSED / CHALLENGED / CONTESTED episode may still end VOID, so it
  freezes the claim (`SourceUnderReview`) instead of cancelling it. Accepting EXPIRED closes R23's second form: the
  caller names the episode, so a displaced `latestEpisode` slot hides nothing from `relapse`.
- **Units and credit notes.** In VERDICT_ONLY, installment reserves are policy units on the claim only (`pool.reserved`
  stays ETH-only and 0); `release` emits `CreditNoteDue` with `ref = keccak256(abi.encode(chainid, pool, installmentId))`
  and `InstallmentReleased`, and moves no funds.
- **Reserved declarations (spec §20).** `CreditNoteSuperseded` is never emitted (supersession is an
  off-chain export step; nobody supersedes a released verdict on chain) and `FinalOutOfTermError` is never
  raised (the event `FinalOutOfTerm` is emitted instead). Both stay declared, marked reserved in NatSpec.
- **Code size: five linked libraries.** To stay under EIP-170 (24,576 B) part of the pool's logic lives in external
  libraries called by DELEGATECALL on the pool's own storage: `src/lib/PoolVerify.sol` (attestation and scene checks,
  observer fee split), `src/lib/PoolSetup.sol` (opening checks), `src/lib/PoolEpisodes.sol` (reads of core episodes),
  `src/lib/PoolCorroboration.sol` (pending / challenge cycle of a MARS corroboration) and `src/lib/PoolInstallments.sol` (installment
  scheduling and cancellation). Every §3.1 function, event and error stays declared on `IncentivePool` (pinned by
  `IncentivePoolSurfaceTest`); the libraries raise the same error selectors and emit the same event topics.
  `make core-bytecode` is unchanged: the core's compiler settings are untouched. Cost: a deployment links five
  libraries (Forge does it in `forge script`; the deployer's choice of library addresses is a trust point, recorded
  and verified with the pool), and each call into one costs an extra DELEGATECALL.

## SuretyBond: the bond can be posted by a surety (`src/SuretyBond.sol`)

Settlement and refunds are assigned to the surety on chain. The SuretyBond contract's address is the covenant's seller;
it never signs. Roles are fixed at deployment: `operator` (the real seller [SIM in the demo]) and `funder` (the surety
[SIM]). The seller org's controller registers the bond's address as SELLER; the funder (the surety) needs no registry
role and is the only withdrawer.

| Function | Who | What it does |
|---|---|---|
| `constructor(covenant, pool, operator, funder)` | deployer | `pool` may be `address(0)`; a pool over another covenant contract reverts `PoolMismatch` |
| `createCovenant(CovenantParams p)` payable | operator | creates a core covenant whose seller is this contract; ETH sent along becomes the initial bond; returns `covenantId` |
| `contest(episodeId)` | operator | the core's seller dispute (holds the rebate, never slashes) |
| `recordMitigationClaim(covenantId, sourceId, claimHash)` | operator | logged by the core, inert there |
| `covenant.fund(covenantId)` on the core | funder (anyone) | the surety posts or tops up the bond directly on the core |
| `pull()` | anyone | brings this contract's credits home from the core (settlement refund) and the pool (cure refunds, a failed clean-pass challenger's bond); reverts `NothingToWithdraw` if both are 0 |
| `withdraw()` | funder | sends everything this contract holds to the funder; a failed transfer reverts `TransferFailed` and keeps the balance |
| `revokeSelf()` | funder | closes this contract's SELLER key in the registry: no new covenants; existing ones keep running |

No function sends ETH anywhere except to `funder` (`test_suretyBond_operatorCannotMoveFunds`, R25). Setup the demo
needs, in order: deploy `SuretyBond(cov, pool, operator, funder)`; the seller org's controller calls
`registry.registerKey(sellerOrg, SELLER, address(bond), 1, 0)` (on a v1 registry the admin can do this before
`renounceAdmin`); the operator calls `bond.createCovenant(p)`; the funder calls `cov.fund{value}(covenantId)`; the
buyer opens the pool (`pool.fund`). Events to read: the bond's `CovenantCreatedThroughBond(covenantId, operator,
bond)`, `Pulled(caller, fromCovenant, fromPool)`, `FunderWithdrawn(funder, amount)`, `SelfRevoked(funder)`; the core's
`CovenantCreated` (seller = the bond's address) and `CovenantSettled`; the pool's `InstallmentReleased` and `Credited`
(account = the bond's address).

## KeyRegistryV2: how a new satellite operator joins (`src/KeyRegistryV2.sol`, Anvil only)

A registry without an admin. Observer organisations are members; a new one is admitted when `max(k, ⌊n/2⌋ + 1)`
member organisations' controllers vote for it (`proposeOrg` / `voteOrg`); its keys are valid, and it votes, only from
`admittedAt + admissionDelay` (production 7 days; Anvil 600 s). A symmetric vote (`proposeRemoval` / `voteRemoval`)
removes a member and closes all its keys at once; a removal that would leave fewer than k members is refused, both
when proposed and again when it executes (two removals proposed at once cannot together drop below k). Votes
are judged when cast (spec §20): an organisation that revoked one of its keys within the last
`admissionDelay` cannot vote (`VoterQuarantined`), and a vote once cast keeps counting. A proposal expires after
`proposalTtl` (production 7 days; Anvil 1 h; late votes revert `ProposalExpired`) and can then be proposed again with
fresh votes; a controller address is reserved while a live proposal names it, so no proposal can be left stuck.
`revokeKey` follows v1: a key that is closed or not yet valid reverts `KeyNotActive`. Only member
organisations hold OBSERVER keys; seller and buyer organisations are fixed at genesis in this prototype. The view
interface is the v1 registry's, so the unchanged core runs on it: `new MethaneCovenant(KeyRegistry(address(v2)), ...)`
(`test_v2_coreAndPoolRunOnV2`; the pool reads it live, and a removed organisation's observer-fee share is forfeited,
`test_v2_poolReadsV2_feeToRemovedOrgForfeited`). Tests: `invariant_V2_noOrgWithoutMajority` (an independent recount of every
admission), `invariant_V2_observerKeysOnlyInMembers`, `test_v2_removal_closesKeys` (R24). Limit: admission changes
the trusted set of every covenant on the registry; a production clause pins its observer set per covenant (not
built). Not deployed to Sepolia: the main registry's admin is renounced.

**Decision that differs from the spec.** spec §17 describes V2 as the v1 registry plus voting. V2 is a fresh
contract with the same view interface instead of a subclass: the v1 `registerKey` is not virtual (any org controller
could mint OBSERVER keys and bypass the vote) and its key validity is private (a new org's keys could not start
after the admission delay, nor be closed on removal). Cost: the key logic exists twice (v1 for the deployed core, V2
for Anvil governance), both under the same rotation / revocation tests.

## Deploying the IncentivePool (`script/DeployPool.s.sol`)

| Command | What it does |
|---|---|
| `make deploy-pool-dryrun` | Offline, part of `make check`: the operator path on a local Anvil (chain id 99911155, public test keys [SIM]); core, a covenant, the main pool opened by the buyer [SIM] under POLICY_V2, the sandbox redeploy with its pool and OBS_INDEPENDENT; the record checked against the chain by `script/check_pool_record.py` (and shown to refuse a record with two library names swapped or an address without code); the missing-key stop checked. Writes only throwaway files. |
| `make deploy-pool-anvil` | On a running Anvil after `script/Deploy.s.sol`: the same two steps with Anvil's test accounts; records in `deployments/31337.json` (not committed). |
| `make deploy-pool-sepolia` | Operator; ran 2026-10-17, record in `deployments/11155111.json`: the main pool over the Sepolia main covenant (`MAIN_COVENANT_ID`, default covenant 1), opened by the buyer [SIM] (`BUYER_PK`) under POLICY_V2, verified on Etherscan, then the record checked against the chain. Needs the operator's key file with a non-empty `SEPOLIA_RPC_URL`; without them the target and the script stop before anything is sent. |
| `make deploy-sandbox-pool-sepolia` | Operator; ran 2026-10-17, record in `deployments/11155111.json`: redeploys the judge sandbox [SIM] pair with its pool and the OBS_INDEPENDENT open slot, then checks the record against the chain; the earlier pair stays on chain and in the record as `sandboxPrevious`. |

**Record keys** (`deployments/<chainId>.json`): `incentivePool`, `incentivePoolLibraries` (`PoolVerify`, `PoolSetup`,
`PoolEpisodes`, `PoolCorroboration`, `PoolInstallments`), `incentivePoolSizes` (deployed bytecode sizes, pool and
libraries), replaced if the main step runs again; `sandbox` is replaced by each sandbox redeploy (the same three keys
for its pool, `incentivePool` being what `make judge-cure` reads, plus `independentController`), and the pair it
replaces moves to `sandboxPrevious`, which keeps one earlier pair only: a second redeploy overwrites it (the older
contracts stay on chain). The v1 entries (`keyRegistry`, `methaneCovenant`, ...) are not touched. Forge deploys and
links the libraries; the script reads their addresses back from the pool's own deployed bytecode at the artifact's
link offsets and refuses a library whose code differs from its compiled artifact (own address masked), so the record
names exactly the libraries the pool calls (the deployer's library choice is a trust point, SECURITY.md). Both pools
on one chain share the same library deployments (stateless code).

**Record written before confirmation.** `forge script` writes the record while it runs, before its transactions are
confirmed. The Sepolia targets therefore run `script/check_pool_record.py` afterwards (every recorded contract has
code; the 20 bytes at each link offset of each pool are the recorded library; sizes as recorded) and, on any failure of
the deployment or the check, stop with: do not commit the record, restore it with
`git checkout contracts/deployments/11155111.json`.

**Sizes from deployed bytecode** (`make deploy-pool-dryrun`, local [SIM] deployment): IncentivePool 22,225 B (EIP-170
limit 24,576 B), PoolVerify 6,541 B, PoolInstallments 3,224 B, PoolSetup 2,879 B, PoolEpisodes 2,182 B,
PoolCorroboration 2,115 B.

**Etherscan verification.** `--verify` on the Sepolia targets verifies the pool and the five libraries. If a
verification has to be repeated, the script prints the exact commands with the deployed addresses and the chain id of
the run (11155111 on Sepolia):

```sh
forge verify-contract --chain 11155111 <PoolVerify> src/lib/PoolVerify.sol:PoolVerify          # and the four other libraries
forge verify-contract --chain 11155111 <IncentivePool> src/IncentivePool.sol:IncentivePool \
  --constructor-args $(cast abi-encode "constructor(address)" <MethaneCovenant>) \
  --libraries src/lib/PoolVerify.sol:PoolVerify:<addr> --libraries src/lib/PoolSetup.sol:PoolSetup:<addr> \
  --libraries src/lib/PoolEpisodes.sol:PoolEpisodes:<addr> --libraries src/lib/PoolCorroboration.sol:PoolCorroboration:<addr> \
  --libraries src/lib/PoolInstallments.sol:PoolInstallments:<addr>
```

`forge script --broadcast` executes a script function twice (a simulation, then again once the libraries are
linked); the sandbox step therefore treats a recorded pair as "previous" only if its contracts exist on chain
(`test_deployPool_sandboxRedeploy_secondPass_keepsRealPrevious`).

## What is proven, and by which method

Six methods, each with its own reach; each property is listed with the strongest method that covers it. Nothing
here claims that the contracts as a whole are formally verified.

| Method | What it establishes | Bound | Command, output |
|---|---|---|---|
| Unit tests | Each custom error is reachable and reverts with nothing written; events and state after each success | the cases written | `forge test` (in `make check`) |
| Fuzz | A pool `CleanPass` signature never verifies under a foreign domain (chain id, contract) | 256 random inputs per run | `testFuzz_cleanPass_foreignDomain_neverVerifies` |
| Stateful fuzz (invariants) | Core I1–I7; pool P1–P9 and `counted` bookkeeping; KeyRegistryV2 "no org joins without a majority" | random sequences: core and KeyRegistryV2 128 runs x depth 128 (16,384 calls); pool 64 runs x depth 512 (32,768 calls), `fail-on-revert`, every planted P1–P9 fault caught | `forge test --match-path 'test/invariants/*'`; KeyRegistryV2: `forge test --match-contract KeyRegistryV2Invariants` |
| Halmos (symbolic) | For **all** inputs within the stated bounds, see below | any-order depth 2 (pool) and 1 (core), plus fixed short lifecycles; loops unrolled to 4 (32 for the share checks) | `make halmos` → `halmos/REPORT.md` |
| Z3 (integers) | The installment rounding lemma, for every cap below 2^128 and every share vector | none (a proof over unbounded integers) | `halmos/rounding_lemma.py`, run by `make halmos` |
| Slither (static) | No High finding; each Medium explained; every finding triaged (a wildcard entry pins its exact count of locations) | the 13 contracts of `src/` | `make slither` → `slither.md` |

**Halmos, property by property** (`halmos/`, outside `forge test`; every result with its paths, time and bounds in
`halmos/REPORT.md`; `make halmos` takes about a quarter of an hour):

- **Installment arithmetic.** For every share and threshold vector the real `PoolSetup.validParams` accepts, every
  cap below 2^128 and every funding, the real `PoolInstallments` creates exactly three installments of
  `cap x share / 10,000`, each PENDING or UNFUNDED, and the claim's reserve equals the pool's
  (`check_installments_amountsAsFormula`, loops unrolled to 32). That the three never exceed the cap and lose at
  most 2 wei is the rounding lemma: as a 256-bit Halmos query it times out (`RoundingLemma.t.sol`, reported as
  TIMEOUT, not as proved), so it is proved by Z3 over integers, which is exact here because `cap x share < 2^142`
  never wraps. A third Z3 query shows the bound is tight: under POLICY_V2's shares a cap of 10,003 wei pays
  3,000 + 3,000 + 4,001 = 10,001, so 2 wei are lost and a 1-wei bound would be false (the witness is in the report).
- **P1 (cap and solvency) and P3 (a PENDING or UNFUNDED installment belongs to an OPEN claim)**, over the
  `IncentivePoolHarness` (below), from a 0.2 ETH pool [SIM] with two OPEN claims: after **every sequence of up to two
  actions in any order**, every argument symbolic (`PoolPropertiesHalmos`). The action set (`PoolActions`): top-up,
  one pass counted through the real `countPass` with its observer fee, a count jump (`h_countTo`), a MARS
  corroboration attested, challenged or finalized, `release`, relapse, `closeClaim`, `sponsorWithdraw` (attesting,
  finalizing, releasing, closing and withdrawing each after a time gap of any length). Not in it: `challengePass`, `resolvePass`, `finalizePass`, `voidPass`, the pool's `withdraw`, and opening
  a third claim. And after every step of four short
  lifecycles with symbolic arguments and optional steps: release then relapse over UNFUNDED installments, a top-up
  serving the UNFUNDED queue, the final share through a challenged MARS corroboration, closing with the observer fee
  and the sponsors' withdrawal (`PoolScenarioHalmos`).
- **Core I4 (bond conservation) on the unchanged core** (the report checks that the Halmos build's
  `MethaneCovenant` and `KeyRegistry` bytecode hash to the pinned v1 values): after every single action from the
  deployed state (`CoreI4Halmos`; actions in `CoreActions`: propose, challenge, contest, resolve, finalize, fund,
  settle, withdraw by any party, a time gap; not in it: a second `createCovenant`, `recordMitigationClaim`) and after every step of four short lifecycles: a paid rebate, a seller contest, a challenge left
  unanswered, settlement with any top-up (`CoreScenarioHalmos`). Signatures are free 65-byte inputs and Halmos
  treats `ecrecover` as an uninterpreted function, so a signature may recover to any registered observer key: a
  forged quorum is allowed, which over-approximates who can sign; I4 holding there holds for real signatures.
- **The proofs are not vacuous.** Reachability canaries run alongside and must fail: an installment exists, one is
  UNFUNDED, a share was released, a claim with installments was cancelled, a rebate was paid, a covenant settled,
  a sponsor withdrew. And five planted faults, committed as patches in `halmos/mutants/`, must each be caught by
  their run (`make halmos-mutants`, about 3 minutes, applied to a throwaway copy): a relapse that does not free the
  pool's reserve (P1), a relapse that leaves UNFUNDED installments alive (P3), an installment amount divided by
  9,999 (installment check), a settlement that does not book the refund and a confirmation that does not book the
  payment (I4, the second on a signed path).

**The harness.** `halmos/IncentivePoolHarness.sol` is a test-only subclass of the pool. Symbolic execution of the
signed entry points (k-of-n observer signatures in the pool; a confirmed core episode behind `openClaim` and
`relapse`) is out of reach, so the harness adds stand-ins that make the same writes to claims, passes, installments
and the pool as the real function after those checks, under the same pool-side preconditions or weaker ones
(`h_openClaim`, `h_attestPass`, `h_attestCorroboration`, `h_relapse`, and `h_countTo`, which lets a claim's count
jump to any value). Weaker preconditions only add behaviours, so a safety property proved over the harness holds
for the pool. Everything else (`fund`, `countPass`
with the observer fee, `release`, `finalizeCorroboration`, `challengeCorroboration`, `closeClaim`,
`sponsorWithdraw`, all five libraries) is the deployed code.

**Not covered by any proof here:** signature verification itself (unit tests: tampered digest, wrong key, replay,
cross-domain; stateful fuzz); sequences longer than the Halmos bounds, including a core resolution signed by k
observers after a challenge (two symbolic signature batches did not finish in 15 minutes) and pool sequences of
three or more actions in any order, or including the pass challenge / resolve / finalize / void and `withdraw`
actions left out of `PoolActions` (the stateful fuzz covers those and long random sequences, without a proof); P2 and
P4–P9, KeyRegistryV2 and SuretyBond (unit tests and stateful fuzz only); gas and denial-of-service limits beyond the coded caps (`MAX_ATTESTATIONS`, `MAX_PENDING`, `MAX_SCHEDULE_PER_FUND`);
the compiler, the EVM and Halmos's own models; the off-chain pipeline and observers; which libraries a deployer
links (`script/check_pool_record.py` checks a deployment against the artifacts: each library's code with its own
address masked, and the link bytes in each pool).

## Interface notes for off-chain signers

- **Digest:** `keccak256(0x1901 ‖ domainSeparator ‖ hashStruct(Attestation))`. The contract exposes `attestationDigest(att)`, `DOMAIN_SEPARATOR()` and EIP-5267 `eip712Domain()`. Signature format: 65 bytes `r ‖ s ‖ v`, with `v ∈ {27, 28}` and low `s`.
- **Type string:** `Attestation(bytes32 covenantId,bytes32 sourceId,bytes32 evidenceHash,bytes32 modelHash,uint32 emissionKgH,uint32 uncertaintyKgH,uint64 detectedAt,uint8 instrumentId,bytes32 providerId,uint8 kind,uint64 nonce,uint64 expiry)`. Kinds: `1 = DETECT`, `2 = CONFIRM`, `3 = WITHDRAW`.
- **Covenant id:** `bytes32(uint256(n))`, sequential from 1.
- **Covenant start:** `CovenantParams.start` = 0 means now; otherwise a backdated start at most `MAX_LOOKBACK` (365 days) ago, with `start + term` still in the future. The term runs from `start`; `createdAt` stores the creation time so a backdated covenant is visible as such. Put `start` in the policy JSON so it is committed in `policyHash`.
- **Source set:** leaf = `keccak256(bytes.concat(keccak256(abi.encode(sourceId))))` (the OpenZeppelin `StandardMerkleTree` leaf for type `bytes32`); pairs are hashed sorted. `sourceId` is chosen off-chain; the tests use `keccak256(source_name)`, and the pipeline uses one leaf per covered facility (`keccak256(MARS source_name)`, `data/derived/sourceset_v2.json`).
- **Batch rule:** every attestation in one call must carry the same covenant, source, evidence, kind, rate, uncertainty, `detectedAt`, instrument, provider and modelHash. If observers disagree, the call reverts with `AttestationMismatch` and no quorum forms.
- **Freshness:** `now ≤ expiry ≤ now + 1 day`. Nonces are per key (bitmap), so any unused `uint64` works.
- **Rejections are fail-closed.** One bad attestation reverts the whole call: `InvalidSignature` (edited data or an unknown key), `SelfAttestation`, `AttestorNotIndependent` (key is not an OBSERVER), `RevokedKey`, `Replay`, `Expired`, `QuorumNotMet`. Before the quorum check: `BadBond`, `DuplicateEvidence` (idempotent resubmission), `NotCovered` (Merkle proof fails: only covered facilities are leaves), `AttestationMismatch` (observers disagree); after it: `DetectionOutOfTerm`, `BelowMinRate`. `make demo` exercises each as a real failed transaction.

## Measured

`proposeEpisode` with 2 attestations over a 4-source set costs about 380k gas (execution only, from `test_gas_proposeEpisode_2of3`). Run `forge test --gas-report` for the full table.
