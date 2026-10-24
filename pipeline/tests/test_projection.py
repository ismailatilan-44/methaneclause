"""Carbon projection (pipeline/projection.py -> data/derived/projection.json): regenerates from committed inputs, types
no number, cites every parameter, is monotone in the response scenario, and says what it is (a projection, not a
measured change). Offline: committed files only."""
from __future__ import annotations

import ast
import copy
import csv
import json
import re
from pathlib import Path

import pytest

import projection
import projection_evidence as evidence

ROOT = Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data" / "derived"
OUT = DERIVED / "projection.json"
SRC = DERIVED / "projection_sources.json"


def interim() -> dict:
    """projection.json as the reviewed interim (T4b step 1) path writes it: its own headline, scopes and card, whether
    or not the published kind is T4d's 'central' (the interim sentences are tested here, T4d's in
    test_projection_t4d.py)."""
    o = committed()
    return o | o["interim"]


def committed() -> dict:
    return json.loads(OUT.read_text(encoding="utf-8"))


def sources() -> dict:
    return json.loads(SRC.read_text(encoding="utf-8"))


def test_projection_regenerates_from_inputs():
    assert projection.build(projection.load_inputs()) == committed(), "run make projection"


def test_inputs_are_hashed():
    names = {g["path"] for g in committed()["generatedFrom"]}
    assert {"data/derived/backtest.json", "data/derived/sourceset_v3.json", "data/derived/floors.json",
            "data/derived/measurements.json", "data/derived/mars_sources.csv",
            "data/derived/projection_sources.json", "pipeline/projection.py"} <= names


