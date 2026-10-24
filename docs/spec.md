# MethaneClause — specification

This is the specification of the system as built: the one-sided core (a seller's bond rebates the buyer on an attested
satellite re-detection) and the two-sided extension (a capped, cancellable cure refund on attested qualified clean
passes). Sections 1–10 describe the core and the shared machinery; sections 11–18 the two-sided clause and what came
with it; section 19 the limits; section 20 the decisions taken where the design had a real choice. Contract-level
decisions and error choices are in [`contracts/README.md`](../contracts/README.md); pipeline decisions in
[`pipeline/README.md`](../pipeline/README.md), [`pipeline/observer/README.md`](../pipeline/observer/README.md) and
[`pipeline/export/README.md`](../pipeline/export/README.md); the security assessment in [`SECURITY.md`](SECURITY.md).

Every number quoted in this repository comes from `make reproduce` outputs (`data/derived/audit_numbers.json` and the
files it lists) or from a test; values below name the file they are read from. Real and simulated parts are labelled
[SIM] where they appear.

## 1. Name, track, pitch

- **Name:** MethaneClause — it names the object, a clause in a gas supply contract, not a platform.
- **Track:** primary Track 4, Climate Data & Environmental Monitoring; secondary Track 1.
- **One sentence:** a gas seller posts a bond under a methane clause for named sources; when independent satellites
  re-detect a super-emitter there, k-of-n attested detections from distinct observer organisations rebate the buyer
  from the bond after a challenge window — and nobody, the seller included, can ignore or delete the detection.
- **Two-sided sentence:** satellite witnesses move the money in a gas contract without an operator — a rebate when they
  confirm a leak, a cancellable refund while they keep not seeing one — and a judge can be a witness.
- **Honesty line (core):** the chain enforces process and money; the truth of the data rests on independent observers
  and open challenge. Non-detection never counts as repair, and no core function accepts "no plume".
- **Honesty line (two-sided):** non-detection never cancels a rebate or counts as repair. It can release a capped
  refund that the next attested detection cancels.
<!-- gen:specProblem:begin -->
- **Problem:** two public datasets disagree about the same pipeline. UNEP IMEO MARS labels source TKM_S_276 (Turkmenistan) *absent* — no plume in its own six-month window. Carbon Mapper observed the sources within 1.5 km of TKM_S_276 on 9 passes since MARS's last plume (13 Jun 2025) and recorded a plume on 9, including 2,548 kg/h on 2026-06-11 (`cases.TKM_S_276` and `lead` in `data/derived/audit_numbers.json`; counting rule: `facility_post` in `pipeline/cases.py`). The union over all 5 linked Carbon Mapper sources (10 of 10) stays in the case file as `post_mars_last_plume_all_linked`, labelled secondary: it adds 2026-04-17, a date on which only linked sources centred beyond 1.5 km (1.774 km from the MARS point) recorded a plume.
<!-- gen:specProblem:end -->
- **The wider gap:** across the join of the two datasets about 203 *absent* sources are contradiction candidates
  (`join_v1.n_candidates`). UNEP reports 3,500+ alerts with about 12 % operator response; the alert notifies, it does
  not pay anyone. A buyer who contracted "no super-emitters" has no executable remedy.

## 2. User, the Monday decision, and the operator gap

**User:** the methane-compliance desk of an EU gas importer — the "importer" under Regulation (EU) 2024/1787 (MRV
equivalence from 1 January 2027; first report due 31 May 2028). Article 31 gives the Commission a MARS-based
super-emitter rapid-reaction mechanism; it notifies, it does not pay. Commission Recommendation (EU) 2026/1834 offers
four model clauses that pass information down the chain and leave the economic consequence open; MethaneClause is that
missing clause. Article 2(62) defines a super-emitter as more than 100 kg CH₄/h, which is also the clause's `minRate`.

**Monday decision (one screen):** which of my contracted sources had an attested positive re-detection this month,
what rebate is due from the seller's bond, and what do I file? At renewal: what bond do I ask for this source set?

**Parties:** seller (exporter, posts the bond), buyer (importer, receives rebates), observer organisations (run their
own fetchers over Carbon Mapper and UNEP MARS, sign attestations), challengers (anyone with a wallet); for the refund,
sponsors (lender, programme or buyer) and optionally a surety. Seller, buyer, sponsor and surety are [SIM] in the demo;
the plumes, the MARS labels, the join, the signatures and the chain execution are real.

**The operator gap (why a chain).** No operator exists for this function. Registries (MiQ, JOGMEC CLEAN, UNEP MARS)
certify and notify but cannot move a seller's money; a bank can move money but cannot adjudicate satellite evidence; a
court can do both and takes years. Seller and buyer are counterparties: neither accepts an operator chosen by the
other, so the decision has to come from k independent observers plus an open challenge window — which is what the
contract executes. The registries stay inputs. A signed database owned by any one of these parties can drop a
re-detection or hold the rebate; here a detection, once attested by k independent keys, cannot be suppressed, the
seller cannot hold back the rebate on its own, and no administrator can touch the funds.

## 3. Mechanism: the covenant

