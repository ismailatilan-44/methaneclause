# Observer service and the walking skeleton

Spec §4 "Observer service", §11 `make demo`.

| File | Purpose |
|---|---|
| `bundle.py` | Carbon Mapper plume → canonical evidence bundle; validation before signing; JCS + SHA-256 = `evidenceHash` |
| `attest.py` | EIP-712 `Attestation` (same type string and domain as `contracts/src/lib/Attestation.sol`), sign / recover |
| `service.py` | `Observer(org)`: fetch (snapshot, or `LIVE=1` Carbon Mapper now) → bundle → validate → hash → sign; CLI |
| `demo.py` | End-to-end run on Anvil, called by `scripts/demo.sh` (`make demo`) |
| `cleanpass.py` | v2 clean-pass observer: Fetch → Validate → Seal → Sign → Re-check over a committed candidate scene; §4 bundle, JCS → `sceneHash`, EIP-712 `CleanPass` + ML-DSA-44; MARS `Corroboration` builder / signer |
| `adversarial.py` | The adversarial input set for detections and clean passes → `data/derived/adversarial.json` |

```sh
make demo          # committed snapshot, offline
LIVE=1 make demo   # both observers fetch Carbon Mapper themselves
```

`make demo` checks 33 things and exits non-zero if any fails: the covenant's backdated start is visible on-chain
(`start` < plume published < `createdAt`); the two observers build the same bundle hash (in the default snapshot mode
both read one committed file, so this is labelled [SIM]; the independence claim is the `LIVE=1` run); the Python
digest equals the contract's `attestationDigest`; the tampered bundle (2,548 → 548 kg/h), the seller's own key, a
batch containing a dissenting observer, and an episode keyed by a Carbon Mapper twin source each produce a **failed
transaction** (receipt status 0; the reason name — `InvalidSignature`, `SelfAttestation`, `AttestationMismatch`,
`NotCovered` — is read from an `eth_call` simulation of the same call) with the bond, the episode count and the
observer's nonce bitmap snapshotted before and asserted equal after; the real 2-of-3
proposal goes PROPOSED → (mitigation claim: no effect; early finalize: `WindowOpen`) → CONFIRMED; the buyer withdraws
the tier-2 rebate; bond conservation holds. Then the spec §9 rejection scene: the same attestation twice in a batch →
`Replay`; the identical proposal resubmitted (an RPC retry) → `DuplicateEvidence`, episode count unchanged; OBS_B's org
revokes its own key (no admin exists after deployment) and OBS_B's next attestation → `RevokedKey`; OBS_A + OBS_C
attest the 2026-06-20 plume, 9 days after the paid episode → `LINKED` to episode 1, recorded, nothing paid or held.
Gas and tx hashes go to `out/demo/run.json` (gitignored).

## Clean-pass observer (`cleanpass.py`, spec-v2 §2, §3.1, §4)

```sh
cd pipeline
.venv/bin/python -m observer.cleanpass --case DZA_S_012 --scene "2025-02-12 emi" --org OBS_A   # five steps + JSON
.venv/bin/python -m observer.cleanpass --case DZA_S_012 --scene "2025-07-08 tan"               # refused: NO_ORIGIN_INTERSECT
.venv/bin/python -m observer.cleanpass --case DZA_S_012 --corroborate --org OBS_C               # MARS Corroboration
LIVE=1 .venv/bin/python -m observer.cleanpass --scene "2025-02-12 emi"   # Re-check against Carbon Mapper now (~40 keyless requests)
```

The rules are imported from `pipeline/cleanpass_rules.py` (one copy, shared with the case files, including the
attestation-time formula `attestable_from` and `cloud_bp`). Each step prints `[i/5 <label>]` with the labels of the
page.
- **Validate** refuses with a spec-v2 §2 code; with `SCHEMA` when the bundle is not exactly the §4 field set with the
  right types, `sceneId != keccak256(sceneProviderId)`, a per-source `plumeCount` is negative, or
  `plumesNearFacility.count != len(plumeIds)`; with `BUNDLE_POLICY_MISMATCH` when the bundle's floor or
  `marsNearby.windowDays` differs from the policy. Rule 3 needs every per-source count to be 0 (not just the sum). A
  refused scene is never signed.
- **Sign** makes the EIP-712 `CleanPass` (via `attest.pool_sign`, kind 4, expiry ≤ now + 1 day) and an ML-DSA-44
  signature over the same JCS bytes.
- **Re-check** rebuilds the scene record from the cached raw responses (`LIVE=1`: keyless Carbon Mapper now, into a
  temporary cache) and decides EQUAL / DIFFERS on `scene_view`: every rule-relevant bundle field plus the plume ids and
  quantities on this scene at the facility (per linked source and within 1.5 km). The whole-response hashes
  (`perSourceQueries[].responseSha256`, `plumesNearFacility.query`, `sceneRecordSha256`) stay in the bundle as
  provenance and are not compared, so a plume added elsewhere at the source does not flip an honest pass, while a plume
  added on this scene does (spec-v2 §15 B2 audit (1)). A cache file whose bytes or content hash no longer match its
  recorded hashes is reported as DIFFERS. On DIFFERS the observer withholds the signature (`RECHECK_DIFFERS`).
  On 2026-10-15 the live re-check of DZA 2025-02-12 was equal.