def test_no_typed_numbers_in_the_script():
    """Only unit and formatting constants may be literals; every parameter value is read from the sources file."""
    lits = set()
    import projection_time as ptime

    allowed = projection.UNIT_AND_FORMAT_CONSTANTS | ptime.UNIT_CONSTANTS
    for f in ("projection.py", "projection_evidence.py", "projection_time.py"):
        tree = ast.parse((ROOT / "pipeline" / f).read_text(encoding="utf-8"))
        lits |= {n.value for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
    assert lits <= allowed, lits - allowed
    p = sources()["parameters"]
    ev = p["evidence"]
    es = [ev["eventStudy"]["primary"], *ev["eventStudy"]["variants"]]
    params = {p["baselineResponseShare"]["value"], *p["responseScenarios"]["values"], p["costScale"]["usdPerTco2e"],
              *(p["ceasedShare"][k] for k in projection.BANDS), ev["confidence"], ev["power"],
              p["responseScenarios"]["rule"]["confidence"],
              ev["bootstrapDraws"], ev["seed"], ev["minSourcesPerGroup"],
              *(s[k] for s in es for k in ("windowDays", "gapDays")),
              *(r[c] for r in p["responseScenarios"]["rule"]["rows"] for c in p["responseScenarios"]["rule"]["columns"])}
    params -= allowed  # a 1,000-day window equals the kt/Mt unit; units stay allowed
    assert not params & lits, f"parameter values typed into the scripts: {params & lits}"


def test_sources_file_is_complete():
    s = sources()
    srcs, params = s["sources"], s["parameters"]
    used = set()
    for name, prm in params.items():
        assert prm.get("note"), f"{name}: no note"
        assert prm["status"] in ("read", "ASSUMPTION", "DESIGN"), name
        for sid in prm.get("informedBy") or []:
            assert sid in srcs, f"{name}: unknown source {sid}"
            used.add(sid)
        if prm["status"] == "ASSUMPTION":
            continue
        assert prm["sources"], f"{name}: a read parameter needs a source"
        for sid in prm["sources"]:
            assert sid in srcs, f"{name}: unknown source {sid}"
            used.add(sid)
    for sid, src in srcs.items():
        assert src["tag"] in ("[V]", "[S]"), sid
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", src["readOn"]), sid
        assert src["url"] and src["title"] and src["quotes"] and all(q.strip() for q in src["quotes"]), sid
        assert sid in used or src.get("context"), f"{sid}: neither a parameter's source nor marked as context"
    assert params["responseScenarios"]["status"] == "ASSUMPTION"
    # projection review (spec-v2 §15): the stop share is an assumption informed by, not read from, its two figures
    assert params["ceasedShare"]["status"] == "ASSUMPTION" and params["ceasedShare"]["informedBy"]


def test_every_parameter_names_its_evidence():
    """A parameter that is not an ASSUMPTION has a non-empty source list; an ASSUMPTION that sets a projected value
    names the published figures that inform it (no scenario stands on an empty list, board T4)."""
    s = sources()
    for name, prm in s["parameters"].items():
        if prm["status"] != "ASSUMPTION":
            assert prm["sources"], f"{name}: status {prm['status']} with an empty source list"
        else:
            assert prm.get("informedBy"), f"{name}: an ASSUMPTION with no published figure informing it"
            for sid in prm["informedBy"]:
                assert s["sources"][sid]["tag"] == "[V]", f"{name}: {sid} is not read at the source"


def _carb_rows(prm: dict, s: dict) -> list[dict]:
    rule = prm["rule"]
    src = s["sources"][rule["source"]]
    assert rule["source"] in prm["informedBy"]
    for row in rule["rows"]:
        # the counts are the published table row (extracted text, whitespace collapsed), column for column
        text = " ".join(str(row[c]) for c in rule["columns"])
        assert f"{rule['sector']} {text}" in src["tableRows"], (row, src["tableRows"])
        # the outcome columns account for every incidence sent
        assert sum(row[c] for c in rule["columns"] if c != rule["of"]) == row[rule["of"]], row
    assert len({r["year"] for r in rule["rows"]}) == len(rule["rows"]) == len(src["tableRows"])
    return rule["rows"]


def test_response_scenarios_follow_the_written_rule():
    """Central = the published share of notified oil and gas incidences that were fixable faults (Type A, every one
    stopped or repaired; every campaign year in the table); low/high = its Wilson interval at the rule's own confidence;
    two decimals. The values in the sources file are a checked cache of that rule, not hand-tuned."""
    s = sources()
    p = s["parameters"]
    prm = p["responseScenarios"]
    rows = _carb_rows(prm, s)
    k = sum(r[prm["rule"]["acted"]] for r in rows)
    n = sum(r[prm["rule"]["of"]] for r in rows)
    lo, hi = evidence.wilson(k, n, prm["rule"]["confidence"])
    assert prm["values"] == [round(lo, 2), round(k / n, 2), round(hi, 2)]
    assert prm["central"] == round(k / n, 2)
    assert projection.response_scenarios(p) == (prm["values"], prm["central"])
    # the published definitions agree with what the rule counts
    q = " ".join(s["sources"][prm["rule"]["source"]]["quotes"])
    assert "stop or repair" in q and "Type A" in q
    rr = committed()["responseRule"]
    assert (rr["acted"], rr["of"]) == (k, n) and rr["source"] == prm["rule"]["source"]
    assert rr["answered"] == n - sum(r["didNotRespond"] for r in rows)
    assert rr["confidence"] == prm["rule"]["confidence"]


def test_the_sensitivity_table_is_generated_and_the_rule_is_its_lowest_definition():
    """projection.json -> responseSensitivity recomputes every definition in rule.sensitivity from the same table rows;
    the rule was chosen as the lowest of them, and the projection refuses to run once it no longer is."""
    out, s = committed(), sources()
    p = s["parameters"]
    rows, base, c = p["responseScenarios"]["rule"]["rows"], p["baselineResponseShare"]["value"], p["ceasedShare"]
    defs = p["responseScenarios"]["rule"]["sensitivity"]
    sens = out["responseSensitivity"]
    assert [d["id"] for d in sens] == [d["id"] for d in defs] and len(defs) >= 6
    for d, got in zip(defs, sens):
        sel = [r for r in rows if d["years"] is None or r["year"] in d["years"]]
        num = sum(r[c] for r in sel for c in d["numerator"])
        den = sum(r[c] for r in sel for c in d["denominator"]) - sum(r[c] for r in sel for c in d["subtract"])
        assert (got["numerator"], got["denominator"], got["share"]) == (num, den, round(num / den, 4)), d["id"]
        for scope, kt in got["ktCh4PerYrByStopEnd"].items():
            stake = out["scopes"][scope]["stakeLowKtCh4PerYr"]
            assert kt == {b: round(stake * (num / den - base) * c[b], 2) for b in ("low", "high")}, (d["id"], scope)
    rule = [d for d in sens if d["isRule"]]
    assert len(rule) == 1 and rule[0]["share"] == min(d["share"] for d in sens) == out["responseRule"]["share"]
    inputs = projection.load_inputs()
    lower = copy.deepcopy(inputs)
    lower["params"]["parameters"]["responseScenarios"]["rule"]["sensitivity"].append(
        {"id": "lower", "label": "Type A / (sent + Type B)", "numerator": ["typeA"], "denominator": ["sent", "typeB"],
         "subtract": [], "years": None})
    with pytest.raises(ValueError, match="lowest"):
        projection.build(lower)


def test_the_baseline_is_the_most_recent_published_mars_share():
    s = sources()
    b = s["parameters"]["baselineResponseShare"]
    latest = max(b["rule"]["candidates"], key=lambda c: s["sources"][c["source"]]["date"])
    quote = s["sources"][latest["source"]]["quotes"][latest["quote"]]
    assert f"{round(b['value'] * 100)} per cent" in quote and latest["source"] in b["sources"]
    assert projection.baseline_share(s) == (b["value"], latest["cite"])
    older = copy.deepcopy(s)
    older["parameters"]["baselineResponseShare"]["value"] = 0.12  # the October 2025 figure, no longer the latest
    with pytest.raises(ValueError, match="most recent"):
        projection.baseline_share(older)
    assert "conservative" in b["note"], "the note says why subtracting a reply rate from an outcome rate is conservative"


def test_a_scenario_that_drifts_from_its_rule_fails_closed():
    inputs = projection.load_inputs()
    drift = copy.deepcopy(inputs)
    drift["params"]["parameters"]["responseScenarios"]["values"] = [0.3, 0.5, 0.7]
    drift["params"]["parameters"]["responseScenarios"]["central"] = 0.5
    with pytest.raises(ValueError, match="rule"):
        projection.build(drift)
    rows = copy.deepcopy(inputs)
    rows["params"]["parameters"]["responseScenarios"]["rule"]["rows"][0]["typeA"] += 1
    with pytest.raises(ValueError, match="rule"):
        projection.build(rows)


# --------------------------------------------------------------------- T4b: one meaning per factor (model, pre-registered)
def _criterion(spec: dict, cls: dict) -> tuple[bool, bool]:
    """The pre-registered criterion recomputed here, independently of projection.py: (1) what a repair cannot touch is
    removed by r, measured on plume-detected notified sources; (2) d is measured on repaired faults, removes nothing r
    removes and credits nothing a repair did not cause."""
    r, d = spec["r"], spec["d"]
    one = set(cls["notRepairable"]) <= set(r["removes"]) and r["population"] == cls["required"]["r"]
    two = (d["population"] == cls["required"]["d"] and not set(r["removes"]) & set(d["removes"])
           and not d.get("credits"))
    return one, two


def test_both_ends_of_the_range_meet_clauses_1_and_2_and_none_claims_the_time_clause():
    s, out = sources(), interim()
    m = s["model"]
    cls = m["classes"]
    ends = [m["range"][b] for b in ("low", "high")]
    for oid in ends:
        end = cls["pairings"][oid]
        assert not set(end["r"]["removes"]) & set(end["d"]["removes"]), f"{oid}: an emission removed by two factors"
        assert _criterion(end, cls) == (True, True) and projection.pairing_violations(end, cls) == [], oid
    for oid, opt in m["options"].items():  # the prose flags agree with the encoded classes
        assert (opt["meets1"], opt["meets2"]) == _criterion(cls["pairings"][oid], cls), oid
    assert sorted(o for o, v in m["options"].items() if v["meets1"] and v["meets2"]) == sorted(ends)
    assert not any(v["meets3"] for v in m["options"].values()), "no option has a time base of a year yet (T4c)"
    assert "time base is a year at the stake's sources" in m["criterion"]
    # the T4 pairing (Type A share x 44 % of total emissions) removes allowed operation and maintenance twice
    assert any("twice" in v for v in projection.pairing_violations(cls["pairings"]["t4"], cls))
    assert out["model"]["choice"] == "range" and out["model"]["violations"]["t4"]
    assert all(out["model"]["violations"][oid] == [] for oid in ends)
    # no single central tonnage while the headline is a range
    assert out["headline"]["kind"] == "range" and out["headline"]["central"] is None
    assert set(out["parameters"]["ceasedShare"]) >= {"low", "high"} and "mid" not in out["parameters"]["ceasedShare"]


# the emission classes the review named (2026-10-21); each is removed by exactly one factor or spanned by the range
REVIEW_CLASSES = ["allowed operation (Type B)", "maintenance (Type C)", "not found", "new faults",
                  "emissions before detection and during repair", "natural stops"]


def test_every_named_emission_class_is_removed_once_or_spanned_by_the_range():
    s, out = sources(), committed()
    m = s["model"]
    cls = m["classes"]
    assert set(REVIEW_CLASSES) <= set(cls["list"])
    factors = {"b": cls["b"]["removes"]}
    for c in cls["list"]:
        for b in ("low", "high"):
            pair = cls["pairings"][m["range"][b]]
            by = [f for f, rm in (("r", pair["r"]["removes"]), ("d", pair["d"]["removes"]), ("b", factors["b"]))
                  if c in rm]
            if c in cls["spannedByInterimRange"]:
                assert c not in cls["removedBy"] and len(by) <= 1 and not {"r", "b"} & set(by), (c, b, by)
            else:
                assert by == [cls["removedBy"][c]], (c, b, by)
    assert out["model"]["emissionClasses"] == {
        c: cls["removedBy"].get(c, "spanned by the interim range") for c in cls["list"]}
    # the range really spans "new faults": one end nets them, the other does not
    low, high = (cls["pairings"][m["range"][b]]["d"]["removes"] for b in ("low", "high"))
    assert "new faults" in low and "new faults" not in high


@pytest.mark.parametrize("edit, match", [
    (lambda c: c["spannedByInterimRange"].remove("natural stops"), "neither removed"),
    (lambda c: c["pairings"]["c"]["d"]["removes"].append("not found"), "twice"),
    (lambda c: c["b"]["removes"].append("new faults"), "spanned"),
])
def test_an_emission_class_that_is_unaccounted_or_removed_twice_refuses_to_run(edit, match):
    bad = copy.deepcopy(projection.load_inputs())
    edit(bad["params"]["model"]["classes"])
    with pytest.raises(ValueError, match=match):
        projection.build(bad)


@pytest.mark.parametrize("edit, match", [
    (lambda c: c["pairings"]["b"]["d"]["removes"].append("maintenance (Type C)"), "twice"),
    (lambda c: c["pairings"]["b"]["d"].update(population="total facility emissions, ground survey"), "population"),
    (lambda c: c["pairings"]["b"]["d"].update(credits=["vent change without repair"]), "credits"),
])
def test_a_pairing_that_breaks_the_criterion_refuses_to_run(edit, match):
    bad = copy.deepcopy(projection.load_inputs())
    edit(bad["params"]["model"]["classes"])
    with pytest.raises(ValueError, match=match):
        projection.build(bad)


def test_options_prose_that_disagrees_with_the_encoding_refuses_to_run():
    bad = copy.deepcopy(projection.load_inputs())
    bad["params"]["model"]["options"]["a"]["meets2"] = True
    with pytest.raises(ValueError, match="options"):
        projection.build(bad)


def test_the_stop_share_follows_its_quotes():
    """model.rules.d: each end is the per-cent figure in its quote (low: one repair round net of new leaks; high: the
    repair rate, valid only with the quote that fixed leaks did not re-appear); a drifted value refuses to run."""
    s = sources()
    c, rule = s["parameters"]["ceasedShare"], s["parameters"]["ceasedShare"]["rule"]
    quotes = s["sources"][rule["source"]]["quotes"]
    assert set(rule["bands"]) == {"low", "high"}, "two ends, no central value (interim until T4c)"
    for band, b in rule["bands"].items():
        assert c[band] == int(re.search(b["regex"], quotes[b["quote"]]).group(1)) / 100, band
    assert "because of new leaks" in quotes[rule["bands"]["low"]["quote"]]
    w = rule["bands"]["high"]["with"]
    assert w["phrase"] in quotes[w["quote"]] and "did not re-appear" in w["phrase"]
    assert projection.stop_shares(s) == {b: c[b] for b in ("low", "high")}
    drift = copy.deepcopy(projection.load_inputs())
    drift["params"]["parameters"]["ceasedShare"]["high"] = 0.95
    with pytest.raises(ValueError, match="quote"):
        projection.build(drift)
    unsupported = copy.deepcopy(projection.load_inputs())
    unsupported["params"]["sources"]["ravikumar-2020-erl"]["quotes"][w["quote"]] = "leaks were re-surveyed"
    with pytest.raises(ValueError, match="re-appear"):
        projection.build(unsupported)


def _carb_summary() -> dict:
    """The committed CARB aggregates; the per-incidence rows are not tracked (make fetch-published rebuilds them and
    tests/test_fetch_published.py checks that they reproduce this file)."""
    return json.loads((DERIVED / "carb_summary.json").read_text(encoding="utf-8"))


def test_the_carb_summary_reproduces_table3_and_a_changed_count_refuses_to_run():
    s, cs = sources(), _carb_summary()
    agg = cs["aggregates"]
    rule = s["parameters"]["responseScenarios"]["rule"]
    assert cs["sha256"] == s["sources"][rule["incidences"]["source"]]["sha256"]
    assert rule["incidences"]["file"] == "data/derived/carb_summary.json"
    for row in (r for r in rule["rows"] if r["year"] in agg["years"]):
        got = agg["byYearOutcome"][str(row["year"])]
        assert sum(got.values()) == row[rule["of"]]
        for col in (c for c in rule["columns"] if c != rule["of"]):
            assert got[col] == row[col], (row["year"], col)
    inputs = projection.load_inputs()
    changed = copy.deepcopy(inputs)
    year = changed["carbSummary"]["aggregates"]["byYearOutcome"][str(agg["years"][0])]
    year["typeA"], year["typeB"] = year["typeA"] + 1, year["typeB"] - 1
    with pytest.raises(ValueError, match="aggregates differ"):
        projection.build(changed)  # the aggregates hash catches an edited count first
    import fetch_published

    changed["carbSummary"]["aggregatesJcsSha256"] = fetch_published.aggregates_hash(changed["carbSummary"]["aggregates"])
    with pytest.raises(ValueError, match="Table 3"):
        projection.build(changed)  # and a re-hashed edit still has to reproduce the published Table 3
    lost = copy.deepcopy(inputs)
    lost["carbSummary"]["aggregates"]["incidences"] += 1
    lost["carbSummary"]["aggregatesJcsSha256"] = fetch_published.aggregates_hash(lost["carbSummary"]["aggregates"])
    with pytest.raises(ValueError, match="do not add up"):
        projection.build(lost)  # the counts by year and by plume count account for every incidence
    other = copy.deepcopy(inputs)
    other["carbSummary"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="appendix"):
        projection.build(other)


def test_the_summary_alone_reproduces_the_projection_inputs(monkeypatch):
    """F3: the per-incidence rows are not in the tracked tree. With every read of the row file refused (Path.read_text,
    read_bytes, open; builtins.open; io.open), projection.build reads only the committed aggregates and gives the
    committed projection.json, every CARB-derived input included (weighting, plume-weighted row, plume-class shares,
    delay quantiles, the d_carb row)."""
    cs = _carb_summary()
    assert "rows" not in cs and set(cs["aggregates"]) == {"incidences", "years", "outcomes", "byYearOutcome",
                                                          "byPlumesOutcome", "uniqueJoinKey", "delays"}
    assert not re.search(r'"(id|earliestPlume|notified|mitigated)"', json.dumps(cs)), "no per-incidence field"
    assert "carb_incidences.json" not in projection.INPUTS and "carb_summary.json" in projection.INPUTS
    import builtins
    import io

    want = committed()

    def refuse(path) -> None:
        if str(path).endswith("carb_incidences.json"):
            raise PermissionError(f"the projection must not read the per-incidence row file ({path})")

    def guard(real):
        def wrapped(*a, **k):  # a[0] is the Path (methods) or the file argument (open)
            refuse(a[0] if a else k.get("file", ""))
            return real(*a, **k)
        return wrapped

    for owner, name in ((Path, "read_text"), (Path, "read_bytes"), (Path, "open"), (builtins, "open"), (io, "open")):
        monkeypatch.setattr(owner, name, guard(getattr(owner, name)))
    with pytest.raises(PermissionError, match="row file"):
        (DERIVED / "carb_incidences.json").read_text(encoding="utf-8")  # the guard is in place for every way in
    with pytest.raises(PermissionError, match="row file"):
        open(DERIVED / "carb_incidences.json", encoding="utf-8")
    got = projection.build(projection.load_inputs())
    assert got["weighting"] == want["weighting"] and got["modelSensitivity"] == want["modelSensitivity"]
    for k in ("rByPlumeClass", "responseDelaysDays", "carbFirstPlumeToMitigationDays"):
        assert got["t4d"][k] == want["t4d"][k], k
    for k in ("d", "d_carb"):
        assert next(r for r in got["t4d"]["sensitivity"] if r["id"] == k) == \
            next(r for r in want["t4d"]["sensitivity"] if r["id"] == k), k
    assert got == want, "run make projection"


def test_the_weighting_record_says_why_the_share_counts_incidences():
    out, agg = committed(), _carb_summary()["aggregates"]
    w = out["weighting"]
    assert (w["incidences"], w["years"], w["uniqueJoinKey"]) == (agg["incidences"], agg["years"], agg["uniqueJoinKey"])
    assert w["uniqueJoinKey"] < w["incidences"] / 2, "the date-and-plume-count key does not identify most incidences"
    assert w["mass"]["computable"] is False and "no emission rate" in w["mass"]["reason"]


def test_the_model_sensitivity_is_generated_from_the_committed_rows():
    out, s, agg = interim(), sources(), _carb_summary()["aggregates"]
    p, m = s["parameters"], s["model"]
    rule, base = p["responseScenarios"]["rule"], p["baselineResponseShare"]["value"]
    rows = rule["rows"]
    sent = sum(r[rule["of"]] for r in rows)
    rav = m["reads"]["ravikumarTotal"]
    total = int(re.search(rav["regex"], s["sources"][rav["source"]]["quotes"][rav["quote"]]).group(1)) / 100
    by_n = {int(n): c for n, c in agg["byPlumesOutcome"].items()}
    plumes = (sum(n * c[rule["acted"]] for n, c in by_n.items())
              / sum(n * sum(c.values()) for n, c in by_n.items()))
    want_r = {"rule": p["responseScenarios"]["central"],
              "answered": round((sent - sum(r["didNotRespond"] for r in rows)) / sent, 2),
              "plumeWeighted": round(plumes, 2)}
    want_d = {"low": p["ceasedShare"]["low"], "high": p["ceasedShare"]["high"], "ravikumarTotal": total}
    sens = out["modelSensitivity"]
    assert [x["id"] for x in sens] == [x["id"] for x in m["sensitivity"]]
    for spec, got in zip(m["sensitivity"], sens):
        if spec["r"] == "massWeighted":
            assert got["computable"] is False and "no emission rate" in got["reason"]
            continue
        assert (got["r"], got["d"]) == (want_r[spec["r"]], want_d[spec["d"]]), spec["id"]
        for scope, kt in got["ktCh4PerYrCentral"].items():
            assert kt == round(out["scopes"][scope]["stakeLowKtCh4PerYr"] * (got["r"] - base) * got["d"], 2)
    central = next(r for r in out["scopes"]["portfolio_v3"]["scenarios"]
                   if r["responseShare"] == p["responseScenarios"]["central"])
    for end in ("low", "high"):  # the range's ends are the rows of its two options, counted by incidence
        row = next(x for x in sens if x["rangeEnd"] == end)
        assert row["pairing"] == m["range"][end]
        assert row["ktCh4PerYrCentral"]["portfolio_v3"] == central["kt_ch4_per_yr"][end]
    assert sum(x["rangeEnd"] is not None for x in sens) == 2


def test_the_projection_reads_the_sources_file():
    out, s = committed(), sources()
    assert out["parameters"] == s["parameters"], "run make projection"
    for scope in out["scopes"].values():
        assert [r["responseShare"] for r in scope["scenarios"]] == s["parameters"]["responseScenarios"]["values"]
    srcs = {g["path"]: g["sha256"] for g in out["generatedFrom"]}
    import hashlib
    assert srcs["data/derived/projection_sources.json"] == hashlib.sha256(SRC.read_bytes()).hexdigest()


def test_read_values_appear_in_their_quotes():
    """The number in the parameter is the number in the quote (percent form), so a value cannot drift from its source."""
    s = sources()
    p = s["parameters"]

    def quotes(prm: dict) -> str:
        ids = [*prm["sources"], *(prm.get("informedBy") or [])]
        return " ".join(q for sid in ids for q in s["sources"][sid]["quotes"])

    assert f"{round(p['baselineResponseShare']['value'] * 100)} per cent" in quotes(p["baselineResponseShare"])
    cq = quotes(p["ceasedShare"])
    assert f"reduced by only {round(p['ceasedShare']['low'] * 100)}% because of new leaks" in cq
    assert f"corresponding to {round(p['ceasedShare']['high'] * 100)}% of fugitive emissions" in cq
    assert "leaks that were fixed did not re-appear" in cq
    assert f"USD {p['costScale']['usdPerTco2e']} per tonne" in quotes(p["costScale"])


@pytest.mark.parametrize("scope", ["portfolio_v3", "backtest_set"])
def test_band_is_monotone_in_the_response_scenario(scope):
    rows = committed()["scopes"][scope]["scenarios"]
    assert [r["responseShare"] for r in rows] == sorted(r["responseShare"] for r in rows)
    for key in ("kt_ch4_per_yr", "mt_co2e_per_yr_gwp100", "mt_co2e_per_yr_gwp20"):
        for band in ("low", "high"):
            vals = [r[key][band] for r in rows]
            assert vals == sorted(vals) and vals[0] < vals[-1], (key, band)
        for r in rows:
            assert set(r[key]) == {"low", "high"} and r[key]["low"] < r[key]["high"], (key, r["responseShare"])


def test_only_the_response_above_baseline_counts_and_below_baseline_fails_closed(monkeypatch):
    inputs = projection.load_inputs()
    # isolate the baseline guard from the rule guard (test_a_scenario_that_drifts_from_its_rule_fails_closed)
    monkeypatch.setattr(projection, "response_scenarios",
                        lambda p: (p["responseScenarios"]["values"], p["responseScenarios"]["central"]))
    base = inputs["params"]["parameters"]["baselineResponseShare"]["value"]
    at = copy.deepcopy(inputs)
    at["params"]["parameters"]["responseScenarios"]["values"] = [base]
    at["params"]["parameters"]["responseScenarios"]["central"] = base
    with pytest.raises(ValueError):
        projection.build(at)  # a scenario equal to today's baseline projects nothing: refused, not a zero line
    below = copy.deepcopy(at)
    below["params"]["parameters"]["responseScenarios"]["values"] = [base / 2]
    below["params"]["parameters"]["responseScenarios"]["central"] = base / 2
    with pytest.raises(ValueError):
        projection.build(below)


def test_projection_is_bounded_by_the_stake_floor():
    out = committed()
    for scope in out["scopes"].values():
        for r in scope["scenarios"]:
            assert r["kt_ch4_per_yr"]["high"] < scope["stakeLowKtCh4PerYr"]
    bt = json.loads((DERIVED / "backtest.json").read_text(encoding="utf-8"))["stake_band"]
    assert out["scopes"]["backtest_set"]["stakeLowKtCh4PerYr"] == bt["kt_ch4_per_yr"]["low"]
    assert out["scopes"]["backtest_set"]["facilitiesQuantified"] == bt["facilitiesQuantified"]


def test_portfolio_stake_is_the_sum_of_its_members():
    bt = {f["name"]: f for f in json.loads((DERIVED / "backtest.json").read_text(encoding="utf-8"))["facilities"]}
    names = [s["source_name"] for s in json.loads((DERIVED / "sourceset_v3.json").read_text())["sources"]]
    q = [bt[n]["stake"]["kt_ch4_per_yr"]["low"] for n in names
         if n in bt and bt[n].get("evaluated") and bt[n].get("stake", {}).get("status") == "quantified"]
    pf = committed()["scopes"]["portfolio_v3"]
    assert pf["facilities"] == len(names) and pf["facilitiesQuantified"] == len(q)
    assert pf["stakeLowKtCh4PerYr"] == round(sum(q), 2)


def test_cost_per_tonne_is_ledger_cost_over_projected_tonnes():
    out = committed()
    meas = json.loads((DERIVED / "measurements.json").read_text(encoding="utf-8"))["functions"]
    calls = sources()["parameters"]["ledgerCalls"]
    per_ep = sum(meas[f]["usd_at_stated_gwei"] for f in calls["perEpisode"])
    gwp100 = json.loads((DERIVED / "floors.json").read_text())["gwp"]["gwp100_fossil_ch4"]["value"]
    for scope in out["scopes"].values():
        led = scope["ledger"]
        assert led["usdPerEpisode"] == round(per_ep, 4)
        per_year = led["episodesPerYear"] * per_ep + sum(meas[f]["usd_at_stated_gwei"] for f in calls["perCovenantYear"])
        assert led["usdPerYear"] == round(per_year, 2)
        for r in scope["scenarios"]:
            for b in ("low", "high"):
                t = r["kt_ch4_per_yr"][b] * 1000 * gwp100
                assert r["ledgerUsdPerTco2eGwp100"][b] == pytest.approx(per_year / t, rel=1e-3)


def test_negative_finding_counts_match_the_mars_csv():
    with open(DERIVED / "mars_sources.csv", newline="", encoding="utf-8") as f:
        og = [r for r in csv.DictReader(f) if r["sector"] == "Oil and Gas"]
    nf = committed()["negativeFinding"]
    for fb in ("Yes", "No"):
        rows = [r for r in og if r["feedback"] == fb]
        g = nf["byFeedback"][fb]
        assert g["sources"] == len(rows)
        assert g["persistentOrFrequent"] == sum(r["persistency_category"] in ("persistent", "frequent") for r in rows)
        assert g["plumesTotal"] == sum(int(r["n_plumes_detected"]) for r in rows)
    assert nf["notified"] == sum(r["notified"] == "Yes" for r in og)
    y, n = nf["byFeedback"]["Yes"], nf["byFeedback"]["No"]
    assert y["meanPersistency"] > n["meanPersistency"] and y["meanPlumes"] > n["meanPlumes"]  # the finding holds
    assert "no notification or response date" in nf["sentence"]


def test_cards_say_what_they_are():
    card = interim()["card"]
    assert set(card) >= {"projectionSentence", "scenariosSentence", "floorSentence", "costSentence",
                         "negativeFindingSentence", "adoptionSentence", "widerSetSentence", "evidenceSentence",
                         "line1Sentence", "line2Sentence", "limitSentence", "headlineSentence", "scaleSentence",
                         "floorLineSentence", "costLineSentence", "methodsSentence"}
    for k in ("projectionSentence", "scenariosSentence", "widerSetSentence"):
        assert card[k].startswith("[PROJECTION]"), k
        assert "projection of what the clause could change if adopted, not a measured reduction" in card[k], k
        assert "ASSUMPTION" in card[k], k
        assert "stake, not abatement" in card[k], f"{k}: every projected tonnage sits next to the stake label"
        assert "acted on" in card[k] or k == "widerSetSentence", k
        # "answered" may only describe CARB's operators replying to the regulator, never the sellers' action
        carb = projection._carb(committed()["responseRule"])
        assert "answered" not in card[k].replace(carb, ""), f"{k}: 'acted on', not 'answered' (a reply is not an action)"
    assert "measured after a satellite notification" in card["projectionSentence"]
    assert "no central value" in card["projectionSentence"], "the interim range says it has no central value"
    for k, s in card.items():  # withdrawn after the T4b review (2026-10-21): over-claims
        assert "staying stopped" not in s and "left out by one factor only" not in s, k
        assert "in the central case" not in s and "central scenario" not in s, f"{k}: no central tonnage (interim)"
    assert "reply rate" in card["projectionSentence"]
    assert card["floorSentence"].startswith("Stake, not abatement")
    cost = card["costSentence"]
    assert "ledger cost only" in cost.lower() and "excludes the refund-side calls and the cost of posting the bond" in cost
    assert "IEA" not in cost, "the IEA price threshold is not compared with the ledger cost"
    assert "(operator or government)" in card["line1Sentence"] and "undetermined" in card["line1Sentence"]
    assert "(operator or government)" in card["negativeFindingSentence"]
    for k, s in card.items():
        assert "capped" not in s and "at most a" not in s, f"{k}: the evidence-capped figure is withdrawn"
    assert "[SIM]" in card["projectionSentence"] and "[SIM]" in card["adoptionSentence"]
    banned = re.compile(r"reduction achieved|\babated\b|\babatement\b|emission reductions?|guarantee", re.I)
    for k, s in card.items():
        for m in banned.finditer(s):
            assert re.search(r"\bnot\b|\bno\b|never", s[max(0, m.start() - 40):m.start()]), (k, s)


def test_the_pdf_draft_carries_the_projection_blocks():
    """Vision first (user decision 2026-10-16), forward hold-out leading (G2, after the B1 review): headline
    ([PROJECTION] once) -> forward stake, interval and rolling origins -> the whole-window comparison -> cost line ->
    one "How we estimated this" paragraph that holds the ASSUMPTIONs, the null and "not a measured reduction", once
    each; the whole-window headline is no longer quoted. The cost and methods sentences are the card's, with the
    whole-window comparison named and the forward hold-out no longer called future work."""
    pdf = ROOT / "docs" / "pdf" / "methaneclause.md"
    if not pdf.exists():
        pytest.skip("docs/pdf/methaneclause.md is not in this checkout")
    t = pdf.read_text(encoding="utf-8")
    for name in ("projection", "projectionCost", "projectionFinding", "projectionEvidence", "projectionAdoption",
                 "projectionSources", "projectionLimit", "forwardLimit"):
        assert f"<!-- gen:{name}:begin -->" in t, name
    sec = t.split("## 4. ")[1].split("## 5. ")[0]
    assert sec.index("gen:projection:begin") < sec.index("gen:stake:begin"), "section 4 leads with the projection"
    out = committed()
    card, fwd = out["card"], out["forward"]["card"]
    assert card["headlineSentence"] not in sec, "the whole-window headline is no longer the lead"
    ww = ("projected at the central action share", "projected at the central action share over each member's whole "
          "window")
    for k in ("costLineSentence", "costSentence"):
        assert card[k].replace(*ww, 1) in sec, f"{k} is not current in the PDF draft: run make readme"
    m = card["methodsSentence"]
    head_m, tail_m = m.split("Each member is credited over its window", 1)[0], m.split("is future work.", 1)[1]
    assert head_m in sec and tail_m in sec and "is future work" not in sec, "methods: run make readme"
    assert f"members chosen with what was known on {out['forward']['preRegistration']['D']}" in sec
    for k in ("negativeFindingSentence", "adoptionSentence", "line1Sentence", "line2Sentence"):
        assert card[k] in sec, f"{k} is not current in the PDF draft: run make readme"
    lead = [sec.index(fwd[k]) for k in ("headlineSentence", "stakeSentence", "intervalSentence", "rollingSentence",
                                        "comparisonSentence")]
    lead += [sec.index(card["costLineSentence"].replace(*ww, 1)), sec.index(head_m)]
    assert lead == sorted(lead), "the lead is the forward hold-out, the comparison, cost, then how we estimated it"
    assert sec.index(card["evidenceSentence"]) > sec.index(head_m) - 1
    assert card["evidenceSentence"] in m, "the null sits in the methods paragraph"
    assert sec.count("[PROJECTION]") == 1, "the projection is labelled once, at the headline"
    assert sec.count("not a measured reduction") == 1, "said once, in the methods paragraph"
    assert sec.count(card["evidenceSentence"]) == 1, "the null is said once"
    for k in ("projectionSentence", "scenariosSentence", "widerSetSentence"):
        assert card[k] not in sec, f"{k} repeats the hedging the methods paragraph says once"
    assert "capped" not in sec, "the evidence-capped figure is withdrawn"
    limits = t.split("## 7. ")[1].split("## 8. ")[0]
    assert card["limitSentence"] in limits
    split = ("; today's 'absent' label is decided inside the credit window;",
             ". Today's 'absent' label is decided inside the credit window;")
    assert fwd["limitsSentence"].replace(*split, 1) in limits and fwd["limitsSentence"].count(split[0]) == 1


def test_the_readme_headline_is_the_generated_one():
    """The README quotes the forward hold-out's generated headline verbatim (G2) and no longer the whole-window one;
    its central figure is projection.json's forward central figure (no hand-typed number)."""
    readme = ROOT / "README.md"
    if not readme.exists():
        pytest.skip("README.md is not in this checkout")
    out = committed()
    text = readme.read_text(encoding="utf-8")
    fwd = out["forward"]["card"]
    assert fwd["headlineSentence"] in text, "run make readme"
    assert out["card"]["headlineSentence"] not in text
    m = re.search(r"would stop ~([\d.]+) kt CH₄/yr at the central", fwd["headlineSentence"])
    assert m.group(1) == f"{fwd['centralKtCh4PerYr']:.1f}"


def hi_pf(scope: dict) -> dict:
    return max(scope["scenarios"], key=lambda r: r["responseShare"])


def test_vision_first_sentences_are_generated_and_say_what_they_are():
    out = interim()
    card, pf, bs = out["card"], out["scopes"]["portfolio_v3"], out["scopes"]["backtest_set"]
    p = out["parameters"]
    central = p["responseScenarios"]["central"]
    cr = next(r for r in pf["scenarios"] if r["responseShare"] == central)
    br = next(r for r in bs["scenarios"] if r["responseShare"] == central)
    hi = max(bs["scenarios"], key=lambda r: r["responseShare"])
    head = card["headlineSentence"]
    assert head.startswith("[PROJECTION]") and head.count("[PROJECTION]") == 1
    assert "[SIM]" in head and f"{pf['facilities']}-facility" in head
    c = p["ceasedShare"]
    assert "designed to stop" not in head, "a projection says 'would stop', not 'is designed to stop'"
    kt, g100 = cr["kt_ch4_per_yr"], cr["mt_co2e_per_yr_gwp100"]
    # interim (T4b review): the headline is the range between the two ends of the stop share at the central action
    # share, generated from projection.json, with no single central tonnage
    assert (f"would stop ~{kt['low']:,.0f}–~{kt['high']:,.0f} kt CH₄/yr at the central {central * 100:.0f} % "
            "action share, from one repair round net of new faults (Ravikumar 2020: "
            f"{c['low'] * 100:.0f} %) to every fix holding all year; ~{g100['low']:.2f}–{g100['high']:.2f} Mt CO₂e/yr "
            "at GWP-100 — assumptions: paper §4.") in head
    h = out["headline"]
    assert (h["kind"], h["central"], h["atActionShare"]) == ("range", None, central)
    assert h["ends"] == out["model"]["range"] and "T4c" in h["note"]
    assert (f"({projection.PORTFOLIO_SELECTION}; stake quantified at {pf['facilitiesQuantified']}: "
            f"{pf['facilities'] - pf['facilitiesEvaluated']} own no Carbon Mapper source, "
            f"{pf['facilitiesEvaluated'] - pf['facilitiesQuantified']} no quantified plume in the window)") in head, \
        "the headline names the portfolio's selection and says why only some facilities carry a quantified stake"
    scale = card["scaleSentence"]
    bk, b100 = br["kt_ch4_per_yr"], br["mt_co2e_per_yr_gwp100"]
    for s in (f"{bs['facilitiesQuantified']:,}", out["selection"]["backtest_set"],
              f"~{bk['low']:,.0f}–~{bk['high']:,.0f} kt", f"~{b100['low']:.2f}–{b100['high']:.2f} Mt",
              f"~{hi['kt_ch4_per_yr']['low']:,.0f}–~{hi['kt_ch4_per_yr']['high']:,.0f} kt"):
        assert s in scale, s
    floor = card["floorLineSentence"]
    assert floor.startswith("Stake, not abatement") and f"~{pf['stakeLowKtCh4PerYr']:,.0f} kt" in floor
    assert "[SIM]" in floor
    assert f"{pf['facilitiesQuantified']} of the portfolio's {pf['facilities']} facilities" in floor
    cost = card["costLineSentence"]
    assert f"USD {pf['ledger']['usdPerEpisode']:.2f}" in cost and "not the work on site" in cost
    m = card["methodsSentence"]
    assert m.startswith("How we estimated this.")
    assert m.count("ASSUMPTION") >= 2 and "Ravikumar et al. 2020" in m and "IEA" not in m
    assert "neither end was measured after a satellite notification" in m
    assert "Time is not yet modelled" in m and "the range spans them" in m and "no central tonnage" in m, \
        "the methods say plainly what the interim range spans (T4b review)"
    for w in ("delay from detection to repair", "how long an event lasts", "new faults", "stop on their own"):
        assert w in m, w
    assert "a repair rate" in m and "not re-appearing" in m, "the 0.90 is labelled a repair rate (T4b review)"
    assert "bounds the fixes replies already drive" in m, "b bounds reply-driven fixes only (T4b review)"
    assert "CARB publishes no emission rate per incidence" in m, "the methods say why the share counts incidences"
    rr = out["responseRule"]
    assert (f"operators answered {rr['answered']} of {rr['of']} oil and gas incidences; {rr['acted']} were faults "
            "unknown to the operator, and every one was stopped or repaired (CARB 2024)") in m, \
        "the action share names the published share it is informed by, and what that share counts"
    assert "action rate" not in m, "CARB's 39 % counts fixable faults, all fixed; it is not an action rate"
    assert f"{rr['confidence'] * 100:.0f} % sampling interval ({rr['of']} California incidences)" in m
    assert "does not cover the move from a regulator's notice to a rebate under a contract" in m
    assert f"at the top of the interval for the share of episodes sellers act on ({hi['responseShare'] * 100:.0f} %)" \
        in scale
    assert "The action share is an ASSUMPTION" in m and "The stop share is an ASSUMPTION" in m
    assert f"{p['baselineResponseShare']['value'] * 100:.0f} % reply rate (a reply, not an action)" in m
    assert card["evidenceSentence"] in m
    assert m.count(projection.PROJ) == 1
    assert "holds between them" in m, "the stake's pass assumption is said in the methods paragraph"
    # projection review 2026-10-20: the README quotes the headline alone, so it ends by pointing to the assumptions
    assert head.endswith("— " + projection.HEADLINE_END) and head.startswith("[PROJECTION]")
    for k in ("headlineSentence", "scaleSentence", "floorLineSentence", "costLineSentence"):
        assert "not a measured reduction" not in card[k] and "ASSUMPTION" not in card[k], f"{k}: said once, in methods"
        assert "answered" not in card[k] and "measured reduction" not in card[k], k
    for k in ("headlineSentence", "scaleSentence"):
        assert "act on" in card[k], f"{k}: lead with the action"


# ------------------------------------------------------------------------------------------- empirical evidence lines
def test_wilson_and_newcombe_match_the_published_worked_examples():
    """Newcombe (1998), Stat Med 17:873: Wilson 81/263 -> 0.2553 to 0.3662; method 10 for 56/70 - 48/80 -> 0.0524 to
    0.3339."""
    lo, hi = evidence.wilson(81, 263, 0.95)
    assert (round(lo, 4), round(hi, 4)) == (0.2553, 0.3662)
    lo, hi = evidence.newcombe(56, 70, 48, 80, 0.95)
    assert (round(lo, 4), round(hi, 4)) == (0.0524, 0.3339)


def _notified_og() -> list[dict]:
    with open(DERIVED / "mars_sources.csv", newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f) if r["sector"] == "Oil and Gas" and r["notified"] == "Yes"]