**Covenant.** `createCovenant(buyer, sourceSetRoot, policyHash, start, term)` by a registered SELLER key, funded with a
bond in testnet ETH. `sourceSetRoot` is the Merkle root of covered facilities (one leaf per facility,
`keccak256(MARS source_name)`; the linked Carbon Mapper sources are metadata). `policyHash` is the SHA-256 of the
canonical policy JSON (k, n, windows, `minRateKgH`, rebate schedule, `joinVersionHash`, forecaster, GWP-100) —
committed before any evidence. `start` may be backdated up to 365 days (Carbon Mapper publishes Tanager plumes weeks
after the scene); the chain stores `createdAt`, so a backdated covenant is visible as such: such a run proves the
mechanism, not the absence of foreknowledge.

**Episode = one positive re-detection at a covered facility.** Only positive detections are evidence. A seller may
record a mitigation claim (a hash); it is stored and never changes any state.

```
PROPOSED ──(challenge window passes)──────────────► CONFIRMED ──► rebate credited to the buyer (pull)
   │                                                    ▲
   ├─ challenge(bond, objective reason) ─► CHALLENGED ──┤  k observers CONFIRM → CONFIRMED (challenger bond → buyer)
   │                                                    └  k observers WITHDRAW → VOID (proposer bond → challenger)
   └─ contest() by the seller ───────────► CONTESTED ───► CONFIRMED on k + 1 distinct-org attestations
                                                        └► EXPIRED (contest window passes; held rebate returns; nobody slashed)
```

- **Proposal:** anyone submits `evidenceHash` + ≥ k attestations from distinct registered observer organisations and a
  proposer bond. Checks: facility in the set (Merkle proof), `detectedAt` after the covenant start, signer
  organisations outside {seller, buyer}, signatures, nonces and expiry valid, all attestations equal.
- **Linking:** a detection within `linkWindow` (30 days) of an open or confirmed episode at the same facility is
  recorded LINKED — no second payout. A later detection opens a new episode with `parentEpisode` set; past episodes
  are never rewritten.
- **Rebate tiers** (IEA response levels): ≥ 5 t/h tier 1; 1.5–5 t/h tier 2; `minRate`–1.5 t/h tier 3. Rebate =
  min(tier amount, bond remaining).
- **Slashing only on objective failure:** challenge reasons are re-checkable (hash mismatch with provider data, source
  not covered, duplicate plume, revoked key, stale). A scientific dispute (for example attribution to a neighbouring
  facility) goes to `contest`, which can hold a payment but never slashes.
- **No half-states:** every non-terminal state has a deadline after which anyone calls `finalize`.
- **Status is dated, never green:** "no positive re-detection attested since <date>", never "clean".

Main-covenant values (k = 2 of n = 3, challenge window 1 day, contest window 2 days, link window 30 days, minRate
100 kg/h, tier amounts) are in the `core` block of [`data/derived/policy_v2.json`](../data/derived/policy_v2.json).

## 4. Data pipeline

| Step | Source | What is stored |
|---|---|---|
| `pipeline/fetch_cm.py` | Carbon Mapper API v1 (sources CSV, per-source records, annotated plumes), keyless | derived fields only + SHA-256 of each raw response |
| `pipeline/fetch_mars.py` | UNEP IMEO MARS public GeoJSON zip, keyless | field subset + SHA-256 of the zip; plumes restricted to oil and gas |
| `join/join.py` | MARS × Carbon Mapper under the frozen criterion v1 ([`pipeline/join/CRITERION.md`](../pipeline/join/CRITERION.md)) | contradiction candidates: Carbon Mapper plume within 1.5 km of a MARS *absent* source, after MARS's last plume and inside its six-month window; twins counted once |
| `pipeline/cases.py`, `pipeline/scenes.py`, `pipeline/clean_passes.py` | the three demo facilities | plumes, passes, scene records and clean-pass checks |
| `pipeline/canon.py` | — | RFC 8785 (JCS) canonical JSON → SHA-256; Python and JavaScript agree on shared vectors |

Sources, licences, snapshot dates and hashes: [`DATA.md`](DATA.md). Raw responses are never committed.

**Demo facilities.** TKM_S_276 — the lead case (penalty arc; a join candidate). DZA_S_012 — the refund → relapse arc:
EMIT detection 2024-09-29 at 2,674 kg/h (published 2024-11-26), a quiet run, then Tanager 694 ± 88 kg/h on 2026-02-01
(published 2026-03-03) (`cases/DZA_S_012.json`). USA_S_077 — the control: MARS and Carbon Mapper agree, nothing moves.

**Observer service** (`pipeline/observer/`): one process per observer organisation, each with its own key, fetching
the provider independently, building the bundle, canonicalising, hashing, signing (EIP-712 + ML-DSA-44). The hashed
bundle holds provider-derived fields and `providerContentSha256` (the response without expiring signed URLs), not
`retrievedAt`, so k observers agree on one hash; each observer keeps its own receipt. Validation before signing:
schema, physical range, timestamps, facility in the set, linked source, duplicate plume, plume within 1.5 km.
Disagreement on the same plume ⇒ no quorum ⇒ nothing happens.

## 5. Forecaster: a frozen baseline first