- **Corroboration** covers `[since, latest counted sceneAt]` against the plumes MARS attributes to the facility in the
  pinned snapshot (`marsSnapshotHash`). It refuses a MARS plume in the window (`MARS_PLUME_IN_WINDOW`) and a claim
  with no counted pass (`NO_COUNTED_PASS`), and applies the chain's checks before signing: expiry within 1 day of
  `now`, `windowFrom ≤ since`, `windowFrom ≤ windowTo`.

**Entry points for other code (the demo and the page).** All offline, from committed files only:

```python
from observer import cleanpass as cp
bundle, jcs_bytes, scene_hash = cp.bundle_for("DZA_S_012", "2025-02-12 emi", covenant_id=COV, claim_id=CLAIM)
doc = cp.load_case("DZA_S_012"); rec = cp.find_scene(doc, "2025-02-12 emi")   # scene by "date inst", name or id
b = cp.build_bundle(doc, rec, covenant_id=COV, claim_id=CLAIM)                 # the §4 bundle (dict)
cp.bundle_hash(b)                                                              # "0x" + SHA-256(JCS(b)) = sceneHash
cp.validate(b, cp.claim_context(doc, now=chain_now))                           # raises cp.CleanPassRefused(code)
s = cp.sign(b, org="OBS_A", chain_id=31337, pool=POOL, ctx=..., nonce=None, expiry=None, pq_deterministic=False)
cp.verify(s, chain_id=31337, pool=POOL)                                        # {"ecdsa": bool, "mldsa": bool}
cp.message(b, nonce=n, expiry=e)                                               # EIP-712 message, §3.1 field order
cp.recheck(doc, rec, covenant_id=COV, claim_id=CLAIM, bundle=s["bundle"])    # {"equal", "differences", ...}
c = cp.build_corroboration(doc, claim_id=CLAIM)
cp.sign_corroboration(c, org="OBS_C", chain_id=..., pool=..., now=chain_now, expiry=chain_now + 3600)
```

Tamper C (spec-v2 §8.4) is `cp.verify` after editing `cloudCoverPct` / `notCloudy` in `s["bundle"]`: `sceneHash`
changes, so both the ECDSA and the ML-DSA-44 checks fail (`data/derived/adversarial.json`, case "cloud fields edited after signing").

**Determinism hooks (opt-in, for a repeatable demo only; defaults stay random; refused on any chain but 31337).**
- `Observer.attest(..., nonce=None, pq_deterministic=False)` (v1 detections), `cleanpass.sign(...)` /
  `CleanPassObserver.attest(...)` (same two parameters) and `sign_corroboration(..., nonce=None)`: `nonce=None` draws
  a random 64-bit nonce; an int pins it. Any opt-in on a chain other than Anvil (31337) raises `ValueError`
  (`service.require_anvil_for_determinism`).
- `pq.sign(sk, message, deterministic=False)`: the default is FIPS 204 hedged signing (32 fresh random bytes);
  `deterministic=True` is FIPS 204's deterministic variant (rnd = 32 zero bytes), exposed by dilithium-py. Both verify
  with the same public key. `tests/test_determinism.py` proves the defaults differ run to run and the opt-in repeats.
Every tool, judge and Sepolia path uses the defaults.

## Decisions that differ from the spec text

- **sourceId is the facility** (`keccak256(MARS source_name)`, e.g. `TKM_S_276`), not the Carbon Mapper source. The
  Merkle set (`data/derived/sourceset_v2.json`) has one leaf per facility; the CM sources linked to it are metadata.
  The bundle names the CM source (`cmSourceName`), and the observer checks it is one of the facility's linked CM
  sources and that the plume origin is within 1.5 km of the MARS point. Reason: on 2026-06-11 Tanager saw six plumes
  around TKM_S_276 under four CM source names; keyed by facility, they share one `linkWindow` anchor and cannot pay
  six times, and the contract enforces it (a CM source id is `NotCovered`).
- **`retrievedAt` is not in the hashed bundle.** k observers fetching at different moments must produce the same
  `evidenceHash`, so the bundle holds only provider-derived fields plus `providerContentSha256` (the stable hash of
  the Carbon Mapper response without its expiring signed URLs). Each observer keeps its own receipt (`retrievedAt`,
  raw-bytes hash, URL) in `out/demo/run.json`.
- **Backdated covenant start, recorded on-chain.** The contract accepts only detections after the covenant start, and
  Carbon Mapper publishes Tanager plumes ~30 days after the scene, so no plume acquired after a covenant created today
  could be shown before the deadline. `createCovenant` therefore takes an optional `start` up to `MAX_LOOKBACK` (365
  days) in the past; it is inside `policyHash`, and the contract stores `createdAt`. The demo backdates to 2026-01-01
  [SIM]. Say it once, plainly: *this run proves the mechanism, not the absence of foreknowledge — the chain shows the
  plume was public before the covenant was created.* The same flow works on Base Sepolia (no clock tricks).