def test_line1_counts_match_the_mars_csv():
    og = _notified_og()
    l1 = committed()["evidence"]["line1"]
    for g in ("Yes", "No"):
        rows = [r for r in og if r["feedback"] == g and r["persistency_category"] != "Undetermined"]
        assert l1["byFeedback"][g]["determined"] == len(rows)
        assert l1["byFeedback"][g]["absent"] == sum(r["persistency_category"] == "absent" for r in rows)
        assert sum(s[g]["determined"] for s in l1["byPlumeStratum"].values()) == len(rows)
    assert "not a confirmed stop" in l1["outcome"] and "absent from the data" in l1["outcome"]


def test_line2_funnel_accounts_for_every_source_and_names_its_threats():
    og = _notified_og()
    l2 = committed()["evidence"]["line2"]
    for cov in l2["coverageSets"].values():
        for run in [cov["primary"], *cov["variants"]]:
            for g in ("Yes", "No"):
                n = sum(v for k, v in run["funnel"].items() if k.startswith(f"{g}:"))
                assert n == sum(r["feedback"] == g for r in og), (g, run["spec"])
                assert run["groups"][g]["sources"] == run["funnel"].get(f"{g}: used", 0)
    n_var = len(sources()["parameters"]["evidence"]["eventStudy"]["variants"])
    assert all(len(c["variants"]) == n_var for c in l2["coverageSets"].values())
    t = " ".join(l2["threats"])
    for w in ("proxy", "November 2024", "tasked", "regression to the mean"):
        assert w in t, w
    full = l2["coverageSets"]["allNotified"]["primary"]["funnel"]
    assert not any("no committed timeline" in k for k in full), "the notified fetch must complete the coverage"