The only AI candidate was a next-pass re-detection forecast used to size the bond (never to trigger a payment). The
baselines were frozen before training (B1 = Carbon Mapper persistence ratio over six months; B2 = MARS "absent ⇒ 0"),
the split was held out by country and time, and the decision rule was pre-registered: keep the model only if it beats
B1 in Brier skill with calibration no worse, on both held-out sets. **It did not: the persistence rule is enough**
(`model.decision = RULE_IS_ENOUGH`; [`pipeline/model/MODEL_CARD.md`](../pipeline/model/MODEL_CARD.md)). Bonds are
sized with the smoothed rule B1s, because hard B1 forecasts exactly zero after a quiet six months — the MARS
*absent* reading this product exists to challenge. `modelHash` in the policy is therefore null.

## 6. On-chain design (core)

Solidity 0.8.37, Foundry. **Chain:** Ethereum Sepolia (chainId 11155111; addresses in
[`contracts/deployments/11155111.json`](../contracts/deployments/11155111.json)) and local Anvil. The first choice,
Base Sepolia, was dropped when its faucets required a browser wallet or a mainnet balance; same bytecode.

1. `contracts/src/KeyRegistry.sol` — organisation-scoped keys with full history: `registerKey(orgId, role, addr, algId,
   pqPubKeyHash)`, `rotateKey`, `revokeKey` (by the organisation's controller or the key itself),
   `keyStatus(addr, ts)`. Roles OBSERVER, SELLER, BUYER. `algId` 1 = secp256k1 ECDSA (checked on chain); 2 = hybrid
   ECDSA + ML-DSA-44 (ML-DSA verified off chain; `pqPubKeyHash` anchored). Key validity is checked at submission, so a
   revoked key's attestations fail even if signed earlier. The setup admin renounces; no admin path to funds.
2. `contracts/src/MethaneCovenant.sol` — covenants, episodes, challenges, contests, pull payments: `createCovenant`, `fund`,
   `proposeEpisode`, `challenge`, `contest`, `submitResolution`, `finalize`, `recordMitigationClaim`, `withdraw`,
   `settle`. Custom errors for every rejection; one bad attestation reverts the whole call.
3. `lib/Attestation.sol`, `lib/SigLib.sol` — EIP-712 hashing, low-s ECDSA recovery, sorted-pair Merkle proofs.

```
Attestation(bytes32 covenantId,bytes32 sourceId,bytes32 evidenceHash,bytes32 modelHash,uint32 emissionKgH,
            uint32 uncertaintyKgH,uint64 detectedAt,uint8 instrumentId,bytes32 providerId,uint8 kind,uint64 nonce,
            uint64 expiry)        kinds: 1 DETECT, 2 CONFIRM, 3 WITHDRAW
domain {name "MethaneClause", version "1", chainId, verifyingContract}
```

Replay protection: per-key nonce bitmap, `now ≤ expiry ≤ now + 1 day`, the domain binds chainId and contract. Quorum
counts distinct organisations, never keys. The SHA-256 of both core `deployedBytecode` artefacts is committed in
`contracts/deployments/core_bytecode.sha256` and compared in `make check`: the two-sided extension does not change the
core.

**Core invariants** (stateful fuzz): I1 no CONFIRMED episode with fewer than k distinct-org attestations; I2 no
attestation by the seller's or buyer's organisation is ever counted; I3 at most one payout per episode; I4 bond and
balance conservation; I5 every non-terminal episode becomes terminal after its deadline; I6 a mitigation claim changes
no state; I7 no function lets the deployer move funds.

**Judge sandbox [SIM].** A second, separate registry + covenant whose seller, buyer and two observer keys are derived
from public strings, so a judge with a fresh wallet can play every role (`scripts/judge.sh`). It is separate because
every covenant trusts every observer organisation of its registry: published observer keys in the main registry would
open the main covenant's quorum to anyone. An observer organisation OBS_JUDGE has a published controller key, so the
judge registers their own wallet as an observer and signs with it — the one signer in the run we do not control.
Sandbox amounts are the main policy / 100 and its windows are 10 minutes.

## 7. Off-chain verification: hybrid ECDSA + ML-DSA-44

Each bundle carries two signatures: the secp256k1 EIP-712 signature (checked on chain) and an ML-DSA-44 signature
(FIPS 204, security level 2; public key 1,312 bytes, signature 2,420 bytes) over the same JCS bytes with the context
string `MethaneClause/v1`. `scripts/verify_bundle.py` and the page check both, and accept the ML-DSA half only if SHA-256 of its public key equals the `pqPubKeyHash` the registry holds for the signer (never a hash the bundle carries).

**PQ limit:** "EVM transaction identity and the on-chain signature check remain secp256k1 ECDSA, which Shor breaks;
Keccak/SHA-256 commitments keep ≈128-bit preimage security under Grover; ML-DSA-44 protects the long-term authenticity
of bundles when verified off-chain. We do not claim a quantum-safe chain." Status: FIPS 203 / 204 / 205 final; FIPS
206, NIST IR 8547 and EIP-8051 / EIP-8052 drafts.

## 8. The page (`app/`, one static page, no build step)

- Map with the MARS point and Carbon Mapper plume origins; attribution "Data by Carbon Mapper®" and "UNEP IMEO MARS,
  CC BY-NC-SA 4.0".
- A three-panel card per source: *leak* (Carbon Mapper CH₄ tiles loaded from Carbon Mapper at view time, MARS badge,
  rebate transaction) → *qualified clean passes* (count, upper bound u(N), instruments, floor, installment states) →
  *relapse* (detection, cancel transaction).
- An observer panel showing the five steps **Fetch → Validate → Seal → Sign → Re-check**, with the same labels as the
  observer's terminal output; the judge's browser wallet can sign the typed data built from the terminal receipt.
- Three tamper buttons: edit the rate (hash and both signatures fail); remove a detection from the list (the re-fetch
  content hash differs); edit a scene's cloud fields (`sceneHash` changes, both signatures fail).
- Personas (importer, seller, observer, sponsor); a top box "what this does not prove" (not abatement, not a clean
  site, not legal compliance, not a payment instrument, prototype keys); a mode strip (real / computed / [SIM] /
  projection); a Limits block.
- IEA Annex D export (official labels; "Emissions ceased?" is always "NOT EVIDENCED"; MARS fields verbatim, MARS's
  own `emission_status` left null) with a round trip: export → import → same hash.
- Every waiting window can be skipped on a local chain with a [SIM]-labelled forward clock; on Sepolia nothing is
  skipped.

## 9. Demo runs

- **`make demo`** (Anvil, clock started at 2024-09-01 and moved forward only, each skip printed as `[SIM: chain clock
  advanced …]`): the DZA replay under its own policy (covenant created through a `SuretyBond`, the 2024-09-29 episode
  confirmed, a claim, the qualified passes attested, windowed and counted, a seeded [SIM] cloudy pass challenged and
  voided, installments as the rule yields, the relapse freezing then cancelling the claim); then the core's checks (the
  TKM tamper, rejections as failed transactions with the bond unchanged, the rebate); the verdict-only covenant with
  its credit-note export; the portfolio covenant. Every rejection has receipt status 0.