### Clean passes (spec-v2 §2 rules 1-6; `pipeline/cleanpass_rules.py`, `pipeline/clean_passes.py`, `pipeline/scenes.py`)

- **Per-source plume query = `/catalog/search/source/{name}`, not `/catalog/plumes/annotated?source_name=`.** On
  2026-10-15 the plumes endpoint ignored `source_name` and returned the whole catalogue (`total_count` 45,227). The
  per-source response lists every plume of that source with its `scene_id`; rule 3 counts that source's plumes whose
  `scene_id` is the scene's, or whose date and instrument match it. The bundle field shape (`perSourceQueries: [{cmSource,
  url, params, responseSha256, plumeCount}]`) is the spec's. Cost: one response per source serves every scene; its
  hash changes whenever Carbon Mapper adds a plume to that source, so a later re-check compares plume ids, not hashes.
- **Rule 3 also refuses a scene plume whose origin lies within 1.5 km of the facility**, even if Carbon Mapper filed it
  under a source not linked to the facility (`plumesNearFacility`, from `/catalog/plumes/annotated?scene_id=`; skipped
  when `published_plume_count == 0`, the spec's fast path). Stricter than the spec, never looser: a plume at the
  facility under another source name is still a plume at the facility. In the three case files it never changed an
  outcome (it agreed with the per-source check on every scene).
- **Rule 2 has no look-ahead (spec-v2 §15, 2026-10-16).** The footprint (server-side `intersects`) must contain an
  origin point whose earliest plume was already published when the pass could first be attested
  (`max(sceneAt + publicationLag, publishedAt)`). Where only the MultiPoint query was made and some origin was
  published later, the answer is undetermined offline and the scene is refused (fail closed; TKM 2026-06-11 / 06-20,
  refused by other rules anyway). `qualifiedAllOriginsToday` shows the same scene against every origin known today,
  for comparison only. It differs at DZA_S_012: the 2025-07-08 and 2025-09-23 Tanager scenes contain only the
  2026-02-01 relapse plume's origin (published 2026-03-03), so DZA counts 8, not 10.
- **Every count is a lower bound** (`countIsLowerBound`): a lower bound on what an observer at each pass's time would
  have counted (they saw at most today's plumes); re-checking later can only lower it. Rules 3 and 4 are checked
  against today's catalogue and the pinned MARS snapshot.
- **The §4 bundle carries one extra field, `plumesNearFacility`** (`{radiusKm, count, plumeIds, query}`), so the
  stricter rule 3 above can be re-checked from the bundle. `floorKgH` = `rateFloorKgH` = the instrument's floor in
  POLICY_V2 (the contract checks `rateFloorKgH ≤` the episode rate); `originPoints` are the origins published by the
  pass's first attestation time (no look-ahead), so k observers build the same bytes whatever their own clock.
- **Observer-side codes beyond the §2 list:** `SCHEMA` (malformed bundle), `BUNDLE_POLICY_MISMATCH` (bundle floor or
  MARS window differs from the policy), `RECHECK_DIFFERS` (step 5), `MARS_PLUME_IN_WINDOW` and `NO_COUNTED_PASS`
  (Corroboration). They never reach the chain: a refused input is not signed.
- **`now` in the case files is the scene query's fetch time** (`evaluatedAt`), so the files reproduce offline from the
  cache. A scene inside the 60-day publication lag at that moment is refused `BEFORE_PUBLICATION_LAG` (USA 2026-08-25).
- **Cloud rule:** Carbon Mapper's `not_cloudy` agreed with `max(cloud_cover_pct, cloud_cover_pct_assessed) ≤ 25` on
  all 27 case scenes; no `CLOUD_FLAG_MISMATCH`. (It does not equal "assessed ≤ 25": 2025-11-11 at DZA is 42 / 0 and
  `not_cloudy = false`.)

## Known limits of the observer prototype

- A batch with a dissenting observer reverts, but with k = 2 of n = 3 a proposer can leave the dissenter out. That is
  the k-of-n trade-off, not a bug; `challenge` (objective reasons) and `contest` (scientific disputes) are the answer.
- The observer's duplicate-plume check is per process. Across runs the chain is the dedup: `evidenceSeen` rejects an
  identical bundle and a re-quantified re-fetch of the same plume lands as LINKED (no second payout).
- Attestations expire 1 hour after signing (contract maximum is 1 day). The CLI and the judge flow read the
  covenant start and minimum rate from `getCovenant` and "now" from the latest block; the contract still fails
  closed if a signer gets them wrong (`DetectionOutOfTerm`, `BelowMinRate`).

## Limits (say them the same way everywhere)

Observers attest what they received, from whom and when, not what the satellite saw. In the prototype all three
observer keys are the author's (Anvil test keys locally), so k-of-n is a mechanism demo. The disagreement case is
[SIM] (an override of one field). Seller and buyer are [SIM].
