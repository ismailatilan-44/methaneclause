# Join v1 (frozen)

`join.py` implements `CRITERION.md`; its output `data/derived/join_v1.json` is frozen at commit 18043cd and keeps those
bytes. The Sepolia main covenant's policy commits to `joinVersionHash = sha256(join_v1.json)` =
`0xb5662f399cb6ff9a390a38d66daad2f930f50668c6a89fe306678e490a7051cf`, so the live covenant's policy hash
(`0x6a004cac…`, `data/derived/testnet_run.json`) is reproducible from this repository
(`pipeline/tests/test_join.py::test_sepolia_covenant_policy_is_reproduced_from_the_repo`).

**Why `criterion_sha256` in `data/derived/join_v1.json` differs from `sha256(CRITERION.md)` today.** The recorded hash
(`c2f01726…`) is the criterion text at freeze time. A later edit (for the public export) changed only the provenance
sentence of the header; rules 1–6 are byte-identical (the test pins the rules-section hash). The two sentences, with
private file paths replaced by a description:

- at freeze: "Derived from [a research script, private path] plus the fixes asked for in [an internal review, private
  path] (twin dedup, plume level)."
- now: "Derived from an exploratory join scan written during idea selection, plus the fixes an internal security review
  asked for (twin de-duplication, plume-level matching)."

`join.compute(...)` is the criterion as a pure function; it reproduces every candidate of the frozen file
(`test_committed_join_is_reproduced_from_committed_inputs`). Re-running `join.py` would rewrite the file with the
current criterion hash and break the Sepolia policy's reproduction: do not commit such a run.
