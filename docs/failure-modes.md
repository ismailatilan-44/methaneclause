# Failure modes

What happens when a part of the system fails, and why no failure leaves a case half-done. The rule throughout: a bad
input produces a reverted transaction (receipt status 0) and nothing moves; every waiting state has a deadline after
which anyone can end it. Threats by an attacker are in [`docs/SECURITY.md`](SECURITY.md); this page is about things
going wrong.

| Failure | What happens | What the system does | Evidence |
|---|---|---|---|
| **Carbon Mapper or MARS is down** | observers cannot fetch | nothing is attested, so nothing moves; windows run on chain time, not on our uptime; HTTP fetches retry with back-off (`pipeline/common.py`); `make demo` runs offline from the committed snapshot | `make demo` (default snapshot mode); `invariant_I5_noStuckState` |
| **Carbon Mapper has no CORS header** | the page cannot fetch the provider | the fetch runs in the observer or terminal; the page shows its receipt and re-verifies hashes and signatures locally | the page's observer panel |
| **RPC node fails, lags or a transaction is retried** | a submission may arrive twice or not at all | an identical proposal is rejected (`DuplicateEvidence`), episode count unchanged; scripts read state from the latest block and retry while a lagging node catches up (`pipeline/observer/chain.py`) | `test_duplicateEvidence_isIdempotent`; `make demo` "Resubmit" step |
| **Gas estimate wrong on the public testnet** | Sepolia prices new state far above the local schedule | scripts ask the node (`eth_estimateGas`); both of the real failures on Sepolia are recorded with their causes | `transactions.unexpected_failures_detail` in [`data/derived/measurements.json`](../data/derived/measurements.json) |
| **Provider re-processes a plume or renames a source** | the same plume returns a new rate or name | bundles pin `emission_version` and the response hash; a re-quantified plume is a new attestation that lands LINKED (no second payout); a CONFIRMED episode is never edited; `make reproduce` reports the change as a provider change | `test_linkedDetection_paysNothing`, `test_detectionAfterLinkWindow_opensNewEpisodeWithParent` |
| **Provider adds a plume after a clean pass was attested** | the pass was not really clean | the re-check compares the plume ids on that scene; a plume added on the scene flips it to DIFFERS (the observer withholds its signature); on chain anyone challenges with `PLUME_ON_SCENE`; a plume added elsewhere at the source does not flip an honest pass | `test_recheck_plume_added_on_the_scene_differs`, `test_recheck_plume_added_elsewhere_at_the_source_stays_equal`, `test_challengePass_withdraw_voidsPass_bondToChallenger` |
| **Observers disagree** | k bundles do not hash the same | no quorum, nothing happens (`AttestationMismatch`); never a silent acceptance | `test_observerDisagreement_reverts`, `test_attestCleanPass_attestationMismatch_reverts` |
| **Mis-attribution to a neighbouring facility** | a plume near two facilities | only plumes within 1.5 km from a linked Carbon Mapper source count; the seller can `contest`, which holds the rebate for k + 1 independent observers and never slashes; unanswered, it expires with nobody slashed | `test_plume_belongs_to_the_nearest_target_only`, `test_contest_confirmedByKPlusOne`, `test_contest_expires_noSlash` |
| **An observer key is lost or stolen** | signatures under that key | the organisation (or the key itself) revokes it; every later submission with it reverts `RevokedKey`, including attestations signed before; a new key is registered by rotation with history kept | `test_revokedKey_reverts`, `test_revoke_bySelf_controller`, `test_rotatedOldKey_reverts_newKeyAccepted`; `make demo` revocation step |
| **Observers do not answer a challenge** | a challenged episode or pass waits | after the resolve window anyone finalizes: the episode or pass expires, both bonds return, nobody is slashed | `test_challenge_unresolved_expires_bondsReturned`, `test_challengePass_unresolved_expires_noSlash`, `test_challengeCorroboration_unanswered_dropped_refunded` |
| **A new detection arrives while a refund is pending** | the source may have relapsed | a live episode freezes counting and release (`SourceUnderReview`); a CONFIRMED or EXPIRED one lets anyone cancel the claim (`relapse`, O(1)); a VOID one is ignored | `test_release_blockedWhileEpisodeLive`, `test_relapse_cancelsClaim_voidsInstallments_releasesReserve`, `test_relapse_gasIndependentOfPendingPasses` |
| **The pool is short of funds** | an installment is earned but cannot be reserved | it waits as UNFUNDED in a first-in first-out queue and is scheduled by the next top-up (any amount, anyone); the pool cannot close and sponsors cannot withdraw while it waits | `test_installment_waitsWhenPoolShort`, `test_installment_unfundedQueue_fifo`, `test_closeClaim_pendingInstallment_reverts` |
| **A payee's contract reverts or re-enters on payment** | `withdraw` fails | pull payments: the credit stays and can be withdrawn later; re-entry pays once | `test_core_revertingBuyer_keepsCredit`, `test_pool_revertingSeller_keepsCredit`, `test_core_reentrantBuyer_paidOnceExactly` |
| **Scenes are published late** | a scene appears after the pool stops accepting passes | scenes published after `end + publicationLag + cleanWindow` are lost (stated limit); a final share that the covenant can no longer police is not scheduled (`FinalOutOfTerm`) | `test_attestCleanPass_poolClosing_reverts`, `test_finalInstallment_outOfTerm_notScheduled` |
| **Somebody revokes a published judge-sandbox key [SIM]** | the sandbox flow stops for that role | the operator registers the next key generation (`make sandbox-rekey`); the scripts pick the first valid generation; the main deployment is unaffected | [`contracts/README.md`](../contracts/README.md), "Decisions that differ from the spec (Sepolia deployment)" |

## Why no state can get stuck

Every non-terminal state has a deadline and a permissionless transition after it: core episodes through `finalize`
(invariant I5, `invariant_I5_noStuckState`), and every pool pass, challenge, corroboration and installment through
`countPass`, `finalizePass`, `voidPass`, `finalizeCorroboration`, `release`, `relapse` or `closeClaim` (P5, probed after
each stateful fuzz run in `contracts/test/invariants/IncentivePool.invariants.t.sol`). The only state that can wait
without a deadline is an UNFUNDED installment, and it ends with any top-up or a relapse; its sponsors' remainder waits
with it (stated in SECURITY.md, residual risk).
