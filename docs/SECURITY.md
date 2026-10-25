# Security and data-integrity assessment

MethaneClause is a methane clause for gas supply contracts that executes itself. The seller posts a bond for named
sources. When k observer organisations independently attest a satellite re-detection at one of them, the buyer's
rebate moves out of the bond after a challenge window (the core: `KeyRegistry`, `MethaneCovenant`). The two-sided
extension (`IncentivePool`) pays the seller a capped, cancellable cure refund when k observer organisations attest a
run of qualified clean passes after a confirmed episode. The next attested detection cancels what is still unpaid.
`SuretyBond` lets a surety post the bond; `KeyRegistryV2` (local chain only) lets observer organisations admit and
remove members by majority vote.

This document follows the ISSRM structure (asset → threat → vulnerability → risk treatment → control → test). Every
control names the test that exercises it; every number names the file it was read from. Specification:
[`docs/spec.md`](spec.md). Contract decisions and error choices: [`contracts/README.md`](../contracts/README.md).
The post-quantum boundary is in section 9.

**At a glance.**
- **Signatures:** EIP-712 typed data, secp256k1 ECDSA (65 bytes, low `s` only) checked on chain; hybrid observers
  (algId 2) add ML-DSA-44 (FIPS 204), checked off chain against the key hash anchored in `KeyRegistry` (sections 6, 8,
  9; `test_tamperedEvidenceHash_reverts`, `test_verify_bundle_rejects_foreign_mldsa_key`).
- **Replay and freshness:** chainId + verifyingContract in the EIP-712 domain, a per-key nonce bitmap,
  `now ≤ expiry ≤ now + 1 day`; one bad attestation reverts the whole call (section 7; `test_replay_reusedNonce_reverts`,
  `test_crossChainReplay_reverts`, `test_crossContractReplay_reverts`, `test_expired_reverts`).
- **Admin:** no contract has an owner, an upgrade path or a pause; the registry admin is renounced on Sepolia:
  [`VERIFY`](VERIFY.md) step 4 reads `admin()` live, and the deployment record has `registryAdmin` = 0x0
  (`contracts/deployments/11155111.json`); no function moves funds as an administrator (section 10;
  `invariant_I7_noAdminPath`).
- **Assurance:** Foundry stateful-fuzz invariants I1–I7 (core), P1–P9 (pool) and KeyRegistryV2's three, a Slither
  triage with 0 High, bounded Halmos runs (section 13).
- **Post-quantum boundary:** on-chain identity stays secp256k1 ECDSA; ML-DSA-44 protects the long-term authenticity of
  bundles verified off chain (verbatim statement in section 9).
- **Questions a reviewer asks first:** section 16.

**What the system vouches for.** Observers attest *what they received, from whom and when* — the provider response,
its content hash, the derived fields — not what the satellite saw. A hash commits to data; it does not make the data
true. The chain enforces process and money; the truth of the data rests on independent observers and an open
challenge. Non-detection never cancels a rebate or counts as repair; it can release a capped refund that the next
attested detection cancels.

## 1. Do we need a ledger?

### The organizer's four questions

| Question | Answer |
|---|---|
| Multiple parties? | Seller, buyer, a sponsor, k of n observer organisations named in the clause (n ≥ k + 2; none is the seller's or the buyer's), open challengers; for the refund also sponsors and, optionally, a surety. |
| No trusted operator? | None exists for this function. Registries (MiQ, JOGMEC CLEAN, UNEP MARS) certify and notify but cannot move a seller's money; a bank can move money but cannot adjudicate satellite evidence; a court can do both and takes years. Seller and buyer are counterparties: neither accepts an operator chosen by the other, so the decision has to come from k independent observers plus an open challenge window — which is what the contract executes. The registries stay inputs. |
| Outside verification? | Anyone can re-fetch Carbon Mapper, recompute the canonical bundle hash (RFC 8785 JCS + SHA-256) and challenge an episode or a clean pass with an objective reason code. |
| Fits the ledger? | Only hashes, identifiers and money: `evidenceHash`, `sceneHash`, `policyHash`, the Merkle root of covered facilities, bonds, rebates and refunds. Raw data stays with the providers and the observers. |

### Walking the Wüst–Gervais flowchart

| Step | Answer | Consequence |
|---|---|---|
| Is state stored? | Yes: covenants, episodes, claims, passes, installments, balances. | Continue. |
| Are there multiple writers? | Yes: observers attest, anyone proposes or challenges, seller contests, sponsors fund. | Continue. |
| Can an always-online trusted third party be used? | No: see the operator gap above. | A ledger. |
| Are all writers known? | Observer organisations and the two parties are registered (`KeyRegistry`); proposers, challengers and sponsors are anyone. | A permissioned design is possible for the observers. |
| Are all writers trusted? | No: the seller has a motive to suppress a detection, the buyer a motive to withhold a refund. | Public verifiability matters. |
| Is public verifiability required? | Yes: the counterparty, a lender and a regulator must check the verdict without asking either party. | **Public, permissioned-by-registry**: a public PoS chain whose quorum is a registered observer set. |

**Chain chosen:** Ethereum Sepolia (PoS, public, chainId 11155111; addresses in
[`contracts/deployments/11155111.json`](../contracts/deployments/11155111.json)) plus local Anvil for the full replay.
A judge can run it with a faucet drip and their own wallet.

**Where each contract runs today:**

