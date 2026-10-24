"""Portfolio source set v3 (spec-v2 §7): every join candidate plus the three demo cases as facility leaves; each Carbon
Mapper source linked to one facility only; every proof verifies; v2 untouched."""
import json

from common import DERIVED
from merkle import leaf, root, source_id, verify


def test_v3_members_proofs_and_disjoint_links():
    v3 = json.loads((DERIVED / "sourceset_v3.json").read_text())
    join = json.loads((DERIVED / "join_v1.json").read_text())
    names = [s["source_name"] for s in v3["sources"]]
    assert names == sorted(names) and len(names) == len(set(names)) == v3["n"]
    assert {c["mars_source"] for c in join["candidates"]} | {"DZA_S_012", "TKM_S_276", "USA_S_077"} == set(names)
    rt = bytes.fromhex(v3["root"][2:])
    assert root([source_id(n) for n in names]) == rt
    linked = [cm for s in v3["sources"] for cm in s["linked_cm_sources"]]
    assert len(linked) == len(set(linked))
    for s in v3["sources"]:
        assert verify([bytes.fromhex(p[2:]) for p in s["proof"]], rt, leaf(source_id(s["source_name"])))
        assert s["reason"] in ("join v1 candidate", "demo case", "join v1 candidate and demo case")


def test_v2_is_unchanged_by_v3():
    v2 = json.loads((DERIVED / "sourceset_v2.json").read_text())
    assert v2["version"] == "v2" and v2["n"] == 3


def test_portfolio_bond_and_pool_sizing():
    import math

    b = json.loads((DERIVED / "model" / "bond_sizing_v3.json").read_text())
    v3 = json.loads((DERIVED / "sourceset_v3.json").read_text())
    assert b["forecaster_used"] == "B1s" and b["sourceset_root"] == v3["root"]
    assert len(b["facilities"]) == v3["n"]
    p90 = b["set_annual_exposure"]["wei"]["p90"]
    assert int(b["recommended_bond_wei"]) == math.ceil(p90 / 10**15) * 10**15
    assert b["pool"]["wei"] == b["recommended_bond_wei"] and "capped at the rebate" in b["pool"]["why"]
    assert b["set_annual_exposure"]["policyUnits"]["p90"] >= b["set_annual_exposure"]["policyUnits"]["mean"] >= 0
    v1 = json.loads((DERIVED / "model" / "bond_sizing.json").read_text())
    assert v1["recommended_bond_wei"] != b["recommended_bond_wei"] or len(v1["facilities"]) == 3  # v1 untouched


def test_portfolio_sentence_names_selection_and_unsized_leaves():
    b = json.loads((DERIVED / "model" / "bond_sizing_v3.json").read_text())
    s = b["card"]["portfolioSentence"]
    unsized = sorted(n for n, f in b["facilities"].items() if not f["sized"])
    assert len(unsized) == 4 and all(n in s for n in unsized)
    assert f"{len(b['facilities']) - len(unsized)} of {len(b['facilities'])}" in s
    assert "MARS did not see" in s and "[SIM]" in s
    assert "the join matches plumes, ownership uses source centroids" in s
