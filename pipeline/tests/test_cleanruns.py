"""Clean-run definition (spec §12) on tiny synthetic timelines; edge cases of the definition."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleanruns import (UPPER_BOUND_BP, build_units, cells_from_plumes, first_run, passes_for, summarise,  # noqa: E402
                       u_bp)


def P(*pairs):
    """('2024-01-01', 'D') -> detected pass, ('2024-01-02', '.') -> clean pass."""
    return [(d, flag == "D") for d, flag in pairs]


def test_fewer_than_two_detections_is_not_eligible():
    assert first_run(P(("2024-01-01", "D"), ("2024-01-02", "."), ("2024-01-03", "."), ("2024-01-04", "."))) is None


def test_basic_run_and_redetection():
    r = first_run(P(("2024-01-01", "D"), ("2024-01-05", "D"), ("2024-02-01", "."), ("2024-03-01", "."),
                    ("2024-04-01", "."), ("2024-05-01", "D"), ("2024-06-01", ".")))
    assert r["second_detection"] == "2024-01-05"
    assert r["run_start"] == "2024-02-01"
    assert r["qualifying_pass"] == "2024-04-01"
    assert r["run_length"] == 3
    assert r["next_pass"] == "2024-05-01" and r["next_pass_detected"] is True
    assert r["redetected_later"] is True and r["first_redetection"] == "2024-05-01"
    assert r["passes_after_qualifying"] == 2


def test_run_shorter_than_three_is_not_a_run_first_qualifying_run_wins():
    r = first_run(P(("2024-01-01", "D"), ("2024-01-02", "D"), ("2024-01-03", "."), ("2024-01-04", "."),
                    ("2024-01-05", "D"), ("2024-01-06", "."), ("2024-01-07", "."), ("2024-01-08", "."),
                    ("2024-01-09", "."), ("2024-01-10", "D")))
    assert r["run_start"] == "2024-01-06"
    assert r["qualifying_pass"] == "2024-01-08"
    assert r["run_length"] == 4
    assert r["next_pass"] == "2024-01-09" and r["next_pass_detected"] is False
    assert r["redetected_later"] is True and r["first_redetection"] == "2024-01-10"


def test_no_run_of_three_returns_none():
    assert first_run(P(("2024-01-01", "D"), ("2024-01-02", "D"), ("2024-01-03", "."), ("2024-01-04", "."),
                       ("2024-01-05", "D"))) is None


def test_clean_passes_before_second_detection_do_not_count():
    # three clean passes between detection 1 and 2 are not "after the second detection"
    assert first_run(P(("2024-01-01", "D"), ("2024-01-02", "."), ("2024-01-03", "."), ("2024-01-04", "."),
                       ("2024-01-05", "D"), ("2024-01-06", "."))) is None


def test_run_at_end_has_no_next_pass():
    r = first_run(P(("2024-01-01", "D"), ("2024-01-02", "D"), ("2024-01-03", "."), ("2024-01-04", "."),
                    ("2024-01-05", ".")))
    assert r["next_pass"] is None and r["next_pass_detected"] is None
    assert r["redetected_later"] is False and r["passes_after_qualifying"] == 0


def test_detection_not_in_obs_counts_as_pass():
    tl = {"obs": [["2024-01-01", "emi"], ["2024-01-03", "emi"], ["2024-01-04", "emi"], ["2024-01-05", "emi"]],
          "det": ["2024-01-01", "2024-01-02"]}
    units = build_units({"S": tl}, plumes=[])
    assert passes_for(units["S"]) == P(("2024-01-01", "D"), ("2024-01-02", "D"), ("2024-01-03", "."),
                                       ("2024-01-04", "."), ("2024-01-05", "."))


def test_twins_merge_into_one_cell():
    plumes = [{"source_name": "A_1000m", "plume_id": "p1"}, {"source_name": "A_250m", "plume_id": "p1"},
              {"source_name": "A_250m", "plume_id": "p2"}, {"source_name": "B_250m", "plume_id": "p3"}]
    cells = cells_from_plumes(["A_1000m", "A_250m", "B_250m", "C_alone"], plumes)
    assert sorted(sorted(c) for c in cells.values()) == [["A_1000m", "A_250m"], ["B_250m"], ["C_alone"]]


def test_cell_timeline_is_union_of_members():
    tls = {"A_1000m": {"obs": [["2024-01-01", "emi"], ["2024-01-02", "tan"]], "det": ["2024-01-01"]},
           "A_250m": {"obs": [["2024-01-02", "tan"], ["2024-01-03", "tan"]], "det": ["2024-01-02"]}}
    plumes = [{"source_name": "A_1000m", "plume_id": "p1", "scene_timestamp": "2024-01-01T10:00:00Z",
               "instrument": "emi"},
              {"source_name": "A_250m", "plume_id": "p1", "scene_timestamp": "2024-01-01T10:00:00Z",
               "instrument": "emi"},
              {"source_name": "A_250m", "plume_id": "p9", "scene_timestamp": "2024-01-02T10:00:00Z",
               "instrument": "tan"}]
    cells = cells_from_plumes(list(tls), plumes)
    units = build_units(tls, plumes, groups=cells)
    (cell,) = units.values()
    assert passes_for(cell) == P(("2024-01-01", "D"), ("2024-01-02", "D"), ("2024-01-03", "."))


def test_instrument_filter_uses_plume_instrument_for_detections():
    tls = {"S": {"obs": [["2024-01-01", "emi+tan"], ["2024-01-02", "tan"], ["2024-01-03", "emi"]],
                 "det": ["2024-01-01"]}}
    plumes = [{"source_name": "S", "plume_id": "p1", "scene_timestamp": "2024-01-01T10:00:00Z", "instrument": "tan"}]
    u = build_units(tls, plumes)["S"]
    assert passes_for(u, "tan") == P(("2024-01-01", "D"), ("2024-01-02", "."))
    assert passes_for(u, "emi") == P(("2024-01-01", "."), ("2024-01-03", "."))


def test_summarise_counts_and_rates():
    units = {
        "x": {"2024-01-01": ({"tan"}, {"tan"}), "2024-01-02": ({"tan"}, {"tan"}), "2024-01-03": ({"tan"}, set()),
              "2024-01-04": ({"tan"}, set()), "2024-01-05": ({"tan"}, set()), "2024-01-06": ({"tan"}, {"tan"})},
        "y": {"2024-01-01": ({"emi"}, {"emi"}), "2024-01-02": ({"emi"}, {"emi"}), "2024-01-03": ({"emi"}, set()),
              "2024-01-04": ({"emi"}, set()), "2024-01-05": ({"emi"}, set())},
        "z": {"2024-01-01": ({"emi"}, {"emi"})},
    }
    s = summarise(units)
    assert s["units"] == 3 and s["units_ge2_detections"] == 2 and s["with_run"] == 2
    assert s["redetected_later"] == 1 and s["redetected_later_share"] == 0.5
    assert s["with_next_pass"] == 1 and s["next_pass_detected"] == 1 and s["next_pass_detection_rate"] == 1.0
    assert s["no_next_pass"] == 1
    assert s["next_pass_by_instrument"] == {"tan": {"n": 1, "detected": 1}}


def test_upper_bound_table():
    assert len(UPPER_BOUND_BP) == 32
    assert u_bp(1) == 9750 and u_bp(3) == 7076 and u_bp(6) == 4593 and u_bp(10) == 3085 and u_bp(14) == 2316
    assert all(a > b for a, b in zip(UPPER_BOUND_BP, UPPER_BOUND_BP[1:]))
    with pytest.raises(ValueError):
        u_bp(0)


def test_twin_merge_is_bounded_by_radius():
    # A-B share p1 (1.1 km apart), B-C share p2 (1.1 km apart), A-C 2.2 km: one pair may merge, never all three
    coords = {"A": (0.0, 0.0), "B": (0.0, 0.0099), "C": (0.0, 0.0198)}  # (lat, lon); 0.0099 deg ~ 1.1 km
    plumes = [{"source_name": "A", "plume_id": "p1"}, {"source_name": "B", "plume_id": "p1"},
              {"source_name": "B", "plume_id": "p2"}, {"source_name": "C", "plume_id": "p2"}]
    cells = cells_from_plumes(["A", "B", "C"], plumes, coords=coords, max_km=1.5)
    assert sorted(len(v) for v in cells.values()) == [1, 2]
    unbounded = cells_from_plumes(["A", "B", "C"], plumes)
    assert sorted(len(v) for v in unbounded.values()) == [3]


def test_instrument_set_filter():
    tls = {"S": {"obs": [["2024-01-01", "GAO"], ["2024-01-02", "tan"], ["2024-01-03", "emi"]], "det": ["2024-01-01"]}}
    plumes = [{"source_name": "S", "plume_id": "p1", "scene_timestamp": "2024-01-01T10:00:00Z", "instrument": "GAO"}]
    u = build_units(tls, plumes)["S"]
    assert passes_for(u, {"tan", "emi"}) == P(("2024-01-02", "."), ("2024-01-03", "."))


def _unit(flags):
    """'DD...D.' -> unit with one pass per day; D = detected (tan)."""
    return {f"2024-01-{i + 1:02d}": ({"tan"}, {"tan"} if c == "D" else set()) for i, c in enumerate(flags)}


def test_base_rate_hazard_and_follow_up():
    units = {"a": _unit(".DD...D"), "b": _unit("DD.....")}
    s = summarise(units)
    # before the run: a = '.DD' (2/3), b = 'DD' (2/2) -> 4/5; without the two eligibility detections: 0/1
    assert s["base_rate_before_run"] == {"detected": 4, "passes": 5, "rate": 0.8, "ci95": s["base_rate_before_run"]["ci95"]}
    assert s["base_rate_before_run_excl_eligibility"]["detected"] == 0
    assert s["base_rate_before_run_excl_eligibility"]["passes"] == 1
    # after the qualifying pass: a = 'D' (hazard 1 at pass 1), b = '..' (clean at passes 1 and 2)
    h = {row["k"]: (row["at_risk"], row["detected"]) for row in s["hazard_after_run"]}
    assert h[1] == (2, 1) and h[2] == (1, 0)
    fu = {row["passes_after"]: (row["units"], row["redetected"]) for row in s["redetected_by_follow_up"]}
    assert fu["1"] == (1, 1) and fu["2"] == (1, 0)
    assert s["redetected_later_right_censored"] is True


def test_bounded_merge_is_order_independent():
    import random

    coords = {f"S{i}": (0.0, 0.004 * i) for i in range(8)}  # 0.44 km steps along a line
    plumes = [{"source_name": f"S{i}", "plume_id": f"p{i}"} for i in range(8)]
    plumes += [{"source_name": f"S{i + 1}", "plume_id": f"p{i}"} for i in range(7)]  # each neighbour pair shares one
    ref = cells_from_plumes(sorted(coords), plumes, coords=coords, max_km=1.5)
    rng = random.Random(7)
    for _ in range(20):
        names, pl = sorted(coords), list(plumes)
        rng.shuffle(names)
        rng.shuffle(pl)
        assert cells_from_plumes(names, pl, coords=coords, max_km=1.5) == ref


def test_card_sentence_is_built_from_numbers():
    from cleanruns import card_sentence

    hazard = [{"k": 1, "at_risk": 210, "detected": 47}, {"k": 2, "at_risk": 145, "detected": 27},
              {"k": 3, "at_risk": 108, "detected": 21}, {"k": 4, "at_risk": 80, "detected": 19},
              {"k": 5, "at_risk": 52, "detected": 10}]
    s = card_sentence(cells=897, next_detected=47, next_n=210, next_ci=[0.1693, 0.2863], hazard=hazard)
    assert s == ("A quiet run is weak evidence: in Carbon Mapper's catalogue (897 cells; all instruments, unqualified "
                 "passes) the pass after three non-detections still saw a plume 22 % of the time (47/210, 95 % CI "
                 "17–29 %), and the passes after longer quiet runs saw one 19–24 % of the time. That is why the refund "
                 "is capped at the rebate, paid in installments, and cancelled by the next attested detection.")


def test_committed_card_is_built_by_the_code():
    import json

    from cleanruns import UNIT_LABEL, card_sentence
    from common import DERIVED

    d = json.loads((DERIVED / "cleanruns.json").read_text())
    c, cell = d["card"], d["by_unit"]["cell"]
    assert c["unitLabel"] == UNIT_LABEL == "catalogue cells, not facilities; all instruments; unqualified passes"
    assert c["sentence"] == card_sentence(d["cells"], cell["next_pass_detected"], cell["with_next_pass"],
                                          cell["next_pass_detection_ci95"], cell["hazard_after_run"])
    assert "base_rate_before_run" not in json.dumps(c)  # no comparator inside the card (spec-v2 §15 after B1-fix)