def test_event_study_detects_a_planted_drop():
    """A synthetic responder whose detections stop after T0 against a non-responder that keeps emitting: the
    difference must be clearly negative (the method can see what it claims to look for)."""
    ev = sources()["parameters"]["evidence"]
    days = [f"{y}-{m:02d}-15" for y in ("2025", "2026") for m in range(1, 13)]
    t0 = "2025-12-31"
    tl, facs, t0s = {}, {}, {}
    for i in range(ev["minSourcesPerGroup"] * 2):
        g = "Yes" if i % 2 == 0 else "No"
        tl[f"cm{i}"] = {"obs": [[d, "emi"] for d in days], "det": [d for d in days if g == "No" or d < t0]}
        facs[f"m{i}"] = {"feedback": g, "cm": [f"cm{i}"], "complete": True}
        t0s[f"m{i}"] = t0
    run = evidence.did_run(facs, t0s, tl, ev["eventStudy"]["primary"], ev, same_t0=set(), catalogue_end=days[-1])
    assert run["did"]["yesMinusNo"] < 0 and run["did"]["ci"][1] < 0


def test_mars_notified_is_derived_from_the_committed_snapshot():
    import mars_notified

    d = json.loads((DERIVED / "mars_notified.json").read_text(encoding="utf-8"))
    man = json.loads((ROOT / "data" / "MANIFEST.json").read_text(encoding="utf-8"))
    assert d["zipSha256"] == man["files"]["mars_zip"]["sha256"]
    got = mars_notified.derive([
        {"sector": "Oil and Gas", "notified": True, "source_name": "A", "tile_date": "2025-03-01T10:00:00"},
        {"sector": "Oil and Gas", "notified": True, "source_name": "A", "tile_date": "2024-07-01T10:00:00"},
        {"sector": "Oil and Gas", "notified": False, "source_name": "A", "tile_date": "2023-01-01T10:00:00"},
        {"sector": "Coal", "notified": True, "source_name": "B", "tile_date": "2024-01-01T10:00:00"},
        {"sector": "Oil and Gas", "notified": True, "source_name": None, "tile_date": "2024-01-01T10:00:00"}])
    assert got == {"A": {"firstNotified": "2024-07-01", "notifiedPlumes": 2}}
    assert {r["source_name"] for r in _notified_og()} <= set(d["sources"])