| Contract | Ethereum Sepolia (public) | Anvil (local, `make demo`, `make judge-local`) |
|---|---|---|
| `KeyRegistry`, `MethaneCovenant` (the core) | **live, Etherscan-verified**: the main pair and the judge-sandbox pair, addresses in `contracts/deployments/11155111.json` | yes |
| `IncentivePool` and its five linked libraries | **live, Etherscan-verified**: the main pool (opened by the buyer [SIM] under `POLICY_V2`) and the judge-sandbox pool share the five libraries; `contracts/script/check_pool_record.py` matched each library's code and each pool's link bytes against the chain | yes |
| `SuretyBond` | not deployed (local chain only: the DZA replay in `make demo`) | yes |
| `KeyRegistryV2` | not deployed (the main registry's admin is renounced; V2 is local only by design) | yes |
| Browser wallet (`app/wallet.js`), not a contract | Sepolia judge sandbox only: the page derives the published [SIM] sandbox keys from their public strings; the judge's own key stays in their wallet (the page asks it to sign typed data and send transactions); without an explicit network override it refuses any chain but Sepolia and any sandbox address other than the three it pins | yes (`make wallet-local`) |

Everything this document says about the surety and V2 is shown by tests and by the local runs. The pool's code is on
Sepolia, and one clean pass was attested and counted there (judge sandbox); its release, freeze and relapse paths are
exercised by tests and by the local runs. The same design maps to Hyperledger Fabric (organisations = seller, buyer,
observers; endorsement policy = k-of-n observers). There no native asset settles atomically, so the verdict stays on the
ledger and settlement moves to a bank transfer (README, "Production path"; `docs/PRODUCTION.md`, "Chain choice").

### Signed database vs chain: what each party can do

A signed database proves what its owner inserted. It cannot prove what its owner left out, and it does not move money.

| Action | Signed DB run by the seller | Signed DB run by the buyer | Escrow bank or registry | This contract |
|---|---|---|---|---|
| Drop a re-detection | Yes: never insert it, or delete the row | No motive | Yes, if persuaded; it cannot judge the evidence | **No.** Anyone holding k observer signatures calls `proposeEpisode`; no function deletes an episode (`invariant_I5_noStuckState`, `invariant_I6_mitigationClaimInert`) |
| Withhold the rebate | Yes: payment is a separate act | — | Yes: it holds the money | **No.** After the window anyone calls `finalize`; the rebate is credited to the buyer (pull). What the seller can do hands the decision to independent observers. `contest` needs k + 1 of them to confirm (`test_contest_confirmedByKPlusOne`); an objective `challenge` (open to anyone, with a bond that goes to the buyer when k confirm) needs k. The observers answer within the window, and the production quorum keeps a spare organisation for it (section 15) |
| Withhold a cure refund | — | Yes | Yes | **No.** `countPass` and `release` are permissionless; the refund is credited to the seller (`test_final_holdBranch_releasedEndToEnd`) |
| Change the policy after the fact | Yes | Yes | Yes | **No.** `policyHash` is fixed at `createCovenant`, before any evidence; the pool's `paramsHash` is fixed by the first `fund` and every top-up must match it (`test_fund_laterMismatch_reverts`); no setter exists |
| Rewrite a past acceptance | Yes | Yes | Yes | **No.** A later detection is LINKED or opens a new episode with `parentEpisode`; a CONFIRMED episode never changes (`test_detectionAfterLinkWindow_opensNewEpisodeWithParent`, `test_linkedDetection_paysNothing`) |
| Attest its own source | Yes | Yes | — | **No.** Keys of the seller's or buyer's organisation are never counted (`invariant_I2_noSelfAttestation`) |
| Move the funds as administrator | Owner | Owner | Yes | **No.** No admin path; the registry admin is renounced after setup (`registryAdmin` is the zero address in the deployment record; `invariant_I7_noAdminPath`, `invariant_P6_coreUntouched`) |

## 2. System model

### Data flow and trust boundaries

```
 TB1 ─ sensor and space link (provider's trust) ─────────────────────────────────────────────────────────────────
   Tanager / EMIT / airborne ──► Carbon Mapper            Sentinel-2 / Landsat / EMIT ... ──► UNEP IMEO MARS
 TB2 ─ provider APIs: keyless, unsigned; we hash what we received ──────────────────────────────────────────────
   api.carbonmapper.org (plumes, sources, scenes)          MARS public GeoJSON zip (pinned SHA-256)
          │                                                         │
 TB3 ─ one boundary per observer organisation: its host, its keys ───────────────────────────────────────────────
   observer OBS_A ─┐  Fetch ─► Validate ─► Seal (JCS bytes, SHA-256) ─► Sign (EIP-712 ECDSA + ML-DSA-44) ─► Re-check
   observer OBS_B ─┼─ k identical bundles ⇒ k signatures over one evidenceHash / sceneHash; disagreement ⇒ no quorum
   observer OBS_C ─┘  receipts (retrievedAt, raw-bytes hash) stay with each observer
          │
 TB4 ─ public mempool and chain: anyone submits; the contract re-checks everything ─────────────────────────────
   relayer / proposer / challenger / sponsor (any wallet)
          ▼
   KeyRegistry ◄── MethaneCovenant (core, unchanged) ◄── IncentivePool (reads the core, never writes it)
   [Sepolia + Anvil]   [Sepolia + Anvil]                     [Sepolia + Anvil, five linked libraries]
                         ▲                                    ▲
                    SuretyBond (seller address of a covenant; credits only the surety)   [Anvil only]
                    KeyRegistryV2 (governance variant of the registry)                   [Anvil only]
          │
 TB5 ─ the reader's browser: the page recomputes hashes and checks both signatures locally; in the judge sandbox
       [SIM] it also derives the published sandbox keys and sends with them, and asks the judge's wallet to sign ──────
   app/ (map, covenant card, tamper buttons, Annex D export with round trip)     regulator / lender / auditor
   app/wallet.js + the judge's EIP-1193 wallet (key stays in the wallet)  →  judge sandbox on Sepolia only
   the page's modules come from cdn.jsdelivr.net, version-pinned; Leaflet, MapLibre and the ML-DSA-44 verifier
   (@noble/post-quantum and its @noble imports, an import map with sha384 per module) load under subresource
   integrity, so an edited copy of those files is refused; viem (ECDSA recovery, the live read, wallet.js for "Become
   a witness") loads as a CDN-generated bundle, which is not byte-stable, so it carries no integrity hash, and it runs
   in the same JavaScript context as the checks: the page is a convenience view, and `scripts/verify_bundle.py --rpc`
   is the authoritative verifier (served copy of viem with its hash: PRODUCTION.md item 7)
```

*We attest what we received, from whom and when; not the space link, not the sensor.*
That trust stays with Carbon Mapper and UNEP, who do not sign their data. Each bundle keeps the query URL, parameters
and the SHA-256 of the provider response, so anyone can re-fetch and compare.

### Actors

| Actor | Can | Cannot |
|---|---|---|
| Seller (bonded) | create a covenant, fund it, `contest` an episode, record a mitigation claim (inert), receive cure refunds and the remaining bond at term | sign as an observer of its own covenant, delete or edit an episode, take the bond back during the term |
| Buyer | receive rebates, open the pool, challenge a clean pass | sign as an observer, block a counted refund |
| Observer organisation (k of n) | sign detections, clean passes, corroborations, CONFIRM / WITHDRAW answers; rotate and revoke its own keys | move money; act alone (k distinct organisations are required) |
| Proposer / relayer / challenger | submit signed batches with a bond; challenge with an objective reason code and a bond | submit anything the signatures do not cover |
| Sponsor (lender, programme, buyer) | top up a pool; withdraw its unreserved share pro rata after CLOSED | withdraw before CLOSED, touch reserved installments |
| Surety (`SuretyBond` funder) | fund the bond, withdraw what the bond contract received, revoke the bond's SELLER key | sign anything |
| Deployer | deploy, register the initial keys, choose the linked library addresses, renounce the registry admin | move funds (no admin path) |
| Attackers considered | external attacker, colluding observers, a seller that bribes observers, griefer / Sybil challenger, compromised observer host, holder of a published sandbox key, future quantum adversary | — |

## 3. Assets

| Asset | Where | Who writes | Integrity mechanism |
|---|---|---|---|
| Evidence bundle (detection) | off-chain, each observer; hash on chain | observers | RFC 8785 JCS → SHA-256 `evidenceHash` inside the EIP-712 `Attestation`; ML-DSA-44 over the same bytes |
| Clean-pass bundle | off-chain; hash on chain | observers | JCS → SHA-256 `sceneHash` inside the EIP-712 `CleanPass` |
| MARS corroboration | off-chain snapshot; hash on chain | observers | pinned `marsSnapshotHash` inside the EIP-712 `Corroboration` |
| Policy | off-chain JSON; hash on chain | seller at creation; pool opener | `policyHash` (SHA-256 of JCS) fixed before evidence; `paramsHash = keccak256(abi.encode(PoolParams))` |
| Covered facilities | Merkle root on chain | seller at creation | sorted-pair Merkle proof per proposal (`NotCovered`) |
| Keys and roles | `KeyRegistry` | org controllers (admin only before renouncing) | full history; validity checked at submission time |
| Bond, rebates, refunds, bonds of proposers and challengers | contracts | the state machines only | pull payments; conservation invariants I4 and P4 |
| Episode / claim / pass / installment state | contracts | permissionless transitions with deadlines | invariants I1–I7, P1–P9 |

## 4. ISSRM risk register (STRIDE-elicited)

Severity is our own estimate (H / M / L), not a CVSS score. "Test" names exist in `contracts/test` (Foundry) or
`pipeline/tests` (pytest). `make test` runs them all; `make demo` repeats the core rejections as real failed
transactions (receipt status 0).

### Core (v1)

| Row | STRIDE | Asset | Threat | Vulnerability | Sev. | Treatment and control | Test |
|---|---|---|---|---|---|---|---|
| R1 | T | evidence bundle | bundle edited after attesting (2,548 → 548 kg/h in the demo) | off-chain storage is mutable | H | Reduce: JCS + SHA-256 `evidenceHash` signed in EIP-712; any edit changes the digest and the recovered signer | `test_tamperedEvidenceHash_reverts`, `test_tamperedEmission_reverts`, `test_any_edit_changes_hash`; `make demo` tamper step |
| R2 | S/T | attestation | replay: same nonce, same batch, other chain, other contract, stale, rotated key | signatures without nonce / domain / expiry | H | Reduce: per-key nonce bitmap, `expiry ≤ now + 1 day`, EIP-712 domain binds chainId + verifyingContract, low-s only, key validity at submission | `test_replay_reusedNonce_reverts`, `test_replay_sameAttestationTwiceInBatch_reverts`, `test_crossChainReplay_reverts`, `test_crossContractReplay_reverts`, `test_expired_reverts`, `test_expiryTooFar_reverts`, `test_rotatedOldKey_reverts_newKeyAccepted`, `test_malformedAndHighSSignature_reverts` |
| R3 | E | quorum | seller or buyer attests its own source; two keys of one org count twice | role confusion | H | Avoid: org ∉ {sellerOrg, buyerOrg}; quorum counts distinct `orgId`s | `invariant_I1_quorum`, `invariant_I2_noSelfAttestation`, `test_selfAttestation_sellerKey_reverts`, `test_selfAttestation_sellerOrgObserver_reverts`, `test_selfAttestation_buyerOrg_reverts`, `test_quorumNotMet_twoKeysSameOrg_reverts`, `test_attestorNotIndependent_nonObserverRole_reverts` |
| R4 | D | timeliness | fetcher, provider or RPC outage; a retry duplicates a proposal | one fetcher, one node | M | Reduce + retain: windows run on chain time, not our uptime; any party finalizes after a deadline; identical resubmission rejected; HTTP retries with back-off (`pipeline/common.py`) | `invariant_I5_noStuckState`, `test_duplicateEvidence_isIdempotent`, `test_finalize_guards`; `make demo` "Resubmit" step |
| R5 | S/T | input truth | spoofed or poisoned provider data; unit swap; slow drift | we cannot authenticate the sensor | H | Reduce + transfer: validation before signing, k distinct fetchers, provider re-fetch hash; residual trust stays with the provider | `test_bad_input_refused` (parametrised), `test_adversarial_set_all_caught_and_slow_drift_is_a_hash_catch`, `test_observer_disagreement_gives_different_hash`, `test_observerDisagreement_reverts`; detection rate in section 11 |
| R6 | S | observer keys | key theft | key on a laptop | H | Reduce: one key per role and org, rotation with history, revocation by the controller or by the key itself, validity at submission, secrets scan | `test_revokedKey_reverts`, `test_revoke_bySelf_controller`, `test_rotate_keepsHistory`, `test_rotatedOldKey_reverts_newKeyAccepted`, `test_anvil_keys_refused_off_anvil`, `test_anvil_key_in_env_refused_off_anvil`, `test_revokedFirstKey_staysController_registersNewObserver`, `test_v2_removal_closesKeys`; `make secrets` (gitleaks) |
| R7 | S | long-term authenticity | a future quantum computer forges ECDSA | secp256k1 falls to Shor | M (long-term) | Reduce + retain: ML-DSA-44 (FIPS 204) over the same JCS bytes, `pqPubKeyHash` anchored in the registry and checked against it; ECDSA transaction identity retained as a stated risk (section 9) | `test_sign_and_verify_both_halves`, `test_verify_bundle_rejects_foreign_mldsa_key`, `test_observer_signs_the_same_bytes_the_evidence_hash_commits_to`, `test_context_and_message_are_bound`, `test_pq_seed_is_not_derived_from_the_ecdsa_key` |
| R8 | D/S | challenge process | griefing or Sybil challenges and proposals | free calls | M | Reduce: exact proposer and challenger bonds; objective reason codes only; failed challenger pays the buyer; unresolved → expired, bonds returned, nobody slashed. Residual (an unanswered challenge or contest expires the rebate) and the production quorum margin: section 15 | `test_badProposerBond_reverts`, `test_challenge_guards`, `test_challenge_confirm_challengerBondToBuyer`, `test_challengeHashMismatch_withdraw_void_challengerGetsProposerBond`, `test_challenge_unresolved_expires_bondsReturned` |
| R9 | I | party confidentiality | commercial data leaks on a public chain | public state | L–M | Reduce: only hashes, ids, rates, timestamps and amounts on chain; raw responses never committed or posted | **design only** (no test proves the absence of a leak): the `Attestation` and `CleanPass` structs (section 6) carry no other fields; `test_extra_field_refused` keeps the off-chain bundle to the published schema |
| R10 | T | forecaster / model | model or rule swapped after the covenant opens | unpinned model | M | Reduce: the forecaster and its parameters are inside `policyHash` (`modelHash` null: "the rule is enough"); the live Sepolia policy hash is reproduced from the repo | `test_sepolia_covenant_policy_is_reproduced_from_the_repo`, `test_core_policy_unchanged` |
| R11 | T | attribution | plume attributed to a neighbouring facility | 1.5 km rule is geometric | M | Reduce: plume origin within 1.5 km of the facility and from a linked Carbon Mapper source; scientific disputes go to `contest`, which can hold but never slash | `test_plume_origin_radius`, `test_plume_belongs_to_the_nearest_target_only`, `test_contest_confirmedByKPlusOne`, `test_contest_expires_noSlash`; adversarial cases "plume 50 km from the facility", "CM source not linked to the facility" |
| R12 | T | record stability | provider re-quantifies a plume or renames a source | ephemeral names, versioned rates | M | Reduce: `emission_version` pinned in the bundle; detections keyed by plume id and facility; re-quantification is a new attestation that lands LINKED, never an edit | `test_linkedDetection_paysNothing`, `test_detectionAfterLinkWindow_opensNewEpisodeWithParent`, `test_bad_input_refused[missing emission_version]`, `test_committed_join_is_reproduced_from_committed_inputs` |

### Two-sided clause, surety and governance (v2)

| Row | STRIDE | Asset | Threat | Vulnerability | Sev. | Treatment and control | Test |
|---|---|---|---|---|---|---|---|
| R13 | T | clean-pass bundle | scene fields (cloud, footprint) edited after signing | off-chain bundle | H | Reduce: JCS + SHA-256 `sceneHash` in the EIP-712 struct; both ECDSA and ML-DSA-44 fail | `test_cleanPass_tamperedCloud_reverts`, `test_cleanPassDigest_matchesPython`, `test_editing_cloud_fields_after_signing_breaks_both_signatures` |
| R14 | S | pool quorum | core attestation replayed in the pool or the reverse; cross-chain | shared signer keys | H | Reduce: distinct type hash **and** distinct verifyingContract (the real guard); chainId in the domain; disjoint `kind` values (belt and braces); pool nonce bitmap; expiry ≤ 1 day | `test_pool_rejectsCoreAttestation`, `test_core_rejectsCleanPass`, `test_cleanPass_crossChain_reverts`, `testFuzz_cleanPass_foreignDomain_neverVerifies`, `test_attestCleanPass_replay_reverts`, `test_kinds_disjoint_from_core`, `invariant_P7_noReplay` |
| R15 | T/R | installment arithmetic | pass attested before the provider has published a plume on that scene | publication lag | H | Reduce: on-chain `publicationLag`; `PLUME_ON_SCENE` challenge; any later non-VOID detection freezes, a CONFIRMED or EXPIRED one cancels | `test_cleanPass_beforePublicationLag_reverts`, `invariant_P8_publicationLag`, `test_relapse_laterEpisodeWithParentPointer_cancels`, `test_relapse_linkedInsideWindow_reverts` |
| R16 | D | refund availability | sponsor or buyer challenges every pass for free | challenge cost | M | Reduce: exact challenger bond; a failed challenger's bond goes to the covenant seller; `resolveWindow` bounds the freeze | `test_challengePass_confirm_bondToSeller`, `test_challengePass_unresolved_expires_noSlash`, `test_challengePass_badBond_reverts` |
| R17 | D | cancellation | flooding PENDING passes so `relapse` runs out of gas | per-pass loops | M | Avoid: O(1) `relapse`, lazy permissionless `voidPass`, `MAX_PENDING` | `test_relapse_gasIndependentOfPendingPasses`, `test_attestCleanPass_tooManyPending_reverts`, `test_voidPass_onCancelledClaim_returnsBonds`; P5 probe (`afterInvariant` in `IncentivePool.invariants.t.sol`) |
| R18 | S/E | pool slot | a stranger opens the pool with a bad policy or mode | first-come opening | M | Avoid: first `fund` only by the covenant's buyer or seller; params hashed and emitted; later calls must match | `test_fund_firstByStranger_reverts`, `test_fund_laterMismatch_reverts`, `test_fund_paramsHash_isAbiEncode` |
| R19 | T | claim state | seller contests a relapse to EXPIRED; LINKED episodes ignored | state reading | H | Reduce: CONFIRMED or EXPIRED later episodes cancel; a live one freezes (`SourceUnderReview`); VOID ignored | `test_relapse_expiredContestedEpisodeCancels`, `test_release_blockedWhileEpisodeLive`, `test_relapse_liveEpisode_reverts`, `invariant_P3_pendingImpliesClaimOpen`, `invariant_P9_noProgressPastLaterDetection` |
| R20 | S | pool trust set | a pool built on a registry the covenant does not trust | constructor parameter | M | Avoid: `registry = covenant.registry()`; no registry parameter | `test_constructor_registryIsCovenants` |
| R21 | S/E | observer honesty | seller bribes k organisations to sign clean passes (the mirror of R5 / R8) | collusion | H | Reduce: k distinct orgs outside {seller, buyer}; re-fetchable positive artefact (scene record, pinned empty queries); objective challenge; cap ≤ rebate; final share only after the cure term, a MARS corroboration and two instruments (contract rules; a final installment without `twoInstruments` is refused when the pool opens, enforced in `PoolSetup.validParams`) | `invariant_P2_quorumAndFinalGates`, `test_cleanPass_sellerOrgSigner_reverts`, `test_final_requiresTwoInstruments`, `test_fund_badParams_finalWithoutTwoInstruments`, `test_final_requiresCorroboration` |
| R22 | D | sponsor funds | late scenes after withdrawal; scheduling without funds | lifecycle | M | Reduce: OPEN / CLOSING / CLOSED; no withdrawal before CLOSED; UNFUNDED installments queue first-in first-out | `test_sponsorWithdraw_beforeClosed_reverts`, `test_installment_waitsWhenPoolShort`, `test_installment_unfundedQueue_fifo`, `invariant_P1_capAndSolvency`, `invariant_P4_ethConservation` |
| R23 | T | release vs relapse ordering | (a) an installment releases just before a relapse is publishable; (b) a relapse is hidden from the core's `latestEpisode` slot | the core keeps only the newest non-LINKED proposal per source | H | Reduce + retain: `holdWindow ≥ publicationLag + challengeWindow + contestWindow`; final share after cure term + grace; `SourceUnderReview` / `RelapsePending` read from `latestEpisode`; `relapse` accepts a caller-named CONFIRMED or EXPIRED episode; sponsor watcher. Residual in section 15 | `test_release_afterHold_beforeRelapse`, `test_release_relapsePending_reverts`, `test_attestCleanPass_relapseHiddenByOlderDatedProposal_residualR23`, `test_attestCleanPass_expiredRelapseStillPending_reverts` |
| R24 | E | registry membership (V2, Anvil) | a cartel admits Sybils; a rogue org cannot be removed; a stolen key votes | admission by vote | H | Reduce + retain: majority with a k floor, admission delay, symmetric removal vote whose k floor is re-checked when it executes, votes judged when cast (quarantine after a revocation), proposal expiry. A majority of member controllers owns the trusted set; the admission delay gives parties time to notice, not a veto; per-covenant observer pinning is not built | `invariant_V2_noOrgWithoutMajority`, `invariant_V2_observerKeysOnlyInMembers`, `test_v2_removal_closesKeys`, `test_v2_concurrentRemovals_cannotDropBelowFloor`, `test_v2_admission_delay`, `test_v2_recentRevocation_voteRejected`, `test_v2_proposal_expires_reProposable` |
| R25 | E | surety's bond | operator or surety moves funds through the `SuretyBond` address | bond contract is the seller | M | Avoid: operator limited to create / contest / mitigation claim; ETH leaves only to the funder; `pull` is permissionless. ETH the operator sends with `createCovenant` is forwarded as the initial bond and, like every return, is paid to the funder at settlement, so the surety posts the bond (`fund` on the core) and the operator sends none | `test_suretyBond_operatorCannotMoveFunds`, `test_suretyBond_operatorCreatesCovenant_sellerIsBond_emits`, `test_suretyBond_pull_afterSettle`, `test_suretyBond_pull_poolRefund`, `test_suretyBond_withdraw_revertingFunder_keepsBalance` |
| R26 | R | open-slot badge | the author registers in the open observer slot and presents it as independent | wording | L | Retain: wording plus the registration transaction link; no mechanism is claimed | — (disclosure) |
| R27 | T | final share | cure term outlives the covenant, so a relapse could never be attested | term arithmetic | M | Avoid: the final share is scheduled only if `since + cureTerm + relapseGrace ≤ end + publicationLag`, else `FinalOutOfTerm` | `test_finalInstallment_outOfTerm_notScheduled` |
| R28 | S/T | corroboration | observers sign a MARS-silent window that the snapshot contradicts | second provider read by the same observers | M | Reduce: pinned `marsSnapshotHash`, `marsPlumesInWindow` must be 0, re-checkable challenge reason 5, k distinct orgs; the final share waits for it | `test_corroboration_marsPlume_reverts`, `test_final_requiresCorroboration`, `test_challengeCorroboration_reattested_effective_bondToSeller`, `test_challengeCorroboration_unanswered_dropped_refunded` |
| R29 | D | payouts | a hostile receiver reverts or re-enters on `withdraw` | external calls | M | Avoid: pull payments, state zeroed before the call, failed transfer keeps the credit | `test_core_reentrantBuyer_paidOnceExactly`, `test_core_revertingBuyer_keepsCredit`, `test_pool_reentrantSeller_paidOnceExactly`, `test_pool_revertingSeller_keepsCredit`, `test_pool_reentrantSponsor_paidOnceExactly`, `test_withdraw_revertingReceiver_keepsCredit` |
| R30 | T/E | pool terms | the seller opens its covenant's pool before the buyer, with terms it chose: VERDICT_ONLY (which takes no top-up, so no funded pool can follow), its own unit cap, short windows, a low publication lag | either party may open; the opening checks the shape of the terms (`validParams`), not the counterparty's agreement; `policyHash` is any non-zero value | M | Reduce + retain: the opener, mode, `policyHash` and `paramsHash` are on chain (`opener` in `getPool`, the `PoolFunded` event), so a pool whose terms are not the agreed policy is visible before any pass counts; `make verify-evidence` (row `pool-terms`) compares each live pool's opener and terms with a recorded policy. Roadmap: the counterparty's EIP-712 consent to `paramsHash` at opening, minimum windows and lag, and a unit cap bounded by the covenant's tier amounts | `test_fund_bySeller_opensPool`, `test_fund_paramsHash_isAbiEncode`, `test_fund_laterMismatch_reverts`, `test_pool_opened_by_seller_on_unrecorded_terms_fails` |

## 5. What the ledger mitigates and what it introduces

| Mitigated by the ledger | Introduced by the ledger |
|---|---|
| A party dropping a detection or a clean pass from its own records | Key loss and theft (R6) |
| Withholding a rebate or a counted refund | Oracle manipulation and observer collusion (R5, R21, R28) |
| Changing the policy after evidence exists (commit before evidence) | Griefing of challenges and proposals (R8, R16, R17) |
| Rewriting a past acceptance | Public metadata (R9) |
| Self-attestation by a contract party | Testnet liveness and finality; RPC dependence (R4) |
| An administrator moving funds | Smart-contract bugs (section 13), a linked-library choice at deployment (section 15) |
| The record disappearing when one party leaves | The quantum threat to ECDSA identities (R7, section 9) |

## 6. Authentication and integrity properties

| Property | Mechanism | Test |
|---|---|---|
| Attestor authentication | ECDSA recovery against a registered OBSERVER key valid at `block.timestamp`; ML-DSA-44 verified off-chain against the registry's `pqPubKeyHash` for the signer | `test_unregisteredSigner_reverts`, `test_revokedKey_reverts`, `test_attestCleanPass_nonObserverKey_reverts`, `test_sign_and_verify_both_halves`, `test_verify_bundle_rejects_foreign_mldsa_key` |
| Message integrity | the signature covers the full EIP-712 struct including `evidenceHash` / `sceneHash` | `test_tamperedEvidenceHash_reverts`, `test_cleanPass_tamperedCloud_reverts` |
| Same-chain replay | per-key nonce bitmap (core and pool keep separate bitmaps) | `test_replay_reusedNonce_reverts`, `test_attestCleanPass_replay_reverts` |
| Cross-domain replay | EIP-712 chainId + verifyingContract; distinct type hashes; FIPS 204 context string `MethaneClause/v1`. The ML-DSA-44 context binds the protocol, not the chain or the contract: the bundle names a `covenantId`, which is a per-deployment counter, so the chain binding of a hybrid signature comes from its ECDSA half today, the half section 9 assumes a quantum adversary can forge. Roadmap ([`PRODUCTION.md`](PRODUCTION.md) item 10): ML-DSA-44 over JCS({bundle, chainId, verifyingContract}) under a new context version, an observer, verifier and page change with no contract change | `test_crossChainReplay_reverts`, `test_crossContractReplay_reverts`, `test_cleanPass_crossChain_reverts`, `test_pool_rejectsCoreAttestation`, `test_core_rejectsCleanPass`, `test_context_and_message_are_bound` |
| Freshness | `now ≤ expiry ≤ now + 1 day` | `test_expired_reverts`, `test_expiryTooFar_reverts`, `test_attestCleanPass_expired_reverts` |
| Agreement | every attestation in a batch must carry the same fields, else `AttestationMismatch` | `test_observerDisagreement_reverts`, `test_attestCleanPass_attestationMismatch_reverts` |
| Non-repudiation | evidence hash and organisation count in events; signer and organisation recovered from the calldata signatures (`make verify-evidence` row `eip712`, [`VERIFY`](VERIFY.md) step 2) | `test_happyPath_2of3_confirmed_buyerWithdraws`, `test_attestCleanPass_storesPendingPass_emits` |
| Anonymity / privacy | **not claimed.** Keys are pseudonymous addresses; organisations are named in the registry | — |

## 7. Replay, nonce, chain id, expiry

Signature format: 65 bytes `r ‖ s ‖ v`, `v ∈ {27, 28}`, low `s` only. Digest
`keccak256(0x1901 ‖ domainSeparator ‖ hashStruct(...))` with domain `{name "MethaneClause", version "1", chainId,
verifyingContract}`; the domain separator is cached and recomputed if the chain id changes (fork safety). Nonces are a
per-key bitmap, so any unused `uint64` works and a nonce never reopens. The signer chooses `expiry`; the contract
accepts it only if `now ≤ expiry ≤ now + MAX_EXPIRY_HORIZON` (1 day, `contracts/src/MethaneCovenant.sol`), so a
signature is usable only in the 24 h before its expiry (`test_expired_reverts`, `test_expiryTooFar_reverts`). A key's
validity is checked when the batch is submitted, so a revoked key's attestations fail even if they were signed before
the revocation. One bad attestation reverts the whole call: nothing is half-written. `make demo` repeats replay (`Replay`), revoked key (`RevokedKey`) and
resubmission (`DuplicateEvidence`) as real failed transactions; the Sepolia run records tamper, self-attestation,
not-covered and replay rejections with receipt status 0 ([`data/derived/testnet_run.json`](../data/derived/testnet_run.json)).

## 8. Key management

| Role | Held by (production design) | Prototype | What it signs or does |
|---|---|---|---|
| OBSERVER | each observer organisation, independently | the author [SIM]; on Anvil, public test accounts; in the judge sandbox, published keys plus the judge's own wallet (OBS_JUDGE) | EIP-712 attestations; ML-DSA-44 over the bundle |
| SELLER | the seller (or its `SuretyBond` contract address) | [SIM] | `createCovenant`, `contest`, `recordMitigationClaim` |
| BUYER | the importer | [SIM] | receives rebates; may open the pool |
| Org controller | a separate, cold key per organisation | **the organisation's first key** (see below) | `registerKey`, `rotateKey`, `revokeKey` for its organisation |
| Registry admin | nobody after setup | renounced on Sepolia (zero address in the deployment record) | setup only |
| Deployer | the operator | the author | deploys and links libraries; holds no funds role |

- **Rotation:** `rotateKey(old, new, algId, pqPubKeyHash)` closes the old key now and registers the new one for the
  same organisation and role; history is kept (`test_rotate_keepsHistory`, `test_rotatedOldKey_reverts_newKeyAccepted`).
  Bundles stay verifiable after rotation because the hash is over canonical JSON and the old key's validity window is
  on chain (`test_verify_bundle_passes_after_signer_rotated_later`).
- **Revocation:** `revokeKey(key)` by the organisation's controller or by the key itself (a compromise report);
  effective at once for every later observer submission and every later `createCovenant`
  (`test_revoke_bySelf_controller`, `test_revokedSellerKey_stillContests_andIsCreditedAtSettle`; `make demo` revokes
  OBS_B and its next attestation reverts `RevokedKey`). A covenant's seller and buyer are addresses fixed at creation:
  the registry gates who may create a covenant, and the covenant then pays and answers those addresses
  (`test_rotatedBuyerKey_rebateStillCreditedToOldAddress`); a party rotates its keys inside a contract wallet
  (section 15).
- **`algId`:** 1 = secp256k1 ECDSA (checked on chain); 2 = hybrid ECDSA + ML-DSA-44, with `pqPubKeyHash = SHA-256` of
  the ML-DSA-44 public key anchored in the registry. The local demo registers its observers as algId 2; the Sepolia
  main observers predate the hybrid and are algId 1 (moving them is `rotateKey(old, new, 2, pqHash)`, not a redeploy).
  ML-DSA seeds are not derived from the ECDSA key (`test_pq_seed_is_not_derived_from_the_ecdsa_key`).
- **Storage:** operator keys live in a local `.env` that is never committed (`.env.example` lists the names); `make
  secrets` runs gitleaks over the history; Anvil test keys are refused off Anvil: an observer refuses any key of the
  public Anvil mnemonic (accounts 0–9) on a chain other than 31337, whether it is set in the environment or passed in
  (`test_anvil_keys_refused_off_anvil`, `test_anvil_key_in_env_refused_off_anvil`),
  and deterministic signing is opt-in and refused on any chain but 31337 (`test_opt_in_determinism_refused_off_anvil`).
- **Controller permanence.** In v1 an organisation's controller is fixed once the admin renounces: a stolen
  controller key is answered by KeyRegistryV2's removal vote (`test_v2_removal_closesKeys`) or by the parties naming a
  new registry; production uses a cold controller key per organisation, separate from its signing keys, a
  deployment-script change with no contract change ([`PRODUCTION.md`](PRODUCTION.md) item 3). On the Sepolia main
  deployment `Deploy.s.sol` (`_org`) makes each organisation's first key its controller, so revoking that key closes
  it as a signer while the same address can still register a new OBSERVER key for the organisation
  (`test_revokedFirstKey_staysController_registersNewObserver`).

## 9. Cryptography and the post-quantum boundary

| Primitive | Use | Under Shor | Under Grover |
|---|---|---|---|
| Keccak-256, SHA-256 | commitments, Merkle tree, EIP-712 digests | not broken | ≈ 128-bit preimage security |
| secp256k1 ECDSA | EVM transactions and the on-chain signature check | broken | — |
| ML-DSA-44 (FIPS 204, level 2) | second signature over the same JCS bundle bytes, verified off-chain (`scripts/verify_bundle.py`, the page) | designed to resist | — |

Sizes: ML-DSA-44 public key 1,312 bytes, signature 2,420 bytes (asserted in `test_sizes_and_deterministic_keygen`).
Time (median of 200 runs on one machine, [`data/derived/pq_bench.json`](../data/derived/pq_bench.json) from
`scripts/pq_bench.py`): ML-DSA-44 signs the bundle in 12.6 ms and verifies it in 3.1 ms; secp256k1 ECDSA signs the
EIP-712 attestation in 2.4 ms and recovers it in 3.7 ms. Both run in pure Python here: dilithium-py is a pure-Python
reference implementation, not an optimised one, and eth-keys uses its native Python backend
(`test_security_md_pq_timing_matches_the_json`).
Libraries, no custom cryptography: `dilithium-py` in the pipeline (a reference implementation, not constant-time),
`@noble/post-quantum` in the page (it only verifies; no secret key reaches it). A production signer uses a
constant-time FIPS 204 library.

**Anchoring.** An ML-DSA-44 signature counts only if SHA-256 of its public key equals the `pqPubKeyHash` that
`KeyRegistry` holds for the signer's address (algId 2). The bundle's own `pkHash` field is informational only: a
bundle signed with a foreign ML-DSA key that carries that key's hash fails
(`test_verify_bundle_rejects_foreign_mldsa_key`). A signer with no anchor, or registered as algId 1, fails too; the
demo's own check (`pipeline/observer/demo.py`) applies the same rule. `scripts/verify_bundle.py` has two modes, and
they prove different things:

- **`--rpc URL` proves it against the chain.** The registry is the one the covenant itself trusts,
  `MethaneCovenant.registry()` read at the run's covenant address; a run that names another registry is refused
  (`test_verify_bundle_rpc_uses_covenant_registry`); the anchor is `KeyRegistry.getKey(signer)` read live. It then
  applies the covenant's acceptance rules: the `EpisodeProposed` (or `DetectionLinked`) log must carry this covenant
  and this evidence hash, and each signer must hold an OBSERVER key of an organisation that is neither party's, valid
  at that block's time, with k distinct organisations (`test_verify_bundle_rejects_key_revoked_before_proposal`,
  `test_verify_bundle_rejects_non_observer_role`, `test_verify_bundle_rejects_signer_in_seller_org`,
  `test_verify_bundle_rejects_two_keys_of_one_org`, `test_verify_bundle_rejects_log_with_other_evidence_hash`).
