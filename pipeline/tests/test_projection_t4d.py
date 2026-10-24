"""Board T4d's unpublished time factor (projection.json -> t4d; pre-registered in projection_sources.json -> model.t4d).

Review finding 8: the checks here are computed independently of projection_time.py (their own event loop, credit,
hazard and lag code on the committed rows), plus synthetic facilities for a censored final event, a single-pass
facility and the order of removed time across conventions. Offline: committed files only."""
from __future__ import annotations

import copy
import csv
import json
import math
import re
from datetime import date
from pathlib import Path

import pytest

import projection
import projection_time as ptime

ROOT = Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data" / "derived"
DAYS_PER_YEAR = 365.25
REVIEW_CLASSES = ["allowed operation (Type B)", "maintenance (Type C)", "not found", "new faults",
                  "emissions before detection and during repair", "natural stops"]


def out() -> dict:
    return json.loads((DERIVED / "projection.json").read_text(encoding="utf-8"))


def src() -> dict:
    return json.loads((DERIVED / "projection_sources.json").read_text(encoding="utf-8"))


def o(s: str) -> int:
    return date.fromisoformat(s[:10]).toordinal()


# ----------------------------------------------------------------------------- an independent implementation of T4d
def runs(passes: list[dict], start: str) -> list[dict]:
    """Runs of consecutive detected passes after the start, each with the passes around it and its detection count."""
    after = sorted((p for p in passes if p["date"] > start), key=lambda p: p["date"])
    last = after[-1]["date"]
    out_, i = [], 0
    while i < len(after):
        if not after[i]["detected"]:
            i += 1
            continue
        j = i
        while j + 1 < len(after) and after[j + 1]["detected"]:
            j += 1
        out_.append({"f": after[i]["date"], "l": after[j]["date"], "n": j - i + 1,
                     "prev": after[i - 1]["date"] if i > 0 else start,
                     "next": after[j + 1]["date"] if j + 1 < len(after) else last,
                     "censored": after[j]["date"] == last})
        i = j + 1
    return out_


def end_of(e: dict, conv: str) -> float:
    if e["censored"] or conv == "shortest":
        return o(e["l"])
    return o(e["next"]) if conv == "longest" else (o(e["l"]) + o(e["next"])) / 2


def credit(x: float, hazard: float) -> float:
    if x <= 0:
        return 0.0
    return x if hazard == 0 else (1 - math.exp(-hazard / DAYS_PER_YEAR * x)) / (hazard / DAYS_PER_YEAR)


def lags_for(fac: dict, plumes: dict[str, list[dict]]) -> dict[str, float]:
    from common import RADIUS_KM, haversine_km

    lag: dict[str, float] = {}
    for n in fac["cm_sources"]:
        for r in plumes.get(n, []):
            if not r["scene_timestamp"] or not r["published_at"]:
                continue
            if haversine_km(fac["lat"], fac["lon"], float(r["lat"]), float(r["lon"])) > RADIUS_KM:
                continue
            a = date.fromisoformat(r["scene_timestamp"][:10])
            from datetime import datetime
            d = (datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
                 - datetime.fromisoformat(r["scene_timestamp"].replace("Z", "+00:00"))).total_seconds() / 86400
            lag[a.isoformat()] = min(max(0.0, d), lag.get(a.isoformat(), max(0.0, d)))
    return lag