def test_notified_timelines_record_their_fetch():
    d = json.loads((DERIVED / "cm_timelines_notified.json").read_text(encoding="utf-8"))
    assert d["fetchedAt"] and d["urlPrefix"].startswith("https://")
    base = json.loads((DERIVED / "cm_timelines.json").read_text(encoding="utf-8"))
    assert not set(d["sources"]) & set(base), "the extra file never overlaps cm_timelines.json"
    assert all(re.fullmatch(r"[0-9a-f]{64}", v["contentSha256"]) for v in d["sources"].values())


def test_evidence_sentence_is_the_reviewed_wording_with_generated_numbers():
    out = committed()
    prim = out["evidence"]["line2"]["coverageSets"]["allNotified"]["primary"]
    used = prim["groups"]["Yes"]["sources"] + prim["groups"]["No"]["sources"]
    mde = f"{prim['did']['minimumDetectableShareOfPreRate'] * 100:.0f} %"
    assert out["card"]["evidenceSentence"] == (
        f"Our two checks (MARS 'absent' label by feedback; Carbon Mapper detection rates around {used:,} notified "
        "sources) find no difference between answered and unanswered sources; the design cannot detect a drop smaller "
        f"than {mde} of the detection rate and cannot see a partial mass cut at all, so they neither support nor bound "
        "this band.")
    s = out["card"]["line2Sentence"]
    agree = prim["groups"]["Yes"]["proxyDatesAgree"] + prim["groups"]["No"]["proxyDatesAgree"]
    for w in ("proxy date", f"{agree} of the {used} sources used", "minimum detectable effect", "November 2024",
              "regression to the mean", "tasking"):
        assert w in s, w
    assert "not a stop" in out["card"]["line1Sentence"]