- **Offline proves consistency only.** The anchor is the registry read the demo run recorded on chain
  (`registeredKeys` in `out/demo/run.json`, with the registry address and block), accepted only from the run's own
  registry; the run's covenant and registry addresses must equal `contracts/deployments/<chainId>.json` when that
  record exists (Sepolia is committed; the Anvil record is written by the local deploy)
  (`test_verify_bundle_offline_compares_the_deployment_record`). It does not show that the recorded read is what the
  chain holds; `--rpc` does. The page checks against the same recorded registry read.

The Sepolia main observers, and the judge sandbox's published observers, are algId 1 with no `pqPubKeyHash`, so
their bundles carry no anchored ML-DSA half (sandbox keys: `contracts/script/SandboxBase.sol`, `pipeline/observer/judge.py`);
the ML-DSA-44 half runs in the local demo.
Anyone can read it from the registry in `contracts/deployments/11155111.json` (observer A shown; `observerB`,
`observerC` likewise); the third field is `algId`, the sixth `pqPubKeyHash`:

```sh
cast call 0x8f96340b051ac19D92976cC14aAEA0461Eaf0fc7 \
  "getKey(address)((bytes32,uint8,uint8,uint64,uint64,bytes32,address))" \
  0xc208f0a1F357B89d611cbb77F9b7630EC246b7Ae --rpc-url https://ethereum-sepolia-rpc.publicnode.com
```