def plumes_by_source() -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    seen = set()
    with open(DERIVED / "cm_plumes.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["plume_id"] not in seen:
                seen.add(r["plume_id"])
                by.setdefault(r["source_name"], []).append(r)
    return by


def carb() -> dict:
    """The committed CARB aggregates (the per-incidence rows are rebuilt locally by make fetch-published;
    tests/test_fetch_published.py checks that they reproduce these aggregates)."""
    return json.loads((DERIVED / "carb_summary.json").read_text(encoding="utf-8"))["aggregates"]


def r_classes() -> dict[str, float]:
    by_n = {int(n): c for n, c in carb()["byPlumesOutcome"].items()}
    out_ = {}
    for k, label in ((1, "1"), (2, "2"), (3, "3+")):
        sel = [c for n, c in by_n.items() if min(n, 3) == k]
        out_[label] = sum(c["typeA"] for c in sel) / sum(sum(c.values()) for c in sel)
    return out_


def delays() -> list[int]:
    hist = carb()["delays"]["typeA"]["notifiedToMitigatedDays"]  # {days: incidences}
    return [max(0, int(d)) for d, n in hist.items() for _ in range(n)]


# ------------------------------------------------------------------------------------------------ synthetic checks
def test_a_censored_final_event_ends_at_the_last_pass_under_every_convention():
    fac = {"name": "X", "start": "2024-01-01", "passes": [
        {"date": "2024-01-10", "detected": True}, {"date": "2024-02-10", "detected": False},
        {"date": "2024-03-10", "detected": True}, {"date": "2024-04-10", "detected": True}]}
    evs = ptime.censored_events(fac)
    assert [(e["f"], e["l"], e["censored"]) for e in evs] == [("2024-01-10", "2024-01-10", False),
                                                              ("2024-03-10", "2024-04-10", True)]
    mine = runs(fac["passes"], fac["start"])
    for conv in ("shortest", "central", "longest"):
        assert [ptime.event_end(e, conv) for e in evs] == [end_of(e, conv) for e in mine], conv
        assert ptime.event_end(evs[1], conv) == o("2024-04-10"), "censored: the last pass, never the catalogue end"


def test_a_single_pass_facility_is_credited_nothing():
    fac = {"name": "Y", "start": "2024-01-01", "passes": [{"date": "2024-01-10", "detected": True}]}
    evs = ptime.censored_events(fac)
    assert len(evs) == 1 and evs[0]["censored"]
    cr = ptime.facility_credit(fac, evs, {"2024-01-10": 0.0}, [0, 5], {"1": 0.5, "2": 0.5, "3+": 0.5}, 0.0,
                               "central", DAYS_PER_YEAR)
    assert cr["credit"] == 0.0, "the fix comes at or after the last pass: no time to credit"


def test_removed_time_orders_shortest_central_longest():
    """In absolute days, shortest <= central <= longest (the T4c band ran the other way; review finding 2)."""
    fac = {"name": "Z", "start": "2024-01-01", "passes": [
        {"date": d, "detected": x} for d, x in (("2024-01-10", True), ("2024-03-10", True), ("2024-05-10", False),
                                               ("2024-07-10", True), ("2024-09-10", False), ("2024-12-10", False))]}
    evs = ptime.censored_events(fac)
    lags = {e["f"]: 1.0 for e in evs}
    got = [ptime.facility_credit(fac, evs, lags, [2, 8], {"1": 1, "2": 1, "3+": 1}, 0.0, c, DAYS_PER_YEAR)["credit"]
           for c in ("shortest", "central", "longest")]
    mine = [sum(sum(credit(end_of(e, c) - (o(e["f"]) + 1 + d), 0) for d in (2, 8)) / 2
                for e in runs(fac["passes"], fac["start"])) for c in ("shortest", "central", "longest")]
    assert got == pytest.approx(mine) and got[0] <= got[1] <= got[2]
    for scope in ("portfolio_v3", "backtest_set"):
        s = {r["id"]: r.get("ktCh4PerYrCentralAction") for r in out()["t4d"]["sensitivity"]}
        assert s["d_shortest"][scope] <= s[out()["t4d"]["model"]["choice"]][scope] <= s["d_longest"][scope], scope


def test_survival_weighting_credits_less_than_no_recurrence():
    assert credit(100, 0) == 100 and 0 < credit(100, 2.0) < 100
    assert ptime.survival_days(100, 2.0, DAYS_PER_YEAR) == pytest.approx(credit(100, 2.0))


# --------------------------------------------------------------------- the committed result, recomputed independently
def test_merging_single_misses_joins_events_one_pass_apart():
    fac = {"name": "M", "start": "2024-01-01", "passes": [
        {"date": d, "detected": x} for d, x in (("2024-01-10", True), ("2024-02-10", False), ("2024-03-10", True),
                                               ("2024-04-10", False), ("2024-05-10", False), ("2024-06-10", True),
                                               ("2024-07-10", False))]}
    evs = ptime.censored_events(fac)
    assert len(evs) == 3
    merged = ptime.merge_single_misses(evs)
    assert [(e["f"], e["l"], e["n"]) for e in merged] == [("2024-01-10", "2024-03-10", 2), ("2024-06-10", "2024-06-10", 1)]


def _portfolio(by: dict, bt: dict) -> list[str]:
    members = [x["source_name"] for x in json.loads((DERIVED / "sourceset_v3.json").read_text())["sources"]]
    return [n for n in members if bt[n].get("evaluated") and bt[n].get("stake", {}).get("status") == "quantified"]


def _independent_d(hazard_of: str = "measured") -> dict:
    """Option d for the portfolio at the central action share, with this file's own loop: kt, credit share of events
    containing a selecting plume, and the ratio of emitting days to summed central event lengths."""
    import backtest

    facs, end, _ = backtest.facilities()
    by = {f["name"]: f for f in facs}
    bt = {f["name"]: f for f in json.loads((DERIVED / "backtest.json").read_text(encoding="utf-8"))["facilities"]}
    q = _portfolio(by, bt)
    plumes, rc, dl = plumes_by_source(), r_classes(), delays()
    sel = {c["mars_source"]: {pl["scene_timestamp"][:10] for pl in c["plumes"]}
           for c in json.loads((DERIVED / "join_v1.json").read_text(encoding="utf-8"))["candidates"]}
    evs = {n: runs(by[n]["passes"], by[n]["start"]) for n in q}
    restarts, off = 0, 0.0
    for n in q:
        last = max(p["date"] for p in by[n]["passes"] if p["date"] > by[n]["start"])
        for i, e in enumerate(evs[n]):
            if e["censored"]:
                continue
            if i + 1 < len(evs[n]):
                nxt = evs[n][i + 1]
                restarts += 1
                off += max(0.0, (o(nxt["prev"]) + o(nxt["f"])) / 2 - end_of(e, "central"))
            else:
                off += max(0.0, o(last) - end_of(e, "central"))
    lam = restarts / (off / DAYS_PER_YEAR)
    hz = lam if hazard_of == "measured" else 0.0
    kt = total = marked = emit_sum = len_sum = 0.0
    for n in q:
        lag = lags_for(by[n], plumes)
        last = max(p["date"] for p in by[n]["passes"] if p["date"] > by[n]["start"])
        emit = bt[n]["stake"]["duty_cycle"] * (o(last) - o(bt[n]["start"]))
        c_f = 0.0
        for e in evs[n]:
            r = rc[str(e["n"]) if e["n"] < 3 else "3+"]
            c = r * sum(credit(end_of(e, "central") - (o(e["f"]) + lag[e["f"]] + d), hz) for d in dl) / len(dl)
            c_f += c
            marked += c if any(e["f"] <= x <= e["l"] for x in sel.get(n, ())) else 0
            len_sum += end_of(e, "central") - (o(e["prev"]) + o(e["f"])) / 2
        total += c_f
        emit_sum += emit
        kt += bt[n]["stake"]["kt_ch4_per_yr"]["low"] * min(1.0, c_f / emit if emit else 0.0)
    return {"kt": kt, "lambda": lam, "restarts": restarts, "selecting": marked / total, "ratio": emit_sum / len_sum}


def test_option_d_is_the_rule_on_the_committed_rows():
    """Low = option d with the measured re-onset hazard, high = no recurrence, the hazard, the selection share and the
    denominator ratio, each recomputed with this file's own loop."""
    t = out()["t4d"]
    sc = t["scopes"]["portfolio_v3"]
    row = next(r for r in sc["scenarios"] if r["responseShare"] == src()["parameters"]["responseScenarios"]["central"])
    m, h = _independent_d("measured"), _independent_d("none")
    assert sc["reonsetHazard"]["restarts"] == m["restarts"]
    assert sc["reonsetHazard"]["perYear"] == pytest.approx(m["lambda"], abs=1e-4)
    assert row["kt_ch4_per_yr"]["low"] == pytest.approx(m["kt"], abs=0.01), "low = with the measured re-onset hazard"
    assert row["kt_ch4_per_yr"]["high"] == pytest.approx(h["kt"], abs=0.01), "high = no recurrence"
    assert sc["selectingShareOfCredit"] == pytest.approx(m["selecting"], abs=1e-4)
    assert sc["denominatorOverEventLength"] == pytest.approx(m["ratio"], abs=0.01)
    assert sc["denominatorOverEventLength"] > 1, "the denominator is longer than the summed events: T errs low"


def test_band_ends_and_choice_are_as_pre_registered():
    t, s = out()["t4d"], src()
    assert s["model"]["t4d"]["choice"] == t["model"]["choice"] == "d"
    sens = {r["id"]: r.get("ktCh4PerYrCentralAction") for r in t["sensitivity"]}
    assert [r["id"] for r in t["sensitivity"] if r["isChoice"]] == ["d"]
    assert s["model"]["t4d"]["options"]["d_norec"]["model"] == "as d with lambda = 0"
    central = s["parameters"]["responseScenarios"]["central"]
    for scope, sc in t["scopes"].items():
        row = next(r for r in sc["scenarios"] if r["responseShare"] == central)
        assert (row["kt_ch4_per_yr"]["low"], row["kt_ch4_per_yr"]["high"]) == (sens["d"][scope], sens["d_norec"][scope])
    assert (t["headline"]["central"], t["headline"]["upToNoRecurrence"]) == (sens["d"]["portfolio_v3"],
                                                                             sens["d_norec"]["portfolio_v3"])


def test_post_result_rows_are_labelled_and_never_the_choice():
    t, s = out()["t4d"], src()
    t4d = s["model"]["t4d"]
    pr = {r["id"]: r for r in t["postResultSensitivity"]}
    assert set(pr) == {"e", "e_norec", "d_lambda_singlemiss", "d_eventdenom"}
    assert all(r["postResult"] and not r["isChoice"] for r in pr.values())
    assert all(t4d["options"][k]["model"].startswith("POST RESULT") for k in pr)
    assert "e was registered" in t4d["status"] and "after d's result was visible" in t4d["status"]
    assert "only option" not in t4d["choiceNote"] and "e was registered after d's result was known" in \
        t4d["choiceNote"].replace("Option e", "e")
    bad = copy.deepcopy(projection.load_inputs())
    bad["params"]["model"]["t4d"]["choice"] = "e"
    with pytest.raises(ValueError, match="registered after a result was known"):
        projection.build(bad)
    sc = t["scopes"]["portfolio_v3"]
    assert sc["reonsetHazardSingleMissMerged"]["restarts"] <= sc["reonsetHazard"]["restarts"]


def test_r_by_plume_class_is_the_carb_rows():
    t = out()["t4d"]
    for k, v in r_classes().items():
        assert t["rByPlumeClass"][k]["share"] == round(v, 4), k
    assert sum(v["n"] for v in t["rByPlumeClass"].values()) == carb()["incidences"]


def test_every_named_emission_class_is_removed_exactly_once_and_none_is_spanned():
    s = src()
    t4d, m = s["model"]["t4d"], s["model"]
    ec = t4d["emissionClasses"]
    assert set(REVIEW_CLASSES) <= set(ec["list"]) <= set(ec["removedBy"]) and not ec["spannedByInterimRange"]
    r_removes = set(m["classes"]["pairings"][m["range"]["low"]]["r"]["removes"])
    for c in ec["list"]:
        assert ec["removedBy"][c] in ("r", "T") and (ec["removedBy"][c] == "r") == (c in r_removes), c
    assert set(ec["atTheNoRecurrenceEnd"]) <= {c for c, f in ec["removedBy"].items() if f == "T"}
    assert out()["t4d"]["model"]["emissionClasses"] == ec["removedBy"]
    for edit, match in ((lambda x: x["emissionClasses"]["spannedByInterimRange"].append("natural stops"), "spanned"),
                        (lambda x: x["emissionClasses"]["removedBy"].update({"natural stops": "r"}), "exactly one"),
                        (lambda x: x["options"][x["choice"]].update(meets=[True, True, False]), "clauses")):
        bad = copy.deepcopy(projection.load_inputs())
        edit(bad["params"]["model"]["t4d"])
        with pytest.raises(ValueError, match=match):
            projection.build(bad)


def test_the_choice_note_and_menu_are_consistent():
    t4d = src()["model"]["t4d"]
    meeting = [k for k, v in t4d["options"].items() if all(v["meets"]) and not v.get("postResult")]
    assert t4d["choice"] in meeting
    others = [k for k in meeting if k != t4d["choice"]]
    assert all(t4d["options"][k].get("variantOf") == t4d["choice"] for k in others), "every other is a variant of d"
    assert all(k in t4d["choiceNote"] for k in others), "the note names every option that also meets the clauses"
    assert {"d_rminusb", "d_carb", "d_lag0", "d_lagcw", "t_nmn"} <= set(t4d["options"])
    assert "committed before the implementation commit" in t4d["status"]


def natural_stop_hazard_independent() -> float:
    """h_nat on the not MARS-notified sample with this file's own loop."""
    nn = json.loads((DERIVED / "cm_timelines_not_mars_notified.json").read_text(encoding="utf-8"))
    ends, emitting = 0, 0.0
    for tl in nn["sources"].values():
        det = set(tl["det"])
        dates = sorted({x[0] for x in tl["obs"]} | det)
        if not dates:
            continue
        start = date.fromordinal(o(dates[0]) - 1).isoformat()
        passes = [{"date": d, "detected": d in det} for d in dates]
        for e in runs(passes, start):
            s = o(e["f"]) if e["prev"] == start else (o(e["prev"]) + o(e["f"])) / 2
            ends += not e["censored"]
            emitting += max(0.0, end_of(e, "central") - s)
    return ends / (emitting / DAYS_PER_YEAR)


def test_the_not_mars_notified_sample_follows_its_draw_and_names_its_population():
    import fetch_cm_not_mars_notified as fnn

    nn = json.loads((DERIVED / "cm_timelines_not_mars_notified.json").read_text(encoding="utf-8"))
    rule = src()["model"]["t4d"]["notMarsNotifiedSample"]
    assert (nn["seed"], nn["size"], nn["selection"]) == (rule["seed"], rule["size"], rule["eligible"])
    assert sorted(nn["sources"]) == fnn.sample(), "the committed sample is the seeded draw from the eligible pool"
    assert nn["eligibleCount"] == len(fnn.eligible()) and nn["licence"].startswith("Carbon Mapper")
    s = out()["t4d"]["notMarsNotifiedSample"]
    assert s["perYear"] == pytest.approx(natural_stop_hazard_independent(), abs=1e-4)
    assert "US airborne-heavy" in s["population"] and s["usBoxShareOfSources"] > 0.5
    assert 0 < s["airborneShareOfObservations"] < 1
    bad = copy.deepcopy(projection.load_inputs())
    bad["cmNotMarsNotified"]["seed"] += 1
    with pytest.raises(ValueError, match="pre-registered rule"):
        projection.build(bad)


def test_the_t_nmn_row_is_computed_from_the_sample():
    row = next(r for r in out()["t4d"]["sensitivity"] if r["id"] == "t_nmn")
    assert row["computable"] and all(v > 0 for v in row["ktCh4PerYrCentralAction"].values())
    assert row["meets"] == [True, True, False], "t_nmn is measured on other sources than the stake's"


def test_the_post_hoc_row_is_labelled_and_sets_nothing():
    t = out()["t4d"]
    ph = t["postHocSensitivity"]
    assert ph and all(r["note"].startswith("POST HOC, not pre-registered") for r in ph)
    assert {r["id"] for r in ph}.isdisjoint({r["id"] for r in t["sensitivity"]})


def test_a_changed_carb_delay_refuses_to_run():
    bad = copy.deepcopy(projection.load_inputs())
    hist = bad["carbSummary"]["aggregates"]["delays"]["typeA"]["notifiedToMitigatedDays"]
    hist["1000"] = hist.pop(next(iter(hist)))  # one bin's incidences moved to a 1,000-day delay
    with pytest.raises(ValueError, match="aggregates differ"):
        projection.build(bad)


_BUILT: dict = {}


def built(kind: str) -> dict:
    """projection.build with model.timeFactor.headlineKind set to kind (in memory; the committed file is one of the
    two)."""
    if kind not in _BUILT:
        inp = copy.deepcopy(projection.load_inputs())
        inp["params"]["model"]["timeFactor"]["headlineKind"] = kind
        _BUILT[kind] = projection.build(inp)
    return _BUILT[kind]


def test_the_central_kind_publishes_option_d_with_the_reviewed_clauses():
    o_ = built("central")
    card, h, t = o_["card"], o_["headline"], o_["t4d"]
    sc = o_["scopes"]["portfolio_v3"]
    central = o_["parameters"]["responseScenarios"]["central"]
    row = next(r for r in sc["scenarios"] if r["responseShare"] == central)
    assert (h["kind"], h["option"], h["central"], h["upToNoRecurrence"]) == ("central", "d", row["kt_ch4_per_yr"]["low"],
                                                                             row["kt_ch4_per_yr"]["high"])
    assert sc["scenarios"] == t["scopes"]["portfolio_v3"]["scenarios"], "the published scopes are option d's"
    head = card["headlineSentence"]
    for w in ("credited over each member's window from MARS's first plume there as if the covenant began then",
              f"would stop ~{row['kt_ch4_per_yr']['low']:,.0f} kt CH₄/yr at the central {central * 100:.0f} % action "
              f"share (up to ~{row['kt_ch4_per_yr']['high']:,.0f} kt with no recurrence)",
              f"~{row['mt_co2e_per_yr_gwp100']['low']:.2f} Mt CO₂e/yr at GWP-100",
              "each fix credited until the fault's observed end, the last pass or a new fault"):
        assert w in head, w
    assert head.startswith("[PROJECTION]") and head.endswith("— " + projection.HEADLINE_END)
    m = card["methodsSentence"]
    assert m.startswith("How we estimated this.") and m.count(projection.PROJ) == 1
    assert card["evidenceSentence"] in m, "the negative-finding sentence sits in the methods (finding 4)"
    tp = t["scopes"]["portfolio_v3"]
    for w in ("the bias runs both ways: sparse passes miss restarts, and a single missed detection of a persistent "
              "source creates a false one",
              f"{tp['denominatorOverEventLength']:.2f} times the summed event lengths, so the time share errs low",
              f"{tp['selectingShareOfCredit'] * 100:.0f} % of the credit is for events containing the plume that "
              "selected the member", "a forward hold-out (members chosen before a date, credit only after it) is "
              "future work", "The action share is an ASSUMPTION", "The time share is an ASSUMPTION"):
        assert w in m, w
    for k, v in t["rByPlumeClass"].items():
        assert f"{v['share'] * 100:.0f} % at {'3 or more' if k == '3+' else k} plume" in m and f"n = {v['n']}" in m, k
    assert m.count("where a buyer, not a regulator, would ask") == 1
    for x in card.values():  # review findings 3-5: withdrawn or false wording
        for bad in ("a floor because sparse passes miss restarts", "priced and fixed in turn", "to event end",
                    "later faults are new events", "staying stopped", "left out by one factor only"):
            assert bad not in x, bad
    for k in ("headlineSentence", "scaleSentence", "floorLineSentence", "costLineSentence"):
        assert "not a measured reduction" not in card[k] and "ASSUMPTION" not in card[k], k
    for k in ("projectionSentence", "scenariosSentence", "widerSetSentence"):
        assert card[k].startswith("[PROJECTION]") and "stake, not abatement" in card[k] and projection.PROJ in card[k]


def test_the_range_kind_keeps_the_reviewed_interim_text():
    r_, c_ = built("range"), built("central")
    assert r_["headline"]["kind"] == "range" and r_["headline"]["central"] is None
    assert r_["card"] == c_["interim"]["card"] == r_["interim"]["card"]
    assert r_["scopes"] == c_["interim"]["scopes"]
    bad = copy.deepcopy(projection.load_inputs())
    bad["params"]["model"]["timeFactor"]["headlineKind"] = "best"
    with pytest.raises(ValueError, match="headlineKind"):
        projection.build(bad)