- **`make judge-local`** — the judge walk-through on Anvil, including the cure flow.
- **Sepolia** — `make judge-attest`, `judge-propose`, `judge-tamper`, `judge-challenge`, `judge-finalize` and `judge-cure`
  (a refund claim in the sandbox pool) in the sandbox with the judge's own key; the main covenant's run is recorded in
  [`data/derived/testnet_run.json`](../data/derived/testnet_run.json).

## 10. Security package

[`SECURITY.md`](SECURITY.md): need-for-ledger (four questions, Wüst–Gervais walk, a signed-database vs chain table);
DFD with trust boundaries; an ISSRM register, STRIDE-elicited, each row with the test that covers it; mitigated vs
introduced; authentication properties; replay; key management (roles, rotation, revocation, `algId`); the PQ limit;
the access-control matrix for all five contracts; the adversarial input set and its detection rate; bonds; assurance
(unit test per error, stateful-fuzz invariants, Slither triage, bounded Halmos proofs and a Z3 rounding lemma); residual risks. Failure handling: [`failure-modes.md`](failure-modes.md).

## 11. The two-sided clause: `IncentivePool`

A separate contract reads the core's episodes and never writes them. It runs on Ethereum Sepolia (a main pool opened by the buyer [SIM] and a judge-sandbox pool, sharing five linked libraries, all Etherscan-verified and checked against the chain by `contracts/script/check_pool_record.py`), on Anvil and in tests; `SuretyBond` runs on Anvil only. When k observer organisations attest a run of
**qualified clean passes** at a source after a confirmed episode, it releases a **capped, cancellable cure refund** —
never more than the rebate that episode cost, frozen while a new detection is under review, cancelled by any later
attested detection at that source.

**Construction.** `constructor(MethaneCovenant covenant)`; `registry = covenant.registry()` — the pool trusts exactly
the registry the covenant trusts. No admin, no owner, no upgrade path; `receive()` reverts. EIP-712 domain
`{name "MethaneClause", version "1", chainId, verifyingContract: pool}`; the `CleanPass` and `Corroboration` type
hashes and the verifying contract differ from the core's, so a signature cannot cross between them; kinds 4–7 are
disjoint from the core's 1–3 as a second guard.

**Pool per covenant, opened by a party.** The first `fund(covenantId, policyHash, mode, PoolParams)` must come from the
covenant's buyer or seller; it fixes `policyHash`, `mode` and the params (`paramsHash = keccak256(abi.encode(p))`,
emitted). Later calls are top-ups by anyone and must match. `fund` validates `holdWindow ≥ publicationLag +
challengeWindow + contestWindow`, `resolveWindow ≥ cleanWindow`, strictly increasing thresholds, shares summing to
10000 bp, a non-increasing upper-bound table, non-zero bonds and exactly one final installment.

**Lifecycle.** OPEN → CLOSING (from `covenant.end + publicationLag + cleanWindow`: no new passes) → CLOSED (no pending
pass, challenge or installment) → `sponsorWithdraw` (pro rata, unreserved only). No withdrawal before CLOSED.

**Claims.** `openClaim(episodeId)` by anyone, for a CONFIRMED episode of this covenant, one per episode. **`since =
detectedAt + linkWindow`**: the clean run starts when the core's own episode window closes. `cap` = the episode's actual
rebate (ONCHAIN) or the tier's amount in policy units (VERDICT_ONLY); a zero cap reverts.