def test_a_non_null_refuses_the_no_difference_sentence():
    import projection as pr

    evd = json.loads(OUT.read_text(encoding="utf-8"))["evidence"]
    bad = copy.deepcopy(evd)
    bad["line2"]["coverageSets"]["allNotified"]["allCiIncludeZero"] = False
    with pytest.raises(ValueError):
        pr._evidence_cards(bad, sources()["parameters"])


def test_the_withdrawn_cap_appears_nowhere():
    text = OUT.read_text(encoding="utf-8")
    assert "evidenceCapped" not in text and "impliedMaxDrop" not in text


def test_funnel_flags_reproduce_the_review():
    """The reviewer's counts (spec-v2 §15 projection review) are what the code writes, and are subsets of 'used'."""
    for cov in committed()["evidence"]["line2"]["coverageSets"].values():
        for run in [cov["primary"], *cov["variants"]]:
            for g in run["groups"].values():
                for k in ("t0BeforeFeedbackTracking", "postWindowTruncated", "proxyDatesAgree"):
                    assert 0 <= g[k] <= g["sources"], k
    g = committed()["evidence"]["line2"]["coverageSets"]["allNotified"]["primary"]["groups"]
    assert (g["Yes"]["sources"], g["No"]["sources"]) == (83, 354)
    assert (g["Yes"]["t0BeforeFeedbackTracking"], g["No"]["t0BeforeFeedbackTracking"]) == (40, 97)
    assert (g["Yes"]["postWindowTruncated"], g["No"]["postWindowTruncated"]) == (9, 105)
