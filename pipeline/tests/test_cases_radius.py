"""The headline count of a demo case uses the facility-radius basis: only linked Carbon Mapper sources whose centre is
within RADIUS_KM of the MARS point, and only on dates in those sources' own observation lists. The union over every
linked source stays in the case file as a labelled secondary field, never as the headline."""
from __future__ import annotations

import json

from cases import facility_post
from common import DERIVED, RADIUS_KM

CASES = ("TKM_S_276", "DZA_S_012", "USA_S_077")


def _case(name: str) -> dict:
    return json.loads((DERIVED / "cases" / f"{name}.json").read_text())


def test_facility_post_drops_far_sources_and_detections_off_the_source_list():
    sources = {
        "near": {"dist_km": 0.2, "obs": ["2025-01-01", "2025-02-01", "2025-03-01"], "det": ["2025-02-01", "2025-04-01"]},
        "edge": {"dist_km": RADIUS_KM, "obs": ["2025-03-01"], "det": ["2025-03-01"]},
        "far": {"dist_km": RADIUS_KM + 0.001, "obs": ["2025-02-15"], "det": ["2025-02-15"]},
    }
    got = facility_post(sources, "2025-01-01")
    assert got["cm_sources"] == ["edge", "near"]
    # 2025-01-01 is not after the label; 2025-02-15 is only on the far source; 2025-04-01 is a detection date that is
    # not on the near source's own observation list, so it is neither a pass nor a detection
    assert (got["n_cm_passes"], got["n_cm_detected"]) == (2, 2)
    assert got["detections"] == ["2025-02-01", "2025-03-01"]


def test_counted_sources_lie_within_the_facility_radius():
    for name in CASES:
        c = _case(name)
        pm, src = c["post_mars_last_plume"], c["cm_sources"]
        assert pm["radius_km"] == RADIUS_KM
        assert pm["cm_sources"], name
        assert all(src[n]["dist_km"] <= RADIUS_KM for n in pm["cm_sources"]), name
        # every linked source within the radius is counted, none beyond it
        assert sorted(pm["cm_sources"]) == sorted(n for n, v in src.items() if v["dist_km"] <= RADIUS_KM), name
        # the headline is recomputable from the committed per-source date lists
        per = {n: {"dist_km": v["dist_km"], "obs": v["post_mars_last_plume"]["passes"],
                   "det": v["post_mars_last_plume"]["detections"]} for n, v in src.items()}
        again = facility_post(per, pm["since"])
        assert {k: again[k] for k in ("n_cm_passes", "n_cm_detected", "detections")} == \
               {k: pm[k] for k in ("n_cm_passes", "n_cm_detected", "detections")}, name


def test_tkm_is_nine_of_nine_on_the_facility_basis_and_the_union_stays_secondary():
    c = _case("TKM_S_276")
    pm, union = c["post_mars_last_plume"], c["post_mars_last_plume_all_linked"]
    assert (pm["since"], pm["n_cm_detected"], pm["n_cm_passes"]) == ("2025-06-13", 9, 9)
    assert "2026-04-17" not in pm["detections"]  # only the 1.774 km source saw a plume that day
    assert "CH4_1B2_1000m_53.71356_39.47100" not in pm["cm_sources"]
    assert c["cm_sources"]["CH4_1B2_1000m_53.71356_39.47100"]["dist_km"] > RADIUS_KM
    assert (union["n_cm_detected"], union["n_cm_passes"]) == (10, 10)
    assert "2026-04-17" in union["detections"]
    assert "secondary" in union["label"]