**Clean-pass attestation.** `attestCleanPass(claimId, CleanPass[], sigs)` with exactly the pass bond: ≥ k distinct
observer organisations outside {seller, buyer}; valid keys; kind CLEAN; pool nonce bitmap; expiry within a day; all
attestations equal; `notCloudy`; instrument floor ≤ the episode's rate; `since < sceneAt ≤ publishedAt ≤ now`;
`now ≥ sceneAt + publicationLag`; `sceneAt < covenant.end`; pool OPEN, claim OPEN, scene not seen before, fewer than
32 pending passes; no later live episode at the source.

**Counting.** `countPass` after the clean window, unchallenged: the pass counts, the submitter's bond returns, and an
**observer fee** is split among the controllers of the organisations that signed (ONCHAIN, from the unreserved balance;
[SIM] amount).

**Challenge of a clean pass** (objective, re-checkable): `challengePass(passId, reason)` with the challenger bond
inside the window. Reasons: 1 `CLOUD_FLAG`, 2 `NO_ORIGIN_INTERSECT`, 3 `PLUME_ON_SCENE` (includes late publication),
4 `SCENE_NOT_FOUND`, 5 `MARS_PLUME_NEARBY`, 6 `FLOOR_MISMATCH`. Observers answer within `resolveWindow` with k
CONFIRM_CLEAN (the pass counts; the challenger's bond goes to the covenant seller) or k WITHDRAW_CLEAN (the pass is
VOID; the pass bond goes to the challenger). Unanswered, `finalizePass` expires it, both bonds return, nobody is
slashed.

**Cross-provider corroboration.** `attestCorroboration(claimId, Corroboration[], sigs)`: k organisations attest that
the pinned UNEP MARS snapshot (`marsSnapshotHash`) shows no MARS plume at the facility (1.5 km) between `windowFrom ≤
since` and `windowTo ≥` the latest counted scene. It is pending for one clean window, challengeable on reason 5, made
effective by `finalizeCorroboration` (or by a fresh k-org answer if challenged). **The final installment needs it**, so
the largest share pays only when a second provider, from other satellites, also saw nothing.

**Installments** (policy values): after 3 counted passes 30 %, after 6 another 30 %, after 10 the final 40 % — the
final one also needs two instruments, the corroboration, and the cure term. `amount = cap × share`; if the pool is short
it waits as UNFUNDED and schedules on the next top-up. `claimableAt = now + holdWindow`; for the final share also
`≥ since + cureTerm + relapseGrace` (grace = publication lag + challenge + contest windows), so it pays only once a
relapse inside the cure term would already be publishable and confirmable. The final share is scheduled only if that
date fits `covenant.end + publicationLag`; otherwise the claim is capped at the non-final shares (`FinalOutOfTerm`).
`release(installmentId)` by anyone: ONCHAIN credits the covenant seller (pull); VERDICT_ONLY emits `CreditNoteDue` in
policy units and moves no funds.

**Source under review / relapse.** Before `attestCleanPass`, `countPass` and `release` the pool reads the core's
`latestEpisode` at the source; a different episode detected after `since` that is PROPOSED / CHALLENGED / CONTESTED
reverts `SourceUnderReview`; CONFIRMED / EXPIRED reverts `RelapsePending`; VOID is ignored. `relapse(claimId,
episodeId2)` by anyone, for a CONFIRMED or EXPIRED episode at the source detected after `since`, is O(1): the claim is
CANCELLED, pending installments are voided and their reserve released; pending passes are voided lazily by
`voidPass`. **Released installments stay released** (decision D1); the final 40 % is withheld until the cure term plus
grace, so a relapse inside the term forfeits the largest share. A new claim may open on the relapse episode once it is
CONFIRMED.

**Settlement modes.** ONCHAIN: refunds in testnet ETH to the seller. VERDICT_ONLY: cap, reserved and paid are policy
units with no balance; pass and challenger bonds still move testnet ETH as anti-spam deposits (fiat escrow in a
production deployment where crypto payment is not lawful).

**Correction.** Nothing is edited: voided or cancelled passes and installments stay on chain with reasons; credit-note
supersession is an append-only, off-chain export step (the on-chain `CreditNoteSuperseded` event is declared and
reserved, never emitted).

**Interfaces** (structs and type strings; the full surface — functions, events, errors — is declared on
`contracts/src/IncentivePool.sol` and pinned by `IncentivePoolSurfaceTest`):

```
CleanPass(bytes32 claimId,bytes32 sourceId,bytes32 sceneId,bytes32 sceneHash,uint64 sceneAt,uint64 publishedAt,
          uint8 instrumentId,bytes32 providerId,uint16 cloudBp,bool notCloudy,uint32 floorKgH,uint32 rateFloorKgH,
          uint8 kind,uint64 nonce,uint64 expiry)            kinds: 4 CLEAN, 5 CONFIRM_CLEAN, 6 WITHDRAW_CLEAN
Corroboration(bytes32 claimId,bytes32 sourceId,bytes32 providerId,bytes32 marsSnapshotHash,uint64 windowFrom,
              uint64 windowTo,uint16 marsPlumesInWindow,uint8 kind,uint64 nonce,uint64 expiry)   kind 7; plumes = 0
sceneId = keccak256(provider scene id); sceneHash = SHA-256(JCS(clean-pass bundle))

fund · sponsorWithdraw · closeClaim · openClaim · attestCleanPass · attestCorroboration · challengeCorroboration ·
finalizeCorroboration · challengePass · resolvePass · countPass · finalizePass · voidPass · release · relapse ·
withdraw · getPool · getClaim · getPass · getInstallment · contributions · cleanPassDigest · corroborationDigest ·
eip712Domain
```

