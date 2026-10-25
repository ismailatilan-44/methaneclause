"""Frozen join criterion v1 (pipeline/join/CRITERION.md) on tiny synthetic data: twins, window edges, the strict
"after MARS's last plume" rule, radius edges and nearest-target ownership. Plus: the committed join_v1.json is what
the code produces from the committed inputs, and the radius-sensitivity set is current."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from common import DERIVED, RADIUS_KM
from join.join import compute

KM_PER_DEG = 111.19508  # haversine R = 6371.0088 km, along a meridian
AS_OF = "2026-09-12"


def mars(*rows):
    base = {"sector": "Oil and Gas", "notified": "Yes", "persistency_category": "absent", "country": "X",
            "source_type": "t", "feedback": "No", "n_plumes_detected": 1, "last_plume_date": "2026-01-01T00:00:00"}
    return pd.DataFrame([{**base, **r} for r in rows])


def cm(*rows):
    return pd.DataFrame([{"source_name": n, "source_latitude": lat, "source_longitude": lon} for n, lat, lon in rows])


def plumes(*rows):
    return pd.DataFrame([{"source_name": s, "plume_id": pid, "scene_timestamp": f"{day}T10:00:00Z", "instrument": "tan",
                          "emission_auto": 1000.0, "emission_uncertainty_auto": 100.0, "emission_version": "v3",
                          "lat": lat, "lon": lon} for s, pid, day, lat, lon in rows])


M = mars({"source_name": "M1", "lat": 0.0, "lon": 0.0})


def run(p, m=M, c=None, radius_km=RADIUS_KM):
    c = cm(("A_1000m", 0.0, 0.0), ("A_250m", 0.001, 0.0)) if c is None else c
    return compute(m, c, p, AS_OF, radius_km=radius_km)


def test_twins_count_once():
    counts, cands = run(plumes(("A_1000m", "p1", "2026-06-01", 0.0, 0.0), ("A_250m", "p1", "2026-06-01", 0.0, 0.0)))
    assert counts["n_twin_duplicate_plume_rows_removed"] == 1
    assert counts["n_candidate_plumes"] == 1 and counts["n_candidates"] == 1


@pytest.mark.parametrize("day, counted", [("2026-03-11", False), ("2026-03-12", True), ("2026-09-12", True),
                                          ("2026-09-13", False)])
def test_window_edges_are_inclusive(day, counted):
    counts, _ = run(plumes(("A_1000m", "p1", day, 0.0, 0.0)))
    assert counts["window"] == ["2026-03-12", AS_OF]
    assert (counts["n_candidate_plumes"] == 1) is counted


@pytest.mark.parametrize("day, counted", [("2026-04-30", False), ("2026-05-01", True)])
def test_plume_must_be_after_mars_last_plume_day(day, counted):
    m = mars({"source_name": "M1", "lat": 0.0, "lon": 0.0, "last_plume_date": "2026-04-30T23:59:59"})
    counts, _ = run(plumes(("A_1000m", "p1", day, 0.0, 0.0)), m=m)
    assert (counts["n_candidate_plumes"] == 1) is counted


@pytest.mark.parametrize("km, radius, counted", [(1.49, 1.5, True), (1.51, 1.5, False), (1.51, 2.0, True),
                                                 (0.99, 1.0, True), (1.01, 1.0, False)])
def test_plume_origin_radius(km, radius, counted):
    counts, _ = run(plumes(("A_1000m", "p1", "2026-06-01", km / KM_PER_DEG, 0.0)), radius_km=radius)
    assert (counts["n_candidate_plumes"] == 1) is counted


def test_cm_source_outside_radius_is_not_in_scope():
    c = cm(("FAR", 1.6 / KM_PER_DEG, 0.0))
    counts, _ = run(plumes(("FAR", "p1", "2026-06-01", 0.0, 0.0)), c=c)
    assert counts["n_cm_sources_within_radius"] == 0 and counts["n_candidate_plumes"] == 0


def test_plume_belongs_to_the_nearest_target_only():
    m = mars({"source_name": "M1", "lat": 0.0, "lon": 0.0}, {"source_name": "M2", "lat": 2.0 / KM_PER_DEG, "lon": 0.0})
    counts, cands = run(plumes(("A_1000m", "p1", "2026-06-01", 1.2 / KM_PER_DEG, 0.0)), m=m)
    assert [c["mars_source"] for c in cands] == ["M2"]


def test_non_target_mars_sources_are_ignored():
    m = mars({"source_name": "M1", "lat": 0.0, "lon": 0.0, "persistency_category": "persistent"})
    counts, _ = run(plumes(("A_1000m", "p1", "2026-06-01", 0.0, 0.0)), m=m)
    assert counts["n_mars_target_sources"] == 0 and counts["n_candidates"] == 0


def test_committed_join_is_reproduced_from_committed_inputs():
    """The v1 numbers ("~203") come out of the committed derived inputs unchanged."""
    from join.join import inputs

    import hashlib

    from join.join import CRITERION

    committed = json.loads((DERIVED / "join_v1.json").read_text())
    summary = committed["summary"]
    counts, cands = compute(*inputs(), radius_km=RADIUS_KM)
    for k in ("n_candidates", "n_candidate_plumes", "n_candidate_cm_sources", "n_mars_target_sources",
              "n_cm_sources_within_radius", "n_twin_duplicate_plume_rows_removed"):
        assert counts[k] == summary[k], k
    as_json = json.loads(json.dumps(cands, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    assert as_json == committed["candidates"]  # every candidate, field by field
    # criterion_sha256 is the freeze-time text (the header was edited later); the rules section is unchanged since then
    dq = json.loads((DERIVED / "data_quality.json").read_text())["joinCriterionHash"]
    assert summary["criterion_sha256"] == dq["recordedInJoin"] and dq["freezeTimeText"] is True
    rules = CRITERION.read_text().split("## Rules", 1)[1]
    assert hashlib.sha256(rules.encode()).hexdigest() == dq["rulesSectionSha256"]


def test_sepolia_covenant_policy_is_reproduced_from_the_repo():
    """The live Sepolia covenant committed to sha256(join_v1.json) inside its policy; the frozen bytes must stay."""
    import hashlib

    from observer.demo import covenant_policy

    root = json.loads((DERIVED / "sourceset_v2.json").read_text())["root"]
    _, _, pol, policy_hash, _ = covenant_policy({"root": root}, "0x" + "11" * 20)
    assert policy_hash == json.loads((DERIVED / "testnet_run.json").read_text())["policyHash"]
    assert pol["joinVersionHash"] == "0x" + hashlib.sha256((DERIVED / "join_v1.json").read_bytes()).hexdigest()
    assert pol["joinVersionHash"] == "0xb5662f399cb6ff9a390a38d66daad2f930f50668c6a89fe306678e490a7051cf"


def test_quality_label_sentence_comes_from_the_data():
    dq = json.loads((DERIVED / "data_quality.json").read_text())
    q = dq["plumeQualityLabel"]
    assert q["rows"] == q["emptyRows"] and q["rows"] > 0
    assert q["sentence"] == (f"Carbon Mapper's API v1 returned no plume_quality label (empty in all {q['rows']:,} "
                             "rows), so no quality filter is applied.")


def test_radius_sensitivity_file_is_current():
    from join.join import inputs

    dq = json.loads((DERIVED / "data_quality.json").read_text())["joinRadiusSensitivity"]
    assert [r["radiusKm"] for r in dq["rows"]] == [1.0, 1.5, 2.0]
    for r in dq["rows"]:
        counts, _ = compute(*inputs(), radius_km=r["radiusKm"])
        assert counts["n_candidates"] == r["n_candidates"]
    assert "lower bound" in dq["rows"][2]["note"]


def test_quality_flags_definitions():
    from data_quality import flags

    nan = float("nan")
    rows = [{"plume_id": "a", "emission_auto": None, "emission_uncertainty_auto": None, "plume_quality": None,
             "emission_version": "v3", "processing_software": "3.1"},
            {"plume_id": "a", "emission_auto": 5.0, "emission_uncertainty_auto": 1.0, "plume_quality": "good",
             "emission_version": "v3", "processing_software": "3.1"},  # twin row: counted once (first kept)
            {"plume_id": "b", "emission_auto": 100.0, "emission_uncertainty_auto": 150.0, "plume_quality": nan,
             "emission_version": nan, "processing_software": None},
            {"plume_id": "c", "emission_auto": 100.0, "emission_uncertainty_auto": 100.0, "plume_quality": "good",
             "emission_version": "v3e", "processing_software": "3.2"}]
    f = flags(rows)
    assert f["plumes"] == 3 and f["hidden_emission"] == 1 and f["uncertainty_exceeds_rate"] == 1
    assert f["plume_quality"] == {"(empty)": 2, "good": 1}
    assert f["emission_version"] == {"(empty)": 1, "v3": 1, "v3e": 1}


def test_join_guard_keeps_equal_content_and_refuses_a_change(tmp_path):
    from join.join import keep_or_refuse

    out = tmp_path / "join_v1.json"
    summary = {"criterion_sha256": "aa", "n_candidates": 1}
    cands = [{"mars_source": "M1", "n_plumes": 2}]
    assert keep_or_refuse(out, summary, cands) is False  # no file yet: the caller writes it
    out.write_text(json.dumps({"summary": summary, "candidates": cands}))
    before = out.read_bytes()
    assert keep_or_refuse(out, {**summary, "criterion_sha256": "bb"}, cands) is True  # only the criterion hash moved
    assert out.read_bytes() == before
    with pytest.raises(SystemExit):
        keep_or_refuse(out, {**summary, "n_candidates": 2}, cands)
    with pytest.raises(SystemExit):
        keep_or_refuse(out, summary, [{"mars_source": "M1", "n_plumes": 3}])
    assert out.read_bytes() == before