**Honest PQ limit (verbatim):** "EVM transaction identity and the on-chain signature check remain secp256k1 ECDSA,
which Shor breaks; Keccak/SHA-256 commitments keep ≈128-bit preimage security under Grover; ML-DSA-44 protects the
long-term authenticity of bundles when verified off-chain. We do not claim a quantum-safe chain."

Status line: FIPS 203 / 204 / 205 are final; FIPS 206, NIST IR 8547 and EIP-8051 / EIP-8052 are drafts.

## 10. Access-control matrix

"k sigs" = a batch signed by k distinct observer organisations outside {seller, buyer}, valid now; "anyone" means any
wallet, with the exact bond where one is listed. No contract has an owner, an upgrade path or a pause. Live on
Sepolia: `KeyRegistry`, `MethaneCovenant` and `IncentivePool` with its five libraries; `SuretyBond` and
`KeyRegistryV2` run on Anvil only (section 1, "Where each contract runs today").

**KeyRegistry** (v1; deployed on Sepolia and Anvil)

| Function | Admin (setup only) | Org controller | The key itself | Anyone |
|---|---|---|---|---|
| `setOrgController`, `renounceAdmin` | yes | — | — | — |
| `registerKey` | yes (before renouncing) | own org | — | — |
| `rotateKey` | yes (before renouncing) | own org | — | — |
| `revokeKey` | yes (before renouncing) | own org | yes | — |