To stay under EIP-170, part of the pool's logic lives in five linked libraries (`PoolVerify`, `PoolSetup`,
`PoolEpisodes`, `PoolCorroboration`, `PoolInstallments`) called by DELEGATECALL on the pool's storage; the deployer's
choice of library addresses is a trust point (no public deployment, so no address record, exists yet).

**Pool invariants** (stateful fuzz over core and pool actions): P1 `paid + reserved ≤ cap` per claim, Σ reserved ≤
balance; P2 no counted pass without k distinct organisations, no final share without two instruments, the cure term
and a covering corroboration; P3 nothing scheduled or released after a cancel; P4 ETH conservation; P5 every pending
item ends through a permissionless O(1) call; P6 the pool never moves core funds or changes core state; P7 no
signature crosses between core and pool or across chains; P8 no pass before `sceneAt + publicationLag`; P9 no
attestation, count or release past a later non-VOID detection.

## 12. The measured unit and qualified clean passes

A pass that sees no plume bounds how **often** the source emits above the instrument's floor, not the rate. The clause
reports **detection frequency above the floor** and its upper bound:

- `u(N) = 1 − 0.025^(1/N)`, the upper limit of the two-sided 95 % Clopper–Pearson interval after N qualified passes
  and 0 detections (the table for N = 1..32 is `upperBoundBp` in `data/derived/policy_v2.json`). It treats qualified passes as
  independent draws; clustered or tasked passes make the effective N smaller, so the bound is optimistic.
- **Floor per instrument:** a pass counts only if its instrument's published detection limit is ≤ the episode's attested
  rate. Values and sources: [`data/derived/floors.json`](../data/derived/floors.json) (Tanager at the conservative end
  of its published 90 % range; EMIT at its 90 % probability of detection, read from a secondary source, said there).
  The card names the maximum floor among the counted instruments.
- Card sentence: *"After N qualified clean passes (Tanager + EMIT), the frequency of plumes above F kg/h at this source
  is at most u(N); before the episode it was d0. Observed, not abated."*
- **Why the refund is capped:** the catalogue figure generated into `data/derived/cleanruns.json` `card.sentence` (a quiet run is
  weak evidence; the pass after three non-detections still often saw a plume) — a reason for the cap, not evidence of
  repair.

**Qualification of a pass** (observer rule, `pipeline/cleanpass_rules.py`; every field signed inside the bundle):
1. **Cloud:** Carbon Mapper's own `not_cloudy` flag must be true; both cloud fields are carried and must agree with it.
2. **Intersect:** the scene footprint contains a plume-origin point of the facility's linked sources that was already
   published when the pass could first be attested (no look-ahead).
3. **No plume at the source:** the per-source query for the scene returns no plume, and no plume on the scene lies
   within 1.5 km of the facility.
4. **Second provider against the negative:** no UNEP MARS plume at the facility within ± 7 days of the scene.
5. **Time:** `since < sceneAt ≤ publishedAt ≤ now`, `now ≥ sceneAt + publicationLag`, `sceneAt < covenant.end`.
6. **Floor:** instrument floor ≤ the episode's rate.

Refusal codes: `CLOUD_FLAG`, `CLOUD_FLAG_MISMATCH`, `NO_ORIGIN_INTERSECT`, `PLUME_ON_SCENE`, `MARS_PLUME_NEARBY`,
`SCENE_BEFORE_SINCE`, `SCENE_NOT_PUBLISHED`, `BEFORE_PUBLICATION_LAG`, `AFTER_TERM`, `DUPLICATE_SCENE`,
`UNKNOWN_INSTRUMENT`, `FLOOR_MISMATCH`, `FUTURE`; plus observer-side `SCHEMA`, `BUNDLE_POLICY_MISMATCH`,
`RECHECK_DIFFERS`. A refused scene is never signed. Every count is a lower bound: rules 3 and 4 are checked against
today's catalogue and the pinned MARS snapshot, which can only refuse more. DZA_S_012 has 8 qualified clean passes
before its relapse (Tanager 4, EMIT 4); TKM_S_276 has no attestable clean pass yet (`clean_passes` in
`data/derived/audit_numbers.json`).

## 13. Policy v2

`pipeline/policy.py` → [`data/derived/policy_v2.json`](../data/derived/policy_v2.json), RFC 8785 + SHA-256. The
`core` block is the v1 policy, unchanged; the `pool` block holds the settlement mode, publication lag (60 days), clean
window (1 day), resolve window (7 days), hold window (64 days), cure term (540 days), pass and challenger bonds, the
observer fee [SIM], verdict-only tier amounts (always written in full as policy units),
the installment rules, the u(N) table, the instrument floors, the cloud rule and the MARS window;
`onchainParamsHash = keccak256(abi.encode(PoolParams))`. `make check` binds each deployed pool's `paramsHash` and mode
to the policy. Separate policies exist for the DZA replay (own start and term, local chain only), the sandbox (short
windows, amounts / 100) and verdict-only covenants. GWP-20 is kept out of every hashed policy, so a later correction
cannot change a pool's hash.

