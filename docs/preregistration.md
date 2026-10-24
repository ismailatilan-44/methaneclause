# Pre-registration of the forward hold-out

The carbon projection's headline is a forward hold-out: portfolio members chosen only with what was known on a date D,
credited only for faults first detected after D. Its rules were written down and committed **before** any forward
value was computed, as a Python constant with a test that pins its SHA-256. This page lists what was fixed, the hash of
each block, the test that pins it and the commits that fixed it and first computed it.

## What was fixed

| Block | What it fixes | SHA-256 of the block | Pinned by |
|---|---|---|---|
| `projection_time.FORWARD` ([`pipeline/projection_time.py`](../pipeline/projection_time.py)) | D = 2025-09-12 and the credit end 2026-09-13 (with why each); the universe; the member rule (MARS knew the source at D and saw no plume in the six months before it; a Carbon Mapper plume published before D); the stake and the re-onset hazard read only up to D; credit only for events first detected after D; the denominator; the band; the selecting-plume share, zero by construction and checked; three sensitivity rows; three disclosed leaks; and that no threshold here decides the headline | `7a53280eed63565eda3987f185c813ebbf2d1afa037fe03dc37f6433b897d2e2` | `test_the_pre_registration_is_byte_for_byte_the_committed_one` |
| `projection_time.FORWARD_ROLLING` (same file) | three more origins, 2025-03-12, 2025-06-12 and 2025-12-12, under FORWARD's rules unchanged with D replaced by each origin; what is reported per origin and pooled; reported only | `31a934171fe94b0d35deed19e5fab59c4c4ac5ed1d07601ab59deae30a4a02b3` | `test_the_rolling_origin_pre_registration_is_byte_for_byte_the_committed_one` |
| `projection_time.FORWARD_CONTROL_LENGTH` (same file) | a control with a credit window as long as FORWARD's, 2025-03-12 to 2026-03-11, ending before today's absent window, under FORWARD's rules; and the rule that decides, before its value was known, which of two readings the limits line states; reported only | `9e5feba8f94153bcd4c55cb7c3d3cbfa124d6bd6f9b2dd12999b3dece560b2dd` | `test_the_full_length_control_rule_is_byte_for_byte_the_committed_one` |

The hash is `sha256(json.dumps(block, sort_keys=True, ensure_ascii=False))`; both tests are in
[`pipeline/tests/test_projection_forward.py`](../pipeline/tests/test_projection_forward.py) and run in `make test`, so
any edit to a block after it was registered fails the build. FORWARD_ROLLING and FORWARD_CONTROL_LENGTH were written
after FORWARD's result was known, and say so in their own `status` fields; they are robustness checks, not the
headline, and each was committed before its own values were computed. Rows added after the result
(a member bootstrap, a late-publication leak row, a short-window control) are kept apart in `projection.json` under
`forward.postResult`, marked as added after the result, and never change the registered figure.

To re-check a hash yourself:

```sh
cd pipeline && .venv/bin/python -c "import hashlib, json, projection_time as p; \
print(hashlib.sha256(json.dumps(p.FORWARD, sort_keys=True, ensure_ascii=False).encode()).hexdigest())"
```

## The commit record

| Commit | SHA | Authored (UTC) | Committed (UTC) |
|---|---|---|---|
| FORWARD pre-registered | `3eff4bf470168ef41fa2936a318fafb3a69e1d80` | 2026-10-24T17:55:32Z | 2026-10-24T18:19:54Z |
| FORWARD first computed | `1419b3144bc4f27ff190f09e4cc63c2873654ef6` | 2026-10-24T18:04:10Z | 2026-10-24T18:19:54Z |
| FORWARD_ROLLING pre-registered | `314fba7921fe9534805d71dd1e296503582868c7` | 2026-10-24T18:57:46Z | 2026-10-24T19:06:06Z |
| FORWARD_ROLLING as first committed, before a rebase | `c6197480cbb229e8fefc4634065e10c7c7f28d9a` | 2026-10-24T18:57:46Z | 2026-10-24T18:57:46Z |
| FORWARD_ROLLING first computed | `a4c39c7803328bc486af9c5f347e49fce5afa894` | 2026-10-24T18:59:47Z | 2026-10-24T19:06:06Z |
| FORWARD_CONTROL_LENGTH pre-registered | `147b220c4b8b2752461709faec704fea6236f6dc` | 2026-10-24T20:28:14Z | 2026-10-24T20:45:40Z |
| FORWARD_CONTROL_LENGTH first computed | `df7c1bcfb6cca3c791cc6c0837aa1f187689f7e2` | 2026-10-24T20:40:15Z | 2026-10-24T20:45:40Z |

Each pre-registration commit changes only the block, its hash test and the source-file hashes that `projection.json`
and `audit_numbers.json` record for `pipeline/projection_time.py`; no member, stake, event or tonnage. Each "first
computed" commit is a descendant of its pre-registration commit. "Authored" is when a commit was first made;
"Committed" is when it was last applied: each pair was rebased onto the main line together, so it shares a commit
time, and the order is the parent chain plus the authored times.

**Where the record lives.** The public repository is a single-commit export of the development repository, so it
carries the blocks, their hashes and the pinning tests, but not the commit history. The order above is attested by the
development repository, available to the judges on request; there, `make test` also checks each listed time and the
parent order against git (`test_preregistration_doc_matches_the_pinned_blocks_and_the_commit_record` in
[`scripts/tests/test_build_app_data.py`](../scripts/tests/test_build_app_data.py)).

**Next step: an on-chain timestamp.** Git times are written by the author's own machine. The next pre-registration
puts its block hash in an Ethereum Sepolia transaction before any value is computed, so its date is fixed by the chain
rather than by the author, the same way the clause anchors its evidence.