**MethaneCovenant** (core)

| Function | Seller | Buyer | Observers | Anyone |
|---|---|---|---|---|
| `createCovenant` | registered SELLER key | — | — | — |
| `fund` | yes | yes | yes | yes |
| `proposeEpisode` | — | — | k sigs required | submits with the proposer bond |
| `challenge` | — | — | — | yes, objective reason + challenger bond, inside the window |
| `contest` | only | — | — | — |
| `submitResolution` | — | — | k sigs (k + 1 after a contest) | submits |
| `finalize`, `settle` | — | — | — | yes, after the deadline / term |
| `recordMitigationClaim` | only (no state effect) | — | — | — |
| `withdraw` | own credit | own credit | own credit | own credit |

**IncentivePool**

| Function | Covenant buyer / seller | Sponsor | Observers | Anyone |
|---|---|---|---|---|
| `fund` (first call) | only a party | — | — | — |
| `fund` (top-up, ONCHAIN) | yes | yes | yes | yes, same policy and params |
| `sponsorWithdraw` | own contribution | own contribution, after CLOSED | — | — |
| `openClaim`, `countPass`, `finalizePass`, `voidPass`, `release`, `relapse`, `closeClaim`, `finalizeCorroboration` | — | — | — | yes (permissionless, state-checked) |
| `attestCleanPass`, `attestCorroboration` | — | — | k sigs required | submits with the pass bond |
| `challengePass`, `challengeCorroboration` | — | — | — | yes, objective reason + challenger bond |
| `resolvePass` | — | — | k sigs (CONFIRM_CLEAN / WITHDRAW_CLEAN) | submits |
| `withdraw` | own credit | own credit | controllers' fee credit | own credit |

**SuretyBond**

| Function | Operator (real seller) | Funder (surety) | Anyone |
|---|---|---|---|
| `createCovenant`, `contest`, `recordMitigationClaim` | only | — | — |
| `pull` | yes | yes | yes (credits arrive at the bond, then only the funder can withdraw) |
| `withdraw`, `revokeSelf` | — | only | — |
| fund the covenant's bond | — | on the core, like anyone | yes |

**KeyRegistryV2** (Anvil only)