**Clean-pass bundle** (hashed → `sceneHash`): schema version, covenant, claim, source, provider and scene ids,
instrument, platform, scene and publication times, both cloud fields and the flag, footprint bounds, origin points
and whether they intersect, the per-source queries with their response hashes and plume counts, plumes near the
facility, the MARS rows within the window, sensitivity mode, solar zenith angle, off-nadir angle, GSD, floors, and the
scene record's hash. `retrievedAt` and raw bytes stay in each observer's receipt.

## 14. Settlement per jurisdiction

| Covenant | Core rebate | Pool refund | Where |
|---|---|---|---|
| Main (Sepolia; buyer "importer [SIM]") | testnet ETH to the buyer | ONCHAIN | live testnet; judge walk-through |
| DZA replay (EU importer [SIM]) | testnet ETH | ONCHAIN, testnet ETH to the seller | Anvil `make demo` |
| TKM verdict-only (Türkiye-type importer [SIM]) | tier amounts 0, no bond: CONFIRMED **is** the verdict | VERDICT_ONLY: `CreditNoteDue` + credit-note export | Anvil |

Three layers: evidence → verdict → settlement. The non-negotiable property is the unsuppressible multi-party verdict;
atomic settlement is the second property, available where lawful. In the EU a MiCA euro e-money token is electronic
money, so the refund could be denominated in one where lawful (no token is built). In Türkiye the central bank's 2021
regulation bans crypto-assets in payments, directly or indirectly, so a Turkish counterparty settles by bank transfer
or credit note referencing the on-chain verdict: `pipeline/export/credit_note.py` writes it ("settle under clause X of
the supply contract by bank transfer or credit note; this is not a payment instrument"). Not legal advice. AML / KYC on
paying a third-country seller is the sponsor's obligation, outside the contract.

## 15. EU and regulator mapping; corroboration badges

- The Annex D export gains `x-methaneclause.euEvidence`: which Annex IX items of Regulation (EU) 2024/1787 the record
  can support (each `yes` / `partly` / `no` with a reason) — evidence, never a statement of compliance.
- **Annex C as the seller's voice:** `recordMitigationClaim(claimHash)` takes the SHA-256 of the JCS IEA Annex C
  feedback form; Annex D cites it as "seller's claim, hash on chain, state unchanged".
- **MARS vocabulary:** a CONFIRMED episode ↔ MARS's "Ongoing"; a claim with ≥ 3 counted passes ↔ "Potentially
  ceased" — MARS's terms, cited; MARS's own field is never filled.
- **Corroboration badges for detections** (off chain): L1 another Carbon Mapper instrument within ± 7 days (a cadence
  badge); L2 a MARS plume from a satellite other than EMIT and Tanager within ± 7 days and 1.5 km. On chain, the final
  installment needs two instruments and a k-of-n MARS corroboration; a cross-provider gate on core payouts would need
  a core change (roadmap).
- Related standards are context only: IEEE 2418.5-2025, IEEE 3218-2022, IEEE P3271.01 (recurring payments follow its
  non-custodial pull pattern); the prototype conforms to none of them.

## 16. Backtest and projections

`pipeline/backtest.py` → [`data/derived/backtest.json`](../data/derived/backtest.json): a counterfactual walk over the
1,122 sources MARS labels absent today — a label decided after the window — from each source's first MARS plume to the
catalogue end, with the policy frozen. Label: *"counterfactual: if these sources had been under the clause; no clause
existed."* Amounts are in policy units (a parameter, not a price), never a currency. Refunds are counted only where
scene data was fetched. Stake: only the lower bound is quoted ("stake, not abatement"); mid and high stay in the JSON.
The only quotable sentences are generated into `card` (episodes, refunds, stake, L1, L2) and each names the selection.
**Portfolio covenant [SIM]:** `data/derived/sourceset_v3.json` over the join candidates plus the demo cases, sized by
`model/bond.py` (bond = pool = the P90 of one year's rebate exposure; sentence in `data/derived/model/bond_sizing_v3.json` → `card`).

## 17. Surety and observer governance

**`contracts/src/SuretyBond.sol`.** Immutable `operator` (the real seller), `funder` (the surety), `covenant`, `pool`. Its address is
registered as the seller organisation's SELLER key by that organisation's controller; it never signs. The operator may
only `createCovenant`, `contest` and `recordMitigationClaim` through it; `pull()` (anyone) brings its credits home from
the core and the pool; only the funder can `withdraw` or `revokeSelf`. The bond can be posted by a surety; settlement
and refunds are assigned to it on chain.

**`contracts/src/KeyRegistryV2.sol` (local chain only).** A registry without an admin and with the v1 view interface, so the
unchanged core and the pool run on it. A new observer organisation is admitted when `max(k, ⌊n/2⌋ + 1)` member
controllers vote; its keys are valid from admission plus a delay; a symmetric vote removes a member and closes its
keys; an organisation in quarantine after a recent revocation cannot vote; proposals expire. Limit: admission changes
the trusted set of every covenant on the registry; a production clause pins its observer set per covenant (not built).
Not deployed to Sepolia (the main registry's admin is renounced).

**Open observer slot (sandbox).** An organisation with a published controller lets anyone register a key; a signature
from it is shown with its registration transaction and never called "independent" on our own observers.

## 18. Measurements

- Gas per function, cost at a stated gas price, p50 / p90 confirmation latency, and every transaction outcome on Sepolia:
  [`data/derived/measurements.json`](../data/derived/measurements.json) (`make measure`).
- Verification time, manual protocol vs tool, author-measured, not a user study:
  [`data/derived/verification_time.json`](../data/derived/verification_time.json).
- Footprint: [`FOOTPRINT.md`](FOOTPRINT.md).
- Slither report with every finding triaged: [`contracts/slither.md`](../contracts/slither.md) (`make slither`). Halmos and Z3 results with paths, times and bounds: [`contracts/halmos/REPORT.md`](../contracts/halmos/REPORT.md) (`make halmos`). Which property each method covers: [`contracts/README.md`](../contracts/README.md), "What is proven, and by which method".

## 19. Limits

- Observers attest what they received, from whom and when — not what the satellite saw. Carbon Mapper and UNEP do not
  sign their data.
- In production each observer organisation is independent and holds its own keys. In this prototype the observer
  organisations are run by one person [SIM]; the judge's own wallet is the one signer we do not control.
- Non-detection is not repair; the refund is capped at the rebate and cancellable; released installments are not
  clawed back.
- Floor blindness both ways: a source just below the floor is invisible to rebate and refund.
- Clean passes come from one provider's scene catalogue: k-of-n guards against one observer's error or key, not against
  provider error; MARS is used against the negative. The chain cannot verify clouds, only that k organisations signed
  the same named fields.
- Tasking: a seller could request passes during planned shutdowns, which is why the final share needs two instruments,
  a MARS corroboration and the cure term.
- Re-quantification never edits a CONFIRMED episode. Scenes published after `end + publicationLag + cleanWindow` are
  lost. A relapse is read from the core's `latestEpisode`, so the sponsor watches and reports relapses.
- The DZA replay runs on a pinned clock: every DZA date was public before that covenant was written; the replay proves
  the mechanism, not the absence of foreknowledge. On Sepolia `createdAt` is real.
- Data licence: non-commercial research prototype; production needs a commercial data agreement with Carbon Mapper,
  held by the observers.
- The on-chain signature check is secp256k1 ECDSA (section 7). Money is testnet ETH.
- The records are tamper-evident, never "tamper-proof".
- Residual risks in detail: [SECURITY.md section 15](SECURITY.md#15-residual-risk).

## 20. Decisions

| # | Decision | Taken |
|---|---|---|
| D1 | Released installments after a relapse | stay released; the final 40 % pays only after the cure term + grace |
| D2 | Who buys TKM's gas | the Sepolia main covenant's buyer is "importer [SIM]"; TKM's credit-note arc runs in a separate verdict-only covenant; DZA carries the EU story |
| D3 | Clean-pass cloud rule | Carbon Mapper's own `not_cloudy` flag |
| D4 | Installment thresholds | 3 / 6 / 10, fixed, not tuned to any case |
| D5 | Cross-provider check | MARS against every clean pass (refusal and challenge reason) and a k-of-n MARS corroboration before the final installment; L1 / L2 badges for detections; a gate on core payouts is roadmap |
| D6 | Who pays the witnesses | an observer fee per counted clean pass; for core detections, roadmap |
| D8 | AI-assistance disclosure | yes, one paragraph (README, [ETHICS.md](ETHICS.md)) |
| D11 | Slashing observers of a withdrawn clean pass | no: slashing only on objective errors, organisational identity |

Recorded deviations from earlier text, with what they cost, are in the "Decisions that differ from the spec" sections of
[`contracts/README.md`](../contracts/README.md), [`pipeline/README.md`](../pipeline/README.md),
[`pipeline/observer/README.md`](../pipeline/observer/README.md) and [`pipeline/export/README.md`](../pipeline/export/README.md).
Source comments that cite "spec-v2 §n" refer to the build-spec numbering; section 21 maps it.

## 21. Section map (build-spec numbering → this document)

| Build spec | Here |
|---|---|
| v1 §1–§8 | §1–§8 |
| v1 §9 (storyboard) | §9 (demo runs) |
| v1 §10 | §10 |
| v2 §0 | §1, §11 |
| v2 §2 | §12 |
| v2 §3, §3.1 | §11 |
| v2 §4 | §13 |
| v2 §5 | §14 |
| v2 §6 | §15 |
| v2 §7 | §16 |
| v2 §8 | §8 |
| v2 §8.8 | §19 |
| v2 §9.1, §9.5 | §18 |
| v2 §9.3 | §10, §18 |
| v2 §9.4 | §17 |
| v2 §9.7 | §10 ([SECURITY.md](SECURITY.md) section 4) |
| v2 §10 | §9 |
| v2 §16 | §20 |
