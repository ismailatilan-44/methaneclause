# Frozen case snapshot of the Sepolia v1 covenant

`TKM_S_276.json` here is byte-for-byte the case file that the live Ethereum Sepolia v1 covenant committed to: its
SHA-256 is the policy's `snapshotManifestHash`, and the policy's hash is the covenant's on-chain `policyHash`
(`data/derived/testnet_run.json`). `pipeline/observer/demo.py` (`covenant_policy`) hashes this file, so
`pipeline/tests/test_join.py::test_sepolia_covenant_policy_is_reproduced_from_the_repo` keeps reproducing that hash.

Do not regenerate or edit it. The current case file is [`../TKM_S_276.json`](../TKM_S_276.json).

What changed since: in this snapshot `post_mars_last_plume` is the union over every linked Carbon Mapper source,
including one centred outside the facility radius (`RADIUS_KM` in `pipeline/common.py`). The current file counts on
the facility-radius basis (`facility_post` in `pipeline/cases.py`) and keeps that union as
`post_mars_last_plume_all_linked`, labelled secondary. Plume records and source response hashes are the same in both.