| Function | Member controller | Party controller | The key itself | Anyone |
|---|---|---|---|---|
| `proposeOrg`, `voteOrg`, `proposeRemoval`, `voteRemoval` | yes, unless quarantined after a recent revocation | — | — | — |
| `registerKey`, `rotateKey` | own org (OBSERVER) | own org (SELLER / BUYER) | — | — |
| `revokeKey` | own org | own org | yes | — |

Covered by `test_accessControl_and_renounce`, `test_recordMitigationClaim_onlySeller`, `test_createCovenant_requiresRegisteredParties`,
`test_fund_firstByStranger_reverts`, `test_sponsorWithdraw_nonSponsor_reverts`, `test_suretyBond_onlyOperator_reverts`,
`test_suretyBond_revokeSelf_onlyFunder`, `test_v2_admission_nonMember_reverts`, `test_v2_roles_byOrgKind`,
`test_holdsNoFunds`, `test_v2_holdsNoFunds_noAdmin`, `invariant_I7_noAdminPath`.

## 11. Input validation and the adversarial detection rate

Observers refuse bad input **before signing**; a refused input is never signed. The adversarial set
([`data/derived/adversarial.json`](../data/derived/adversarial.json), `make adversarial`) is read here:

| Slice | Caught / n |
|---|---|
| All cases | 42 / 42 (`detection_rate` 1.0) |
| Detections | 18 / 18 |
| Clean passes | 24 / 24 |
| Layer: validation | 36 / 36 |
| Layer: hash | 5 / 5 |
| Layer: re-check | 1 / 1 |

Scope, quoted from the file: *"inputs we wrote; a detection rate over our own adversarial set, not over unknown
attacks"*. The cases include negative and absurd rates, both unit swaps, missing `emission_version`, future and
pre-start timestamps, a plume 50 km away, an unlinked source, a facility outside the set, extra fields, a double
attestation, an edited bundle, a signature replayed on another chain; for clean passes every refusal code of the spec
(cloud flag, origin intersect without look-ahead, plume on the scene, MARS plume nearby, publication lag, term,
duplicate scene, unknown instrument, floor), malformed bundles, edited cloud fields, a [SIM] cloudy scene signed as
not cloudy, and a MARS corroboration over a window with a MARS plume.

**Slow drift** (rate nudged ×0.97 per pass over 6 passes) passes validation on every pass and is caught only because
the bundle hash no longer matches the provider re-fetch (`slowDrift` in the same file;
`test_adversarial_set_all_caught_and_slow_drift_is_a_hash_catch`). It is a hash catch, not a statistical one.

## 12. Bonds and griefing (testnet tokens, no monetary value [SIM])

| Bond | Value | Source | Effect |
|---|---|---|---|
| Core proposer bond | 0.0001 ETH | `proposerBond` in `contracts/deployments/11155111.json` | returned on CONFIRMED / EXPIRED; to the challenger on VOID |
| Core challenger bond | 0.0002 ETH | `challengerBond` in the same file | to the buyer when k observers CONFIRM; returned if unresolved |
| Pool pass bond | 0.0001 ETH | `passBondWei` in `data/derived/policy_v2.json` | returned to the submitter when counted; to the challenger on VOID |
| Pool challenger bond | 0.0002 ETH | `challengerBondWei`, same file | to the covenant seller on CONFIRM_CLEAN; returned if unresolved |
| Observer fee per counted pass | 0.00001 ETH [SIM] | `observerFeeWei`, same file | split among the signing organisations' controllers |

Slashing happens only on objective, re-checkable failure. A scientific dispute holds a payment through `contest` and
never slashes. Bonds make spam cost something; they are not sized to deter a funded attacker on mainnet.

## 13. Smart-contract assurance

- **Core invariants I1–I7** (`contracts/test/invariants/MethaneCovenant.invariants.t.sol`): quorum, no
  self-attestation, one payout per episode, bond conservation, no stuck state, mitigation claim inert, no admin path.
- **Pool invariants P1–P9** (`contracts/test/invariants/IncentivePool.invariants.t.sol` + `contracts/test/invariants/PoolHandler.sol`, stateful
  fuzz over core and pool actions): `invariant_P1_capAndSolvency`, `invariant_P2_quorumAndFinalGates`,
  `invariant_P3_pendingImpliesClaimOpen`, `invariant_P4_ethConservation`, P5 liveness probed in `afterInvariant`,
  `invariant_P6_coreUntouched`, `invariant_P7_noReplay`, `invariant_P8_publicationLag`,
  `invariant_P9_noProgressPastLaterDetection`; `test_handler_reachesEveryPath` shows the handler reaches every path.
- **Registry V2 invariants:** `invariant_V2_noOrgWithoutMajority`, `invariant_V2_observerKeysOnlyInMembers`,
  `invariant_V2_memberCountMatches`.
- **Unit tests per custom error** and a pinned ABI surface (`test_surface_functions`, `test_surface_events`,
  `test_surface_errors`, `test_surface_structs`).
- **Core unchanged:** the SHA-256 of both core `deployedBytecode` artefacts is compared in `make check`
  (`make core-bytecode`).
- **Cross-language parity:** Python and Solidity digests match on shared vectors (`test_digestMatchesContract`,
  `test_cleanPassDigest_matchesPython`, `test_corroborationDigest_matchesPython`, `test_policyV2_paramsHash_matchesPipeline`).

### Slither / Halmos / invariants

Which property each method covers, and its bound: [`contracts/README.md`](../contracts/README.md), "What is proven,
and by which method". Nothing here claims that the contracts as a whole are formally verified.

**Stateful fuzz (Foundry invariants, in `make test`).** Core I1–I7 and KeyRegistryV2's three invariants: 128 runs ×
depth 128 (16,384 calls each); pool P1–P9 plus `counted` bookkeeping: 64 runs × depth 512 (32,768 calls) with
`fail-on-revert`; every planted P1–P9 fault was caught (`contracts/test/invariants/`). No proof: random sequences only.

**Static analysis.** Slither 0.11.6 over the 13 contracts of `contracts/src` (`make slither`, outside `make check`):
52 findings, 0 High, 10 Medium, 37 Low, 5 Informational; 0 fixed, 9 false-positive, 43 accepted, each with its reason;
25 are in the byte-frozen v1 core, 0 in the five pool libraries. Every finding, including wildcard groups by exact
count, must match the triage file `contracts/slither.triage.json`; the run fails otherwise
([`contracts/slither.md`](../contracts/slither.md)).

**Symbolic execution (bounded proofs).** Halmos 0.3.3 with yices (`make halmos`, about a quarter of an hour, outside
`make check`): 25 results, all as expected ([`contracts/halmos/REPORT.md`](../contracts/halmos/REPORT.md)). Proved
within the stated bounds:
- (a) for every share and threshold vector `validParams` accepts, every cap below 2^128 and every funding, the real
  `PoolInstallments` creates three installments of exactly `cap × share / 10,000`, PENDING or UNFUNDED, with the
  claim's reserve equal to the pool's (32 paths);
- (b) P1 (cap and solvency) and P3 (a pending installment belongs to an open claim) after every sequence of up to two
  of ten pool actions in any order from a funded pool with two open claims (3,248 paths, 314 s), and after every step
  of four short lifecycles (release-then-relapse, top-up queue, challenged MARS corroboration to the final share,
  close and sponsor withdrawal). The ten actions are named in `contracts/README.md`; pass challenge / resolve /
  finalize / void, the pool's `withdraw` and opening a third claim are left out of that run and are covered by the
  stateful fuzz only;
- (c) core I4 (bond conservation) on the unchanged core (bytecode hash re-checked) after every single action and
  along four lifecycles (paid rebate, contest, unanswered challenge, settlement), with signatures over-approximated
  so a forged quorum is allowed.

The signed entry points are replaced by stand-ins with weaker preconditions
(`contracts/halmos/IncentivePoolHarness.sol`), so these safety properties transfer to the deployed pool. Eleven
reachability canaries fail as required, so the checked states are not empty. Five planted faults, committed as
patches in `contracts/halmos/mutants/`, must each be caught by their run (`make halmos-mutants`, outside
`make check`).

**Rounding lemma.** The three installments never exceed the cap and lose at most 2 wei. Halmos's 256-bit query times
out (reported as TIMEOUT, not as proved); Z3 proves it over unbounded integers for every cap below 2^128 and every
share vector, exact because `cap × share < 2^142` never wraps (`contracts/halmos/rounding_lemma.py`). The 2-wei bound
is tight: under the 3000 / 3000 / 4000 shares a cap of 10,003 wei pays 10,001, so 2 wei are lost (the witness is in
`contracts/halmos/REPORT.md`).

**Not proved by any of the above:** signature verification itself (unit tests: tampered digest, wrong key, replay,
cross-domain, ML-DSA parity); a k-signed core resolution after a challenge (two symbolic signature batches did not
finish in 15 minutes); pool sequences of three or more actions, the pass challenge / resolve / finalize / void paths
and full lifecycles (fuzz only); P2 and P4–P9, KeyRegistryV2 and SuretyBond (unit tests and fuzz only); the compiler,
the EVM, Halmos's own models; the off-chain pipeline and the observers. Which libraries a deployer links is checked
after deployment, not proved: `contracts/script/check_pool_record.py` compares each library's deployed code with its
artifact (its own address masked) and the link bytes in each pool with the deployment record. On-chain signatures are
secp256k1 ECDSA, not post-quantum; the ML-DSA-44 hybrid is off-chain (section 9).

## 14. Data protection (KVKK / GDPR)

The system collects no personal data and writes none on chain: hashes, facility identifiers, rates, timestamps,
amounts and pseudonymous organisation keys; the one identifier a user can add is their own wallet address as a judge
observer (table below). The data sources describe industrial facilities, not people. Raw provider responses are never committed
(data/raw/ is ignored). Corrections never edit: a re-quantified rate is a new attestation, a superseded credit note
is a new note that links the old one. Erasure applies off chain (derived files can be removed); an on-chain hash of
non-personal data needs no erasure. Details: [`docs/ETHICS.md`](ETHICS.md), [`docs/DATA.md`](DATA.md).

### Data classification

Every data item the system handles, where it lives, who can read it, whether it is personal data, what makes a change
to it visible, and how long it is kept. "Chain" means Ethereum Sepolia and the local Anvil chain; what reaches a chain
stays there for as long as the chain exists and is never edited, so correction is a new record and erasure applies off
chain only. The path from this prototype to production for each element: [`docs/PRODUCTION.md`](PRODUCTION.md).

| Data item | Where it lives | Public? | Personal data? | Integrity mechanism | Retention |
|---|---|---|---|---|---|
| Detection evidence bundle (plume id, rate and uncertainty, instrument, detection and publication times, plume coordinates, the provider response hash) | Each observer, off chain; embedded in the committed Annex D export (`data/derived/export/TKM_S_276.annex_d.json`); on chain only its `evidenceHash` | Yes, derived from public provider data under the providers' terms | No: industrial facilities | RFC 8785 JCS → SHA-256 `evidenceHash` inside the EIP-712 `Attestation`; ML-DSA-44 over the same bytes (R1, section 9) | Observers: the life of the covenant plus the limitation period (model clause 8.3); repository: its history; chain: the hash, permanently |
| Clean-pass scene fields (scene id, scene and publication time, instrument, provider, cloud cover and `not_cloudy` flag, floors) | Chain: the signed `CleanPass` fields in calldata and the pool's events; the full scene record with each observer and in `data/derived/cases/*.clean_passes.json` | Yes | No | JCS → SHA-256 `sceneHash` inside the EIP-712 `CleanPass`; ML-DSA-44 (R13) | Chain: permanently; observers and repository as above |
| MARS corroboration (snapshot hash, window, plume count) | Chain: the signed `Corroboration` fields; the snapshot with each observer | Yes | No | Pinned `marsSnapshotHash` inside the EIP-712 `Corroboration` (R28) | Chain: permanently; observers as above |
| Provider responses (raw Carbon Mapper JSON, the MARS GeoJSON zip) and observer receipts (query URL, `retrievedAt`, raw-bytes hash) | `data/raw/` on the fetching machine and with each observer; never committed, never on chain; receipts in the local run record (`out/demo/run.json`, not committed) | Published by the providers; we do not redistribute the raw bytes | No | SHA-256 of each response in `data/MANIFEST.json` and in each receipt; the bundle carries the provider response hash, so a re-fetch is compared byte for byte (R5, R12) | Observers: the life of the covenant plus the limitation period; a fetching machine: until deleted; the repository: never |
| CH₄ map tiles and imagery | Loaded by the reader's browser from Carbon Mapper at view time | Yes, from the provider | No | None of ours: not attested, not stored | Not stored |
| ECDSA signatures (EIP-712) | Chain calldata; signer and organisation in events | Yes | No | Recovery against an OBSERVER key valid at submission; nonce bitmap, domain, expiry (R2) | Chain: permanently |
| ML-DSA-44 signatures and public keys | Off chain beside the bundle (local demo run record); the registry holds `pqPubKeyHash` | Yes | No | SHA-256 of the public key must equal the registry's `pqPubKeyHash` (section 9) | With the bundle |
| Private keys and ML-DSA seeds | The holder's machine only: the operator's `.env` (never committed, never read by coding agents); a judge's key in their wallet or the `JUDGE_PK` environment variable, never written to disk by the scripts; Anvil accounts, the judge-sandbox keys and the demo's ML-DSA seeds are public test values on purpose | Secret, except the published test keys | No | `make secrets` (gitleaks over the history); Anvil keys refused off Anvil, from the environment or passed in (`test_anvil_keys_refused_off_anvil`, `test_anvil_key_in_env_refused_off_anvil`); a lost key is revoked or rotated in the registry (R6) | As long as the holder keeps them; a revoked key never counts again |
| Registered keys, roles, organisation ids and controllers | Chain (`KeyRegistry`) | Yes | No: pseudonymous organisation keys of named organisations | Written only by the admin before it renounces, then by each organisation's controller; full history kept (R6) | Chain: permanently; validity windows close, entries are never deleted |
| A judge's wallet address (OBS_JUDGE) | Chain: an OBSERVER key in the judge-sandbox registry and the sender of the judge's transactions; `out/judge/<chainId>.json` on the judge's own machine (git-ignored) | Yes | Pseudonymous; it can be personal data if it can be linked to a person (GDPR Recital 26). The system asks for no name or contact; the walk-through asks for a fresh test wallet (`cast wallet new`) | Registry history; the key can be revoked by OBS_JUDGE's published controller or by itself | Chain: permanently (revocable as a key, not erasable); the local file until the judge deletes it |
| Policy (`POLICY_V2`, `POLICY_V2_VERDICT_ONLY`, the v1 policy) | Repository (`data/derived/policy_v2.json`); chain: `policyHash` per covenant, `paramsHash` per pool | Yes | No | SHA-256 of the JCS form fixed at `createCovenant` before evidence; `paramsHash = keccak256(abi.encode(PoolParams))`, every top-up must match (R10, R18) | Repository and chain: permanently |
| Covered facility set | Repository (`data/derived/sourceset_v2.json`, `data/derived/sourceset_v3.json`); chain: the Merkle root, and one proof per proposal in calldata | Yes | No: MARS source ids of industrial facilities | Sorted-pair Merkle proof per proposal (`NotCovered`) | Repository and chain: permanently |
| Amounts: bond, rebates, refunds, deposits, observer fees | Chain storage and events | Yes | No | State machines only; pull payments; conservation invariants I4 and P4 | Chain: permanently |
| Episode, claim, pass, corroboration and installment states | Chain | Yes | No | Permissionless transitions with deadlines; invariants I1–I7 and P1–P9 | Chain: permanently; no function deletes a record |
| Exports: IEA Annex D, Annex C seller claims [SIM], the credit-note register | Repository (`data/derived/export/`); credit notes written by `make demo` to `out/demo/` (not committed); an Annex C claim reaches the chain only as its hash (`recordMitigationClaim`) | Yes for the committed files | No in this repository. A real seller's Annex C form has facility and corporate contact fields, which stay off chain with the parties; only the form's hash is on chain | JSON and CSV round trip to the same JCS hash; a credit note's `noteHash`, and an edited note is refused (`test_import_refuses_an_edited_note`) | Append-only: a correction is a new note that links the old one |
| Transaction index and measurements | Repository (`data/derived/sepolia_txs.json`, `data/derived/sepolia_txlog.jsonl`, `data/derived/measurements.json`), rebuilt from the chain | Yes | No: our own addresses, including the author-generated judge test wallet | Each field read back from a public RPC; each sender's count checked against its nonce | Repository: its history |
| What a page reader's browser sends | The page has no backend and keeps its state, wallet state included, in memory; the browser loads modules from cdn.jsdelivr.net, map tiles from tile.openstreetmap.org and CH₄ tiles from api.carbonmapper.org, and reads Sepolia through a public RPC | — | The reader's IP address reaches the page's host and those services under their own terms; the page collects nothing | Module versions pinned; SRI on Leaflet, MapLibre and the ML-DSA-44 graph; viem is version-pinned without an integrity hash (section 2) | Nothing kept by the page |

## 15. Residual risk

> **Residual risks we accept and state**
>
> - **One person holds every observer key in this prototype [SIM].** In production each observer organisation is
>   independent and holds its own keys; here k-of-n is a mechanism demonstration. The judge's own wallet (OBS_JUDGE)
>   is the one signer we do not control.
> - **Hash ≠ truth.** A commitment shows the data was not changed after attesting; it does not show the provider
>   measured correctly. Carbon Mapper and UNEP do not sign their data. Clean passes come from one provider's scene
>   catalogue: k-of-n guards against one observer's error or key, not against a provider error. The chain cannot see
>   clouds; it checks that k organisations signed the same named fields.
> - **Testnet finality and liveness.** Sepolia is a public testnet; money is testnet ETH.
> - **R23, both forms.** (a) An installment can release before a relapse is published, proposed and confirmed;
>   released installments stay released (decision D1) — the hold window and the final share's cure term plus grace
>   bound this. (b) The core keeps only the newest non-LINKED proposal per source (`latestEpisode`), and the pool reads
>   relapses from that slot. A CONFIRMED relapse cannot be displaced by an older-dated proposal: that proposal falls
>   inside the relapse's link window and lands LINKED, leaving the slot alone. Displacement needs either an EXPIRED
>   relapse (EXPIRED anchors no links, so a later proposal dated before the claim's `since` takes the slot) or a
>   later-dated proposal that ends VOID; both need k observer signatures and a proposer bond. While displaced,
>   `release` does not see the relapse. Closed by `relapse(claimId, episodeId)`, which accepts a caller-named CONFIRMED
>   or EXPIRED episode, so any watcher cancels in one call; the sponsor runs the watcher
>   (`test_attestCleanPass_relapseHiddenByOlderDatedProposal_residualR23`).
> - **`finalizeCorroboration` does not apply P9.** Only `attestCorroboration` checks for a later detection; finalizing
>   may reserve the final share while a later detection is live. `release` re-checks P9, and a relapse voids the
>   reserved share (`test_finalizeCorroboration_afterRelapse_schedulesNothing`, `test_release_blockedWhileEpisodeLive`).
> - **A detection rebate rests on the observers answering in the window.** A challenge (anyone, objective reason
>   code, challenger bond) or the seller's contest restarts the deadline; if the observers do not submit k
>   (challenge) or k + 1 (contest) signatures before it, `finalize` expires the episode: the held rebate returns to the
>   bond and the posted bonds are returned, so a silent quorum lets a challenger end a valid detection at no cost
>   (D11: nobody is slashed on expiry; for a clean pass, responsive observers sign CONFIRM_CLEAN and the challenger's
>   bond goes to the seller). An
>   expired episode still cancels a refund claim (R19). The production quorum keeps a spare organisation for each path,
>   n ≥ k + 2 (2-of-4 for k = 2; `test_quorumMargin_contested_oneOrgSilent_kPlusOneConfirm_confirmed`,
>   `test_quorumMargin_contested_onlyKAnswer_expires_rebateBackToBond`), with each observer answering the challenges it is named in
>   ([`PRODUCTION.md`](PRODUCTION.md)). Roadmap in the contract: an objective-reason challenge left unanswered ends
>   CONFIRMED (the detection already carries k signatures), or the challenger's bond goes to the buyer on expiry.
> - **The deployer's linked-library choice is a trust point.** `IncentivePool` delegates to five external libraries
>   (`PoolVerify`, `PoolSetup`, `PoolEpisodes`, `PoolCorroboration`, `PoolInstallments`) by DELEGATECALL to stay under
>   EIP-170. The deployer chooses their addresses at deployment. `contracts/script/check_pool_record.py` checks a
>   deployment record against the chain: each library's deployed code equals its compiled artifact (own address
>   masked) and the link bytes in each pool are the recorded addresses. The main and the judge-sandbox pools are on
>   Sepolia: the same script matched each library's code and each pool's link bytes against the chain, and the
>   pools and the five libraries are Etherscan-verified.
> - **KeyRegistryV2: a majority of member controllers owns the trusted set** of every covenant on that registry. The
>   admission delay gives parties time to notice, not a veto; per-covenant observer pinning is not built. V2 runs on
>   Anvil only.
> - **Observers are not bonded in v2.** A wrong clean pass or detection costs its submitter's bond (won by the
>   challenger), not the signing organisations; an observer's stake is its registered identity, its revocable key and,
>   on V2, removal by vote.
> - **Controller permanence.** In v1 an organisation's controller is fixed once the admin renounces: a stolen
>   controller key is answered by KeyRegistryV2's removal vote (`test_v2_removal_closesKeys`) or by the parties naming
>   a new registry; production uses a cold controller key per organisation, separate from its signing keys. On the
>   Sepolia main deployment the controller is each organisation's first key (section 8,
>   `test_revokedFirstKey_staysController_registersNewObserver`).
> - **Parties rotate keys inside a contract wallet.** A covenant's seller and buyer are addresses fixed at creation
>   (section 8), so a party keeps its address and rotates the keys behind it: a multisig does this for the buyer or
>   the seller. `SuretyBond` already puts a contract at the seller address, so its operator key can create, contest
>   and record claims but cannot move the bond's money (R25); an operator-rotation function is the remaining step
>   (`test_revokedSellerKey_stillContests_andIsCreditedAtSettle`, `test_rotatedBuyerKey_rebateStillCreditedToOldAddress`).
> - **Settlement tail.** Detections published after the term plus the challenge window are outside the bond: after
>   `settle`, `proposeEpisode` reverts `WindowClosed` (`test_settle_thenLatePublishedDetection_reverts_WindowClosed`).
>   A tail covenant with a backdated start (up to `MAX_LOOKBACK`, 365 days in `contracts/src/MethaneCovenant.sol`)
>   covers them (`test_tailCovenant_backdatedStart_coversLatePublishedDetection`), and a later core version settles
>   only after the publication lag (roadmap; the v1 core stays byte-identical).
> - **An UNFUNDED installment** keeps its pool CLOSING until a top-up or a relapse; sponsors' remainder waits with it.
>   The queue is first in, first out: an UNFUNDED installment at the head blocks later, smaller ones that the balance
>   could already cover (head-of-line blocking), so no installment overtakes an older one.
> - **Sponsor remainder is split by gross contribution.** After CLOSED, `sponsorWithdraw` shares the unreserved balance
>   pro rata to what each sponsor put in, not to what each contribution still covers. A sponsor who tops up after
>   installments were already paid from earlier money shares those earlier payouts: its remainder is diluted and the
>   earlier sponsors' grows by the same amount.
> - **Challenge-loop stall.** A challenger may challenge every pending pass. With silent observers each challenge ends
>   only when `resolveWindow` passes (expired, both bonds returned, nobody slashed), so a claim's progress can stall
>   for up to `MAX_PENDING` × `resolveWindow` (32 × 7 days under `POLICY_V2`; `MAX_PENDING` in
>   `contracts/src/IncentivePool.sol`, `resolveWindow` in `data/derived/policy_v2.json`). Responsive observers end it
>   at once and the challenger's bond goes to the seller.
> - **Floor blindness both ways:** a source just below the instrument floor is invisible to rebate and refund.
> - **ECDSA on chain** (section 9).
> - **Judge sandbox [SIM].** Its observer, seller and buyer keys are published so anyone can play every role; its pool
>   (on Sepolia) is opened by `make judge-cure` or by the page's `openRefundClaim` with one rebate plus one observer fee
>   and has a public quorum. The OBS_JUDGE and OBS_INDEPENDENT controllers are published too, so anyone can add or
>   revoke OBS_JUDGE (and OBS_INDEPENDENT) keys and reach k = 2 with published observer 1: the sandbox shows the
>   mechanism, never observer independence. Anyone holding a published key can revoke it (the operator registers the
>   next generation, `make sandbox-rekey`), race a judge's proposal (`make judge-reset`), contest with the seller key
>   (confirming then needs k + 1 = 3 organisations including OBS_JUDGE, otherwise it expires and nobody is slashed),
>   or sweep the seller key (it only ever holds gas money). The main deployment's keys stay private.

## 16. Questions a security reviewer will ask

**"Your ML-DSA signature is checked against a hash the bundle itself carries — what stops me from signing with my own
key?"** Nothing would, which is why the bundle's hash is not used. The verifier takes the anchor from the registry:
SHA-256 of the presented ML-DSA-44 public key must equal `KeyRegistry.getKey(signer).pqPubKeyHash` for the address
whose ECDSA signature the contract accepted, with algId 2. A foreign keypair with a matching self-declared hash fails
(`test_verify_bundle_rejects_foreign_mldsa_key`). The ML-DSA half is still verified off chain only (section 9).

**"What if k observer organisations form a cartel?"** On the core and the pool, k colluding organisations outside
{seller, buyer} can attest what they like; what limits them is that every attestation is a re-fetchable claim about a
public provider record, so anyone can challenge an objective error and win the submitter's bond, and the refund is
capped at the rebate with the final share behind two instruments, a second provider and the cure term. On
`KeyRegistryV2`, a majority of member controllers owns the trusted set: it can admit or remove members; the admission
delay gives parties time to notice, not a veto. Per-covenant observer pinning, which would let each covenant refuse a
newly admitted cartel, is not built. In this prototype one person runs every observer organisation [SIM].

**"What is actually live, and where?"** On Ethereum Sepolia: `KeyRegistry` and `MethaneCovenant` (main pair and judge
sandbox pair) and `IncentivePool` with its five linked libraries (main pool and judge-sandbox pool), all
Etherscan-verified, addresses in `contracts/deployments/11155111.json`; `contracts/script/check_pool_record.py`
matched the libraries' code and the pools' link bytes against the chain. `SuretyBond` and `KeyRegistryV2` run on
Anvil only (`make demo`, `make judge-local`) and in tests. Section 1 has the table.

**"Can the buyer stall the seller's refund?"** The buyer (or anyone) can challenge each pending clean pass with a
challenger bond. If the observers answer, the pass counts at once and the challenger's bond goes to the seller, so
stalling costs money. If the observers stay silent, each challenge expires after `resolveWindow` with both bonds
returned, and a claim can stall for up to `MAX_PENDING` × `resolveWindow` (section 15). The buyer cannot cancel a
counted pass, unpay a released installment, or touch the core's rebate path.

**"Who loses money when something goes wrong?"** A proposer of a bogus detection loses its proposer bond to the
challenger; a failed challenger of a detection loses its bond to the buyer; a submitter of a bad clean pass loses its
pass bond to the challenger; a failed challenger of a clean pass loses its bond to the seller; the seller pays rebates
on CONFIRMED episodes from its bond; sponsors pay counted installments from their pool, and the share released before
a relapse is not clawed back (D1). Nobody loses on an expiry: unanswered challenges and contests return the bonds.
Observer organisations post no bond in v2 and lose nothing directly. All amounts here are testnet ETH [SIM].
