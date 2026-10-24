"""Carbon projection: what the clause could change if adopted, with the stake as the floor (IEEE "carbon projection").

    cd pipeline && .venv/bin/python projection.py     # -> data/derived/projection.json (offline, committed inputs)

A projection, not a measured change. For each scope (the portfolio covenant set v3 [SIM]; all facilities of the
backtest with a quantified stake) and each response scenario r (ASSUMPTION), per band b of the share stopped:

    kt CH4/yr stopped = stake low bound (kt CH4/yr, backtest.json, taken as given)
                        x (r - baseline response share)            (only the response above today's counts)
                        x ceased share[b]                          (share of a fixed source's yearly emissions that
                                                                    stops: two ends, b = low or high)

Board T4b (pre-registered in projection_sources.json -> model, amended after the adversarial review): r is the only
factor that removes emissions a repair cannot touch (CARB Type A over incidences sent), the baseline is subtracted from r
only and bounds reply-driven fixes, and the ceased share is an interim range with two ends (one repair round net of new
faults; every fix holding all year), so the headline is a range with no central tonnage until board T4c models time.
`check_model` refuses a range end that removes an emission class twice or is measured on another population than the
criterion requires, and an emission class that is neither removed by exactly one factor nor spanned by the range.

CO2e with GWP-100 and GWP-20 from floors.json. Ledger cost per tonne = (episodes per year at the scope, from the
backtest, x USD per episode on Ethereum Sepolia + one createCovenant) / projected t CO2e; the cost of the clause's
records only, not the cost of the work on site. Every parameter and its source is in data/derived/projection_sources.json;
this file types no parameter value (pipeline/tests/test_projection.py checks the literals).

Negative finding (stated in the output): in the MARS cross-section, oil and gas sources with feedback = Yes have
higher persistency and more plumes than those with feedback = No; MARS publishes no notification or response date, so
no before/after estimate can be built from MARS and the projection leans on published evidence instead.
"""
from __future__ import annotations

import csv
import math
import re
import statistics
from datetime import date
from pathlib import Path

import projection_evidence as evidence
import projection_time as ptime
from common import DERIVED, RADIUS_KM, ROOT, haversine_km, sha256_file, write_json

SOURCES = DERIVED / "projection_sources.json"
OUT = DERIVED / "projection.json"
INPUTS = ["backtest.json", "sourceset_v3.json", "floors.json", "measurements.json", "mars_sources.csv",
          "projection_sources.json", "mars_notified.json", "mars_plumes_og.csv", "cm_sources.csv", "cm_timelines.json",
          "cm_timelines_notified.json", "carb_summary.json", "cm_plumes.csv", "cm_sources.csv",
          "cm_timelines_not_mars_notified.json", "join_v1.json"]
CODE = ["projection.py", "projection_evidence.py", "projection_time.py", "backtest.py"]
DAYS_PER_YEAR = 365.25
T_PER_KT = KT_PER_MT = 1000
PERCENT = 100
# Unit conversions and rounding digits: the only numeric literals allowed in this file (tests/test_projection.py).
UNIT_AND_FORMAT_CONSTANTS = {0, 1, 2, 3, 4, DAYS_PER_YEAR, T_PER_KT, PERCENT}
# the two ends of the interim stop-share range (board T4b review, 2026-10-21): no central value until the time factor
# is built (board T4c)
BANDS = ("low", "high")
PROJ = "projection of what the clause could change if adopted, not a measured reduction"
PORTFOLIO_SELECTION = "members chosen because each showed a Carbon Mapper plume MARS did not see"
HEADLINE_SELECTION = "members chosen for a Carbon Mapper plume not in MARS"  # the T4d headline's shorter wording
FIX = "the published share of notified incidences that were fixable faults, all fixed"
# the README quotes the headline alone (projection review 2026-10-20): it ends by pointing to the assumptions; the
# caveat "not a measured reduction" stays once, in the methods paragraph (user decision 2026-10-16)
HEADLINE_END = "assumptions: paper §4."


def _load_json(name: str):
    import json

    return json.loads((DERIVED / name).read_text(encoding="utf-8"))


def _csv(name: str) -> list[dict]:
    with open(DERIVED / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_inputs() -> dict:
    return {"backtest": _load_json("backtest.json"), "sourceset": _load_json("sourceset_v3.json"),
            "floors": _load_json("floors.json"), "measurements": _load_json("measurements.json"),
            "params": _load_json("projection_sources.json"), "mars": _csv("mars_sources.csv"),
            "marsNotified": _load_json("mars_notified.json")["sources"], "marsPlumes": _csv("mars_plumes_og.csv"),
            "cmSources": _csv("cm_sources.csv"), "cmTimelines": _load_json("cm_timelines.json"),
            "cmTimelinesNotified": _load_json("cm_timelines_notified.json")["sources"],
            "carbSummary": _load_json("carb_summary.json"), "facilities": _facilities(),
            "cmNotMarsNotified": _load_json("cm_timelines_not_mars_notified.json"), "join": _load_json("join_v1.json")}


def _facilities() -> dict:
    """Each MARS target's passes and detections, rebuilt from committed files by backtest.facilities() (the series the
    stake's duty cycle counts), with the catalogue end."""
    import backtest

    facs, end, _ = backtest.facilities()
    return {"byName": {f["name"]: f for f in facs}, "end": end}


def _evidence(inp: dict) -> dict:
    ev = inp["params"]["parameters"]["evidence"]
    joined = {**inp["cmTimelines"], **inp["cmTimelinesNotified"]}
    coverage = {
        "allNotified": ("every notified oil and gas source with feedback Yes or No: cm_timelines.json plus "
                        "cm_timelines_notified.json", joined),
        "joinNeighbourhood": ("cm_timelines.json only: the neighbourhood of the sources MARS labels absent today, a "
                              "label decided after the window", inp["cmTimelines"]),
    }
    return {"line1": evidence.line1(inp["mars"], ev),
            "line2": evidence.line2(inp["mars"], inp["marsNotified"], inp["marsPlumes"], inp["cmSources"], coverage,
                                    ev, RADIUS_KM)}


# ------------------------------------------------------------------------------------------------------- formatting
def _pct(v: float) -> str:
    return f"{v * PERCENT:.0f} %"


def _kt(x: float) -> str:
    return f"~{x:,.0f} kt"


def _mt(x: float) -> str:
    return f"~{x:.2f} Mt"


def _usd_small(x: float) -> str:
    """Two significant digits in plain decimals (0.00045, not 4.5e-04)."""
    return f"USD {x:.{max(0, 1 - math.floor(math.log10(x)))}f}"


def _sig4(x: float) -> float:
    return float(f"{x:.4g}")


# ----------------------------------------------------------------------------------------------------------- pieces
def _rule_counts(p: dict) -> tuple[int, int]:
    rule = p["responseScenarios"]["rule"]
    return sum(r[rule["acted"]] for r in rule["rows"]), sum(r[rule["of"]] for r in rule["rows"])


def _rows_sum(rows: list[dict], cols: list[str]) -> int:
    return sum(r[c] for r in rows for c in cols)


def response_scenarios(p: dict) -> tuple[list[float], float]:
    """The action scenarios by the rule written in projection_sources.json (responseScenarios.rule): central = the
    published share of notified incidences that were fixable faults (CARB Type A, every one stopped or repaired),
    low/high = its Wilson interval at the rule's own confidence, two decimals."""
    k, n = _rule_counts(p)
    lo, hi = evidence.wilson(k, n, p["responseScenarios"]["rule"]["confidence"])
    central = round(k / n, 2)
    return [round(lo, 2), central, round(hi, 2)], central


def _response_rule(p: dict) -> dict:
    """Fails closed if the scenario values in the sources file are not what their rule gives."""
    rs = p["responseScenarios"]
    rule = rs["rule"]
    values, central = response_scenarios(p)
    if sorted(rs["values"]) != values or rs["central"] != central:
        raise ValueError(f"responseScenarios {rs['values']} (central {rs['central']}) do not follow their rule, which "
                         f"gives {values} (central {central}): fix projection_sources.json, do not pick values")
    k, n = _rule_counts(p)
    lo, hi = evidence.wilson(k, n, rule["confidence"])
    return {"source": rule["source"], "acted": k, "of": n, "share": round(k / n, 4),
            "answered": n - _rows_sum(rule["rows"], ["didNotRespond"]),
            "interval": [round(lo, 4), round(hi, 4)], "confidence": rule["confidence"]}


def baseline_share(prm: dict) -> tuple[float, str]:
    """The baseline by its written rule: the most recent published MARS response share among the rule's candidates;
    the value is the per-cent figure in that candidate's quote. Fails closed if the parameter differs."""
    b =prm["parameters"]["baselineResponseShare"]
    latest = max(b["rule"]["candidates"], key=lambda c: prm["sources"][c["source"]]["date"])
    quote = prm["sources"][latest["source"]]["quotes"][latest["quote"]]
    m = re.search(r"(\d+) per cent", quote)
    if not m or int(m.group(1)) != round(b["value"] * PERCENT) or latest["source"] not in b["sources"]:
        raise ValueError(f"baselineResponseShare {b['value']} is not the most recent published MARS response share "
                         f"({latest['source']}: {quote!r}): fix projection_sources.json")
    return b["value"], latest["cite"]


# ------------------------------------------------------------------- T4b: one meaning per factor (model, pre-registered)
def pairing_violations(spec: dict, cls: dict) -> list[str]:
    """What breaks the pre-registered criterion in a pairing of r (action share) and d (stop share): an emission class
    removed by both factors, a factor measured on another population than the criterion requires, a change credited
    that a repair did not cause, or a class a repair cannot touch that r leaves in. Empty list = the pairing meets it."""
    r, d = spec["r"], spec["d"]
    out = [f"removes {c!r} twice (r and d)" for c in r["removes"] if c in d["removes"]]
    out += [f"{f} is measured on {spec[f]['population']!r}, not the required population {cls['required'][f]!r}"
            for f in ("r", "d") if spec[f]["population"] != cls["required"][f]]
    out += [f"d credits {c!r}, a change a repair did not cause" for c in d.get("credits") or []]
    out += [f"r does not remove {c!r}, which a repair cannot touch" for c in cls["notRepairable"] if c not in r["removes"]]
    return out


def check_model(prm: dict) -> dict:
    """Fails closed unless (i) both ends of the interim range meet criterion clauses (1) and (2), (ii) the prose flags
    of every option agree with the encoded classes, (iii) no option claims clause (3) while the headline is a range, and
    (iv) every emission class in classes.list is removed by exactly one factor or spanned by the interim range."""
    m = prm["model"]
    cls = m["classes"]
    viol = {k: pairing_violations(v, cls) for k, v in cls["pairings"].items()}
    ends = {b: m["range"][b] for b in BANDS}
    for b, oid in ends.items():
        if viol[oid]:
            raise ValueError(f"the {b} end of the range (option {oid!r}) breaks the criterion: {'; '.join(viol[oid])}")
    for oid, opt in m["options"].items():
        r, d = cls["pairings"][oid]["r"], cls["pairings"][oid]["d"]
        one = set(cls["notRepairable"]) <= set(r["removes"]) and r["population"] == cls["required"]["r"]
        two = (d["population"] == cls["required"]["d"] and not set(r["removes"]) & set(d["removes"])
               and not d.get("credits"))
        if (opt["meets1"], opt["meets2"]) != (one, two):
            raise ValueError(f"model.options.{oid} says meets1={opt['meets1']}, meets2={opt['meets2']}; the classes give "
                             f"{one}, {two}: fix projection_sources.json")
    if m["choice"] != "range" or any(o["meets3"] for o in m["options"].values()):
        raise ValueError("an option meeting clause (3) needs the time factor (board T4c); the interim headline is a range")
    ledger = {}
    for c in cls["list"]:
        at_ends = [[f for f, rm in (("r", cls["pairings"][oid]["r"]["removes"]),
                                    ("d", cls["pairings"][oid]["d"]["removes"]), ("b", cls["b"]["removes"]))
                    if c in rm] for oid in ends.values()]
        if c in cls["removedBy"]:
            if c in cls["spannedByInterimRange"] or any(f != [cls["removedBy"][c]] for f in at_ends):
                raise ValueError(f"emission class {c!r} is not removed by exactly one factor ({cls['removedBy'][c]}) at "
                                 f"both ends of the range: {at_ends}")
            ledger[c] = cls["removedBy"][c]
        elif c in cls["spannedByInterimRange"]:
            if any(len(f) > 1 or "r" in f or "b" in f for f in at_ends):
                raise ValueError(f"emission class {c!r} is spanned by the range but removed by {at_ends}")
            ledger[c] = "spanned by the interim range"
        else:
            raise ValueError(f"emission class {c!r} is neither removed by one factor nor spanned by the interim range")
    return {"choice": m["choice"], "range": ends, "criterion": m["criterion"], "violations": viol,
            "emissionClasses": ledger}


def _quote_share(prm: dict, ref: dict) -> float:
    q = prm["sources"][ref["source"]]["quotes"][ref["quote"]]
    if ref.get("regex"):
        m = re.search(ref["regex"], q)
        if not m:
            raise ValueError(f"{ref['source']} quote {ref['quote']} no longer matches {ref['regex']!r}")
        w = ref.get("with")
        if w and w["phrase"] not in prm["sources"][ref["source"]]["quotes"][w["quote"]]:
            raise ValueError(f"{ref['source']} quote {w['quote']} no longer states {w['phrase']!r}")
        return int(m.group(1)) / PERCENT
    if ref.get("all") and ref["all"] in q:
        return 1
    raise ValueError(f"{ref['source']} quote {ref['quote']} does not state {ref.get('all')!r}")


def stop_shares(prm: dict) -> dict:
    """The stop share by its written rule (ceasedShare.rule): each band read from its quote; fails closed if the value
    in the sources file differs from its quote."""
    c = prm["parameters"]["ceasedShare"]
    rule = c["rule"]
    got = {b: _quote_share(prm, {"source": rule["source"], **rule["bands"][b]}) for b in BANDS}
    if any(got[b] != c[b] for b in BANDS):
        raise ValueError(f"ceasedShare {[c[b] for b in BANDS]} does not follow its quotes, which give "
                         f"{[got[b] for b in BANDS]}: fix projection_sources.json, do not pick values")
    return got


def _check_carb_summary(p: dict, prm: dict, cs: dict) -> None:
    """The committed CARB aggregates (data/derived/carb_summary.json, from Appendix Tables A1/A2; the per-incidence rows
    are rebuilt locally by make fetch-published) are the recorded appendix's, unedited, add up, and reproduce the
    Table 3 rows of their years outcome for outcome; anything else refuses to run."""
    rule = p["responseScenarios"]["rule"]
    src = prm["sources"][rule["incidences"]["source"]]
    if (cs["source"], cs["sha256"], cs["url"]) != (rule["incidences"]["source"], src["sha256"], src["url"]):
        raise ValueError("carb_summary.json is not from the appendix recorded in projection_sources.json")
    from fetch_published import aggregates_hash

    agg = cs["aggregates"]
    if aggregates_hash(agg) != cs["aggregatesJcsSha256"]:
        raise ValueError("carb_summary.json aggregates differ from the ones extracted from the appendix: re-run "
                         "fetch_published.py --write-summary, do not edit the summary")
    totals = [sum(sum(c.values()) for c in agg[k].values()) for k in ("byYearOutcome", "byPlumesOutcome")]
    if totals != [agg["incidences"]] * len(totals) or cs["rowsFile"]["rows"] != agg["incidences"]:
        raise ValueError(f"carb_summary.json counts {totals} do not add up to its {agg['incidences']} incidences")
    for row in (r for r in rule["rows"] if r["year"] in agg["years"]):
        counts = agg["byYearOutcome"][str(row["year"])]
        if sum(counts.values()) != row[rule["of"]] or any(counts.get(c, 0) != row[c] for c in rule["columns"]
                                                          if c != rule["of"]):
            raise ValueError(f"carb_summary.json {row['year']} counts {counts} do not reproduce Table 3 {row}: "
                             "re-run fetch_published.py, do not edit the summary")


def _plume_counts(cs: dict) -> list[tuple[int, dict]]:
    """(number of plumes, incidences by outcome) for every plume count in the CARB aggregates."""
    return [(int(n), c) for n, c in cs["aggregates"]["byPlumesOutcome"].items()]


def _weighting(cs: dict, prm: dict) -> dict:
    agg = cs["aggregates"]
    return {"source": cs["source"], "incidences": agg["incidences"], "years": agg["years"],
            "uniqueJoinKey": agg["uniqueJoinKey"],
            "joinKey": "year, earliest plume date and number of plumes: the only fields CARB's rows share with a "
                       "Carbon Mapper plume list",
            "mass": {"computable": False, "reason": cs["missing"]},
            "decision": prm["model"]["rules"]["massWeighting"]}


def _model_sensitivity(p: dict, prm: dict, cs: dict, stakes: dict[str, float]) -> list[dict]:
    """model.sensitivity: the chosen model next to its weighting variants and the two other pairings (option a, the T4
    model), each r and d from the committed CARB aggregates and quotes, with the portfolio and backtest kt at the
    central value."""
    rule, base = p["responseScenarios"]["rule"], p["baselineResponseShare"]["value"]
    sent = _rows_sum(rule["rows"], [rule["of"]])
    by_n = _plume_counts(cs)
    plume = sum(n * c[rule["acted"]] for n, c in by_n) / sum(n * sum(c.values()) for n, c in by_n)
    rs = {"rule": p["responseScenarios"]["central"],
          "answered": round((sent - _rows_sum(rule["rows"], ["didNotRespond"])) / sent, 2),
          "plumeWeighted": round(plume, 2)}
    ds = {**{b: p["ceasedShare"][b] for b in BANDS},
          "ravikumarTotal": _quote_share(prm, prm["model"]["reads"]["ravikumarTotal"])}
    out = []
    for s in prm["model"]["sensitivity"]:
        end = s["d"] if s["d"] in BANDS and s["r"] == "rule" and prm["model"]["range"][s["d"]] == s["pairing"] else None
        row = {"id": s["id"], "label": s["label"], "pairing": s["pairing"], "rangeEnd": end}
        if s["r"] not in rs:
            out.append({**row, "computable": False, "reason": cs["missing"]})
            continue
        r, d = rs[s["r"]], ds[s["d"]]
        out.append({**row, "computable": True, "r": r, "d": d, "aboveBaseline": round(r - base, 4),
                    "ktCh4PerYrCentral": {n: round(v * (r - base) * d, 2) for n, v in stakes.items()}})
    return out


def _sensitivity(p: dict, rule: dict, stakes: dict[str, float]) -> list[dict]:
    """Each definition of the action share in responseScenarios.rule.sensitivity, computed from the same table rows,
    with what it would project at either end of the stop share (generated, so the rule's choice is visible)."""
    rows, base, c = p["responseScenarios"]["rule"]["rows"], p["baselineResponseShare"]["value"], p["ceasedShare"]
    out = []
    for d in p["responseScenarios"]["rule"]["sensitivity"]:
        sel = [r for r in rows if d["years"] is None or r["year"] in d["years"]]
        num = _rows_sum(sel, d["numerator"])
        den = _rows_sum(sel, d["denominator"]) - _rows_sum(sel, d["subtract"])
        share = num / den
        out.append({"id": d["id"], "label": d["label"], "numerator": num, "denominator": den,
                    "share": round(share, 4), "isRule": (num, den) == (rule["acted"], rule["of"]),
                    "ktCh4PerYrByStopEnd": {s: {b: round(v * (share - base) * c[b], 2) for b in BANDS}
                                            for s, v in stakes.items()}})
    return out


def _episodes_per_year(facs: list[dict], end: date) -> float:
    total = 0.0
    for f in facs:
        if f.get("evaluated") and f.get("episodes"):
            total += len(f["episodes"]) / ((end - date.fromisoformat(f["start"])).days / DAYS_PER_YEAR)
    return round(total, 3)


def _scope(name: str, facs: list[dict], stake_low: float, n_quant: int, p: dict, gwp: dict, ledger_fn) -> dict:
    base = p["baselineResponseShare"]["value"]
    ceased = p["ceasedShare"]
    ledger = ledger_fn(facs)
    rows = []
    for r in sorted(p["responseScenarios"]["values"]):
        inc = r - base
        if inc <= 0:
            raise ValueError(f"{name}: response scenario {r} is not above the baseline {base}; nothing to project")
        kt = {b: round(stake_low * inc * ceased[b], 2) for b in BANDS}
        rows.append({
            "responseShare": r, "aboveBaseline": round(inc, 4),
            "kt_ch4_per_yr": kt,
            "mt_co2e_per_yr_gwp100": {b: round(kt[b] * gwp["gwp100"] / KT_PER_MT, 3) for b in BANDS},
            "mt_co2e_per_yr_gwp20": {b: round(kt[b] * gwp["gwp20"] / KT_PER_MT, 3) for b in BANDS},
            "ledgerUsdPerTco2eGwp100": {b: _sig4(ledger["usdPerYear"] / (kt[b] * T_PER_KT * gwp["gwp100"]))
                                        for b in BANDS},
        })
    return {"facilities": len(facs), "facilitiesEvaluated": sum(1 for f in facs if f.get("evaluated")),
            "facilitiesQuantified": n_quant, "stakeLowKtCh4PerYr": stake_low, "ledger": ledger, "scenarios": rows}


def _negative_finding(mars: list[dict]) -> dict:
    og = [r for r in mars if r["sector"] == "Oil and Gas"]
    by = {}
    for fb in ("Yes", "No"):
        rows = [r for r in og if r["feedback"] == fb]
        pers = [float(r["persistency"]) for r in rows if r["persistency"] != ""]
        plumes = [int(r["n_plumes_detected"]) for r in rows]
        by[fb] = {"sources": len(rows), "withPersistency": len(pers),
                  "meanPersistency": round(statistics.mean(pers), 3),
                  "medianPersistency": statistics.median(pers),
                  "meanPlumes": round(statistics.mean(plumes), 2), "medianPlumes": statistics.median(plumes),
                  "plumesTotal": sum(plumes),
                  "persistentOrFrequent": sum(r["persistency_category"] in ("persistent", "frequent") for r in rows)}
    notified = sum(r["notified"] == "Yes" for r in og)
    y, n = by["Yes"], by["No"]
    sentence = (
        f"In MARS's own cross-section the answered sources are not the cleaner ones: of the {notified:,} notified oil "
        f"and gas sources, the {y['sources']} with feedback = Yes (operator or government) have a mean persistency of "
        f"{y['meanPersistency']:.3f} and "
        f"{y['meanPlumes']} plumes on average ({y['persistentOrFrequent']} persistent or frequent), against "
        f"{n['meanPersistency']:.3f} and {n['meanPlumes']} ({n['persistentOrFrequent']}) for the {n['sources']:,} with "
        "feedback = No; a cross-section cannot separate what a reply changes from which sources get replies "
        "(recurring sources draw more alerts). MARS publishes no notification or response date for a source, so no "
        "before/after estimate can be built from MARS alone: our event study needs a proxy date, and the projection's "
        "stop share is an ASSUMPTION informed by published figures.")
    return {"sector": "Oil and Gas", "file": "data/derived/mars_sources.csv", "notified": notified,
            "feedbackYesShareOfNotified": round(y["sources"] / notified, 3),
            "feedbackYesShareNote": "sources with feedback = Yes over notified sources; UNEP's rate counts alerts, "
                                    "not sources, so the two differ",
            "byFeedback": by, "sentence": sentence}


def build(inp: dict) -> dict:
    bt, prm = inp["backtest"], inp["params"]
    p = prm["parameters"]
    if p["responseScenarios"]["central"] not in p["responseScenarios"]["values"]:
        raise ValueError("the central response scenario must be one of the scenarios")
    model = check_model(prm)  # one meaning per factor (T4b) before anything is projected
    rule = _response_rule(p)  # the scenarios follow their written rule
    rule["baselineCite"] = baseline_share(prm)[1]  # the baseline follows its written rule too
    stop_shares(prm)  # and so do the two ends of the stop share
    _check_carb_summary(p, prm, inp["carbSummary"])
    gwp = {"gwp100": inp["floors"]["gwp"]["gwp100_fossil_ch4"]["value"],
           "gwp20": inp["floors"]["gwp"]["gwp20_fossil_ch4"]["value"]}
    meas = inp["measurements"]
    fn = meas["functions"]
    calls = p["ledgerCalls"]
    per_ep = sum(fn[c]["usd_at_stated_gwei"] for c in calls["perEpisode"])
    per_cov = sum(fn[c]["usd_at_stated_gwei"] for c in calls["perCovenantYear"])
    end = date.fromisoformat(bt["catalogueEnd"])

    def ledger(facs: list[dict]) -> dict:
        return ledger_at(_episodes_per_year(facs, end))

    def ledger_at(epy: float) -> dict:
        return {"episodesPerYear": epy, "usdPerEpisode": round(per_ep, 4), "usdPerCovenantYear": round(per_cov, 4),
                "usdPerYear": round(epy * per_ep + per_cov, 2), "calls": calls,
                "statedGwei": meas["usd_assumption"]["stated_gwei"], "ethUsd": meas["usd_assumption"]["eth_usd"],
                "measuredAt": meas["measuredAt"]}

    by_name = {f["name"]: f for f in bt["facilities"]}
    members = [s["source_name"] for s in inp["sourceset"]["sources"]]
    missing = [m for m in members if m not in by_name]
    if missing:
        raise ValueError(f"portfolio members not in backtest.json: {missing}")
    pf = [by_name[m] for m in members]
    pf_q = [f for f in pf if f.get("evaluated") and f.get("stake", {}).get("status") == "quantified"]
    all_eval = [f for f in bt["facilities"] if f.get("evaluated")]
    sb = bt["stake_band"]
    evd = _evidence(inp)
    scopes = {
        "portfolio_v3": _scope("portfolio_v3", pf, round(sum(f["stake"]["kt_ch4_per_yr"]["low"] for f in pf_q), 2),
                               len(pf_q), p, gwp, ledger),
        "backtest_set": _scope("backtest_set", all_eval, sb["kt_ch4_per_yr"]["low"], sb["facilitiesQuantified"], p,
                               gwp, ledger),
    }
    neg = _negative_finding(inp["mars"])
    stakes = {n: s["stakeLowKtCh4PerYr"] for n, s in scopes.items()}
    sens = _sensitivity(p, rule, stakes)
    msens = _model_sensitivity(p, prm, inp["carbSummary"], stakes)
    if not any(d["isRule"] for d in sens) or rule["share"] != min(d["share"] for d in sens):
        raise ValueError("responseScenarios.rule is no longer the lowest of its sensitivity definitions: re-review it")
    t4d = _t4d(inp, prm, {"portfolio_v3": pf, "backtest_set": all_eval}, scopes, gwp)
    interim_card = _all_cards(scopes, p, gwp, neg, bt["selection"], evd, rule)
    interim_head = {"kind": "range", "central": None, "atActionShare": p["responseScenarios"]["central"],
                    "ends": {b: prm["model"]["range"][b] for b in BANDS}, "note": prm["model"]["range"]["note"]}
    if t4d["headline"]["kind"] == "central":  # board T4d option d is published (model.t4d.rules.headline)
        pub_scopes = _t4d_scopes(scopes, t4d)
        card = interim_card | _t4d_cards(pub_scopes, p, bt["selection"], interim_card["evidenceSentence"], rule, t4d)
        head = {"kind": "central", "model": "t4d", "option": t4d["model"]["choice"],
                "central": t4d["headline"]["central"], "upToNoRecurrence": t4d["headline"]["upToNoRecurrence"],
                "atActionShare": p["responseScenarios"]["central"], "note": prm["model"]["t4d"]["rules"]["headline"]}
        formula = ("kt CH4/yr = sum over quantified facilities of stake low bound x the fixed share of its emitting time "
                   "(action share by plume count x credited days / emitting days, board T4d option d)")
    else:
        pub_scopes, card, head = scopes, interim_card, interim_head
        formula = ("kt CH4/yr = stake low bound x (action scenario - MARS reply rate) x share of a fixed fault's "
                   "emissions that stays stopped")
    return {"schema": "methaneclause.projection/1",
            "label": f"[PROJECTION] {PROJ}; counterfactual inputs (the backtest), scenario parameters (ASSUMPTION) "
                     "and published evidence (projection_sources.json)",
            "formula": formula,
            "selection": {"portfolio_v3": f"the portfolio covenant set v3 [SIM], {PORTFOLIO_SELECTION}",
                          "backtest_set": bt["selection"]},
            "headline": head,
            "parameters": p, "model": model, "responseRule": rule, "responseSensitivity": sens,
            "modelSensitivity": msens, "weighting": _weighting(inp["carbSummary"], prm), "gwp": gwp,
            "scopes": pub_scopes, "negativeFinding": neg, "t4d": t4d,
            "interim": {"headline": interim_head, "scopes": scopes, "card": interim_card},
            "evidence": evd, "card": card,
            "forward": _forward(inp, prm, bt, gwp, t4d["scopes"]["portfolio_v3"], pub_scopes["portfolio_v3"],
                                ledger_at),
            "generatedFrom": [{"path": f"data/derived/{n}", "sha256": sha256_file(DERIVED / n)} for n in INPUTS]
            + [{"path": f"pipeline/{n}", "sha256": sha256_file(Path(__file__).resolve().parent / n)} for n in CODE]}


# ================================================================== board T4d (model.t4d): unpublished time factor
def check_t4d(prm: dict) -> dict:
    """The chosen option meets clauses (1)-(3); every emission class in model.t4d.emissionClasses.list (the review's
    classes included) is removed by exactly one factor (r's classes are r's encoded removals, the rest belong to T) and
    none is left to the interim range; the classes left at the no-recurrence end are T's; the headline kind is
    'central' (publish T4d option d) or 'range' (keep the reviewed interim range)."""
    m, t4d = prm["model"], prm["model"]["t4d"]
    if not all(t4d["options"][t4d["choice"]]["meets"]):
        raise ValueError(f"model.t4d.choice {t4d['choice']!r} does not meet clauses (1)-(3)")
    if t4d["options"][t4d["choice"]].get("postResult"):
        raise ValueError(f"model.t4d.choice {t4d['choice']!r} was registered after a result was known")
    ec = t4d["emissionClasses"]
    by = ec["removedBy"]
    r_removes = set(m["classes"]["pairings"][m["range"]["low"]]["r"]["removes"])
    problems = [c for c in [*m["classes"]["list"], *ec["list"]] if c not in by]
    problems += [c for c, f in by.items() if f not in ("r", "T") or (f == "r") != (c in r_removes)]
    problems += [c for c in ec["atTheNoRecurrenceEnd"] if by.get(c) != "T"]
    if problems or ec["spannedByInterimRange"]:
        raise ValueError(f"emission classes not removed by exactly one factor of the T4d model: {problems} "
                         f"(spanned: {ec['spannedByInterimRange']})")
    kind = m["timeFactor"]["headlineKind"]
    if kind not in ("central", "range"):
        raise ValueError(f"model.timeFactor.headlineKind {kind!r} is neither 'central' nor 'range'")
    return {"choice": t4d["choice"], "emissionClasses": dict(by), "atTheNoRecurrenceEnd": ec["atTheNoRecurrenceEnd"],
            "headlineKind": kind}


def _selecting_dates(inp: dict) -> dict[str, set]:
    """Per portfolio member, the scene dates of the Carbon Mapper plumes that selected it (join_v1.json candidates)."""
    return {c["mars_source"]: {pl["scene_timestamp"][:10] for pl in c["plumes"]}
            for c in inp["join"]["candidates"]}


def _t4d(inp: dict, prm: dict, scope_facs: dict[str, list[dict]], scopes: dict, gwp: dict) -> dict:
    """Board T4d's time factor per scope, option d: S x T, T_i = min(1, sum_e r_e C_e / (D_i W_i)); the band from
    'with the measured re-onset hazard' (low, the central figure) to 'no recurrence' (high); every registered variant,
    the labelled post-result and post-hoc rows, and the diagnostics the review asked for."""
    import policy

    model = check_t4d(prm)
    p, t4d = prm["parameters"], prm["model"]["t4d"]
    nn = inp["cmNotMarsNotified"]
    rule = t4d["notMarsNotifiedSample"]
    if (nn["seed"], nn["size"], nn["selection"]) != (rule["seed"], rule["size"], rule["eligible"]):
        raise ValueError("cm_timelines_not_mars_notified.json was not drawn by the pre-registered rule: re-run "
                         "fetch_cm_not_mars_notified.py")
    nn_series = [(sp, ptime.sample_start(sp)) for sp in (ptime.sample_passes(tl) for tl in nn["sources"].values())
                 if sp]
    h_nat = ptime.natural_stop_hazard(nn_series, DAYS_PER_YEAR)
    usable = h_nat["sourcesWithEvents"] >= p["evidence"]["minSourcesPerGroup"] and h_nat["observedEnds"] > 0
    fac, inc = inp["facilities"], inp["carbSummary"]
    min_days, delays = ptime.min_event_days(prm), ptime.response_delays(inc, prm)
    carb = ptime.carb_first_plume_delays(inc, prm)
    rcls = ptime.r_by_plume_class(inc, prm)
    central, base = p["responseScenarios"]["central"], p["baselineResponseShare"]["value"]
    cw = policy.POLICY_V2_VERDICT_ONLY["core"]["challengeWindow"] / ptime.SEC_PER_DAY
    rev = t4d["reviewerReportOnly"]
    total_44 = _quote_share(prm, prm["model"]["reads"]["ravikumarTotal"])
    selecting = _selecting_dates(inp)
    out_scopes, sens, post_result, post, diag = {}, {}, {}, {}, {}
    for name, facs in scope_facs.items():
        q = [f for f in facs if f.get("evaluated") and f.get("stake", {}).get("status") == "quantified"]
        series = {f["name"]: fac["byName"][f["name"]] for f in q}
        evs = {n: ptime.censored_events(s) for n, s in series.items()}
        lags = {n: ptime.publication_lags(s) for n, s in series.items()}
        missing = [n for n in evs if any(e["f"] not in lags[n] for e in evs[n])]
        if missing:
            raise ValueError(f"T4d: detections without a published plume at {missing[:3]}")
        lps = {n: ptime.last_pass(s) for n, s in series.items()}
        lam = ptime.reonset_hazard([evs[n] for n in series], [lps[n] for n in series], DAYS_PER_YEAR)
        lam_merged = ptime.reonset_hazard([ptime.merge_single_misses(evs[n]) for n in series],
                                          [lps[n] for n in series], DAYS_PER_YEAR)
        emit = {f["name"]: f["stake"]["duty_cycle"] * (date.fromisoformat(lps[f["name"]])
                                                       - date.fromisoformat(f["start"])).days for f in q}
        ev_len = {n: sum(ptime.event_end(e, "central") - ptime.event_start_central(e) for e in evs[n]) for n in evs}

        def run(scale: float = 1, hazard: float | None = None, conv: str = "central", lag_add: float = 0,
                use_lag: bool = True, dl: list | None = None, rflat: float | None = None, r_minus: float = 0,
                r_minus_censored: float = 0, nat: float = 0, denom: dict | None = None) -> dict:
            hz = lam["perYear"] if hazard is None else hazard
            kt = cens = credit = marked = kt_ns = 0.0
            capped = 0
            for f in q:
                n = f["name"]
                r_of = {c: (rflat if rflat is not None else rcls[c]["share"]) * scale - r_minus for c in rcls}
                cr = ptime.facility_credit(series[n], evs[n], lags[n], dl or delays, r_of, hz, conv, DAYS_PER_YEAR,
                                           lag_add=lag_add, use_lag=use_lag, r_censored_minus=r_minus_censored,
                                           censored_stop_hazard=nat, mark_dates=selecting.get(n, set()))
                dn = (denom or emit)[n]
                ti = cr["credit"] / dn if dn > 0 else 0.0
                capped += ti > 1
                kt += f["stake"]["kt_ch4_per_yr"]["low"] * min(1.0, ti)
                kt_ns += f["stake"]["kt_ch4_per_yr"]["low"] * min(1.0, (cr["credit"] - cr["markedCredit"]) / dn
                                                                  if dn > 0 else 0.0)
                cens += cr["censoredCredit"]
                credit += cr["credit"]
                marked += cr["markedCredit"]
            return {"kt": round(kt, 2), "capped": capped, "ktNonSelecting": round(kt_ns, 2),
                    "censoredShareOfCredit": round(cens / credit, ptime.T_DIGITS) if credit else 0.0,
                    "selectingShareOfCredit": round(marked / credit, ptime.T_DIGITS) if credit else 0.0}

        led = scopes[name]["ledger"]
        rows = []
        for s in sorted(p["responseScenarios"]["values"]):
            lo, hi = run(scale=s / central), run(scale=s / central, hazard=0)
            kt = {"low": lo["kt"], "high": hi["kt"]}
            rows.append({"responseShare": s, "kt_ch4_per_yr": kt,
                         "mt_co2e_per_yr_gwp100": {b: round(v * gwp["gwp100"] / KT_PER_MT, 3) for b, v in kt.items()},
                         "mt_co2e_per_yr_gwp20": {b: round(v * gwp["gwp20"] / KT_PER_MT, 3) for b, v in kt.items()},
                         "ledgerUsdPerTco2eGwp100": {b: _sig4(led["usdPerYear"] / (v * T_PER_KT * gwp["gwp100"]))
                                                     for b, v in kt.items()},
                         "facilitiesAtCap": {"low": lo["capped"], "high": hi["capped"]},
                         "censoredShareOfCredit": {"low": lo["censoredShareOfCredit"],
                                                   "high": hi["censoredShareOfCredit"]}})
        base_run = run()
        out_scopes[name] = {"facilitiesQuantified": len(q), "reonsetHazard": lam,
                            "reonsetHazardSingleMissMerged": lam_merged, "scenarios": rows,
                            "naturalStopHazardOwnPasses": ptime.natural_stop_hazard(
                                [(series[n]["passes"], series[n]["start"]) for n in series], DAYS_PER_YEAR),
                            "events": sum(len(v) for v in evs.values()),
                            "censoredEvents": sum(e["censored"] for v in evs.values() for e in v),
                            "denominatorOverEventLength": round(sum(emit.values()) / sum(ev_len.values()), 2),
                            "selectingShareOfCredit": base_run["selectingShareOfCredit"],
                            # P1b: the same credit without the events containing a selecting plume, and both as a
                            # share of the scope's stake low bound (portfolios of different size compare)
                            "nonSelecting": {
                                "ktCh4PerYrCentral": base_run["ktNonSelecting"],
                                "centralShareOfStake": round(base_run["ktNonSelecting"]
                                                             / scopes[name]["stakeLowKtCh4PerYr"], ptime.T_DIGITS),
                                "allCreditKtCh4PerYrCentral": base_run["kt"],
                                "allCreditCentralShareOfStake": round(base_run["kt"] / scopes[name]["stakeLowKtCh4PerYr"],
                                                                      ptime.T_DIGITS),
                                "denominator": STAKE_DENOMINATOR}}
        stake = scopes[name]["stakeLowKtCh4PerYr"]

        def t4c_rev(hazard: float = 0, cap: float | None = None, censored: bool = False) -> float:
            kt = 0.0
            for f in q:
                n = f["name"]
                ev = evs[n] if censored else ptime.events(series[n]["passes"], series[n]["start"], fac["end"])
                w, length = ptime.t4c_variant(ev, lags[n], delays, min_days, hazard, cap, DAYS_PER_YEAR)
                kt += f["stake"]["kt_ch4_per_yr"]["low"] * central * (w / length if length > 0 else 0)
            return round(kt, 2)

        lag_med = statistics.median(lags[n][e["f"]] for n in evs for e in evs[n])
        rows_s = {
            "d": base_run["kt"], "d_norec": run(hazard=0)["kt"], "d_shortest": run(conv="shortest")["kt"],
            "d_longest": run(conv="longest")["kt"], "d_rflat": run(rflat=central)["kt"],
            "d_lag0": run(use_lag=False)["kt"], "d_lagcw": run(lag_add=cw)["kt"],
            "d_carb": run(dl=carb, use_lag=False)["kt"], "d_rminusb": run(r_minus=base)["kt"],
            "d_bcensored": run(r_minus_censored=base)["kt"],
            "t_nmn": round(stake * _t_nmn(nn, delays, rcls, base, lam["perYear"], lag_med), 2) if usable else None,
            "rev_t4c_censored": t4c_rev(censored=True), "rev_t4c_hazard": t4c_rev(hazard=rev["hazardPerYear"]),
            "rev_t4c_cap365": t4c_rev(cap=rev["capDays"][0]), "rev_t4c_cap182": t4c_rev(cap=rev["capDays"][1]),
            "c": round((central - base) * p["ceasedShare"]["low"] * stake, 2),
            "b": round((central - base) * p["ceasedShare"]["high"] * stake, 2),
            "a": round((_answered_share(p) - base) * total_44 * stake, 2),
            "t4": round((central - base) * total_44 * stake, 2),
        }
        rows_pr = {
            "e": run(nat=h_nat["perYear"])["kt"] if usable else None,
            "e_norec": run(nat=h_nat["perYear"], hazard=0)["kt"] if usable else None,
            "d_lambda_singlemiss": run(hazard=lam_merged["perYear"])["kt"],
            "d_eventdenom": run(denom=ev_len)["kt"],
        }
        for sid in t4d["sensitivity"]:
            sens.setdefault(sid, {})[name] = rows_s[sid]
        for sid in t4d["postResultSensitivity"]:
            post_result.setdefault(sid, {})[name] = rows_pr[sid]
        for ph in t4d["postHoc"]:  # labelled post-hoc rows (model.t4d.postHoc): they set no value
            post.setdefault(ph["id"], {})[name] = run(hazard=ph["restarts"] / ph["eventFreeYears"])["kt"]
        diag[name] = {"lagDays": ptime.quantiles([lags[n][e["f"]] for n in evs for e in evs[n]]),
                      "eventsPerFacility": round(sum(len(v) for v in evs.values()) / len(q), 3)}

    def table(ids: list[str], values: dict) -> list[dict]:
        out = []
        for sid in ids:
            opt = t4d["options"][sid]
            row = {"id": sid, "model": opt["model"], "meets": opt["meets"], "variantOf": opt.get("variantOf"),
                   "postResult": bool(opt.get("postResult")), "isChoice": sid == t4d["choice"]}
            if values[sid]["portfolio_v3"] is None:
                row |= {"computable": False, "reason": NN_UNUSABLE}
            else:
                row |= {"computable": True, "ktCh4PerYrCentralAction": values[sid]}
            out.append(row)
        return out

    pfq = [f for f in scope_facs["portfolio_v3"] if f.get("evaluated") and f.get("stake", {}).get("status") == "quantified"]
    pop = rule["population"]
    srcs = list(nn["sources"].values())
    box = pop["usBox"]
    in_box = sum(1 for v in srcs if v["lon"] is not None and box["lonMin"] < v["lon"] < box["lonMax"]
                 and box["latMin"] < v["lat"] < box["latMax"])
    obs = [i for v in srcs for o in v["obs"] for i in o[1].split("+")]
    return {"model": model, "rByPlumeClass": rcls, "responseDelaysDays": ptime.quantiles(delays),
            "carbFirstPlumeToMitigationDays": ptime.quantiles(carb), "challengeWindowDays": cw,
            "minEventMinutes": round(min_days * ptime.MIN_PER_DAY, 1), "scopes": out_scopes,
            "notMarsNotifiedSample": h_nat | {"usable": usable, "seed": nn["seed"], "size": nn["size"],
                                              "eligibleCount": nn["eligibleCount"], "fetchedAt": nn["fetchedAt"],
                                              "usBoxShareOfSources": round(in_box / len(srcs), ptime.T_DIGITS),
                                              "airborneShareOfObservations": round(
                                                  sum(i in pop["airborneInstruments"] for i in obs) / len(obs),
                                                  ptime.T_DIGITS),
                                              "population": pop["note"]},
            "sensitivity": table(t4d["sensitivity"], sens),
            "postResultSensitivity": table(t4d["postResultSensitivity"], post_result),
            "postHocSensitivity": [{"id": ph["id"], "note": ph["note"],
                                    "hazardPerYear": round(ph["restarts"] / ph["eventFreeYears"], ptime.T_DIGITS),
                                    "ktCh4PerYrCentralAction": post[ph["id"]]} for ph in t4d["postHoc"]],
            "diagnostics": diag | {"topCountries": _top_countries(pfq)},
            "headline": {"kind": prm["model"]["timeFactor"]["headlineKind"],
                         "central": out_scopes["portfolio_v3"]["scenarios"][1]["kt_ch4_per_yr"]["low"],
                         "upToNoRecurrence": out_scopes["portfolio_v3"]["scenarios"][1]["kt_ch4_per_yr"]["high"]}}


NN_UNUSABLE = ("not computable: the not MARS-notified sample has fewer than evidence.minSourcesPerGroup sources with an "
               "event or no observed event end (model.t4d.notMarsNotifiedSample.unusable)")


def _t_nmn(nn: dict, delays: list, rcls: dict, base: float, hazard: float, lag: float) -> float:
    """Option t_nmn's time factor: T4d's credit (r - b by plume class, the stake's re-onset hazard, central convention,
    censored at the last observation, the portfolio's median publication lag) pooled over the not MARS-notified sample,
    over its emitting days (detected / observed dates x days from the first to the last observation)."""
    r_of = {c: v["share"] - base for c, v in rcls.items()}
    credit = emitting = 0.0
    for name, tl in nn["sources"].items():
        sp = ptime.sample_passes(tl)
        if not sp:
            continue
        src = {"name": name, "passes": sp, "start": ptime.sample_start(sp)}
        evs = ptime.censored_events(src)
        duty = sum(x["detected"] for x in sp) / len(sp)
        days = date.fromisoformat(sp[-1]["date"]).toordinal() - date.fromisoformat(sp[0]["date"]).toordinal()
        credit += ptime.facility_credit(src, evs, {}, delays, r_of, hazard, "central", DAYS_PER_YEAR,
                                        fixed_lag=lag)["credit"]
        emitting += duty * days
    return min(1.0, credit / emitting) if emitting else 0.0


def _answered_share(p: dict) -> float:
    rows = p["responseScenarios"]["rule"]["rows"]
    sent = sum(r["sent"] for r in rows)
    return round((sent - sum(r["didNotRespond"] for r in rows)) / sent, 2)


def _top_countries(q: list[dict]) -> dict:
    by: dict[str, float] = {}
    for f in q:
        by[f["country"]] = by.get(f["country"], 0) + f["stake"]["kt_ch4_per_yr"]["low"]
    total = sum(by.values())
    top = sorted(by.items(), key=lambda kv: -kv[1])[:2]
    return {"countries": [c for c, _ in top], "stakeShare": round(sum(v for _, v in top) / total, 4)}


def _t4d_scopes(scopes: dict, t4d: dict) -> dict:
    """The published scopes under headline kind 'central': the interim scope fields with option d's scenarios."""
    return {n: {k: v for k, v in s.items() if k != "scenarios"} | {"scenarios": t4d["scopes"][n]["scenarios"]}
            for n, s in scopes.items()}


def _t4d_cards(scopes: dict, p: dict, selection: str, evidence_sentence: str, rule: dict, t4d: dict) -> dict:
    """The sentences option d publishes under headline kind 'central' (vision first: [PROJECTION] once at the
    headline; the ASSUMPTIONs, the null of our own checks and 'not a measured reduction' once, in the methods). The
    central figure is the measured-hazard end; the no-recurrence end follows it."""
    pf, bs = scopes["portfolio_v3"], scopes["backtest_set"]
    rs, central = p["responseScenarios"], p["responseScenarios"]["central"]
    cr, br = _row(pf, central), _row(bs, central)
    top = max(bs["scenarios"], key=lambda r: r["responseShare"])
    kt, led = cr["kt_ch4_per_yr"], pf["ledger"]
    lam, sc4 = t4d["scopes"]["portfolio_v3"]["reonsetHazard"], t4d["scopes"]["portfolio_v3"]
    diag, tops = t4d["diagnostics"], t4d["diagnostics"]["topCountries"]
    scen = ", ".join(_pct(v) + (" central" if v == central else "") for v in sorted(rs["values"]))
    cls = "; ".join(f"{_pct(v['share'])} at {'3 or more' if k == '3+' else k} plume{'' if k == '1' else 's'}, "
                    f"n = {v['n']}" for k, v in t4d["rByPlumeClass"].items())
    costs = [v for r in pf["scenarios"] for v in r["ledgerUsdPerTco2eGwp100"].values()]

    def of_stake(s: dict) -> str:
        return f"of the stake's low bound of {_kt(s['stakeLowKtCh4PerYr'])} (stake, not abatement)"

    def ck(d: dict) -> str:
        return f"{_kt(d['low'])} CH₄/yr (up to {_kt(d['high'])} with no recurrence)"

    acted = (f"their sellers acted on {_pct(central)} of attested episodes (ASSUMPTION, informed by {FIX}: "
             f"{_carb(rule)})")
    time = ("a fix were assumed to stop its fault until the fault's observed end, the facility's last pass or a new "
            "fault (ASSUMPTION: events on the stake's own passes by Cusworth et al. 2026's definition, California's "
            "response delays; not measured after a satellite notification)")
    return {
        "headlineSentence": (
            # the reviewer's sentence, words shortened (never a clause dropped) to keep README.md within 25,000 bytes
            "[PROJECTION] The clause makes each attested super-emitter a priced event the seller pays for and earns "
            f"back by staying clean: one {pf['facilities']}-facility portfolio covenant [SIM] ({HEADLINE_SELECTION}, "
            "credited over each member's window from MARS's first plume there as if the covenant began then; stake "
            f"quantified at {pf['facilitiesQuantified']}) whose sellers act on episodes would stop {_kt(kt['low'])} "
            f"CH₄/yr at the central {_pct(central)} action share (up to {_kt(kt['high'])} with no recurrence), "
            f"{_mt(cr['mt_co2e_per_yr_gwp100']['low'])} CO₂e/yr at GWP-100, each fix credited until the fault's "
            f"observed end, the last pass or a new fault — {HEADLINE_END}"),
        "scaleSentence": (
            f"Scaled to the backtest set ({bs['facilitiesQuantified']:,} facilities with a quantified stake, of "
            f"{selection}), the same arithmetic gives {_kt(br['kt_ch4_per_yr']['low'])} CH₄ a year (up to "
            f"{_kt(br['kt_ch4_per_yr']['high'])} with no recurrence; {_mt(br['mt_co2e_per_yr_gwp100']['low'])} CO₂e/yr "
            f"at GWP-100, {_mt(br['mt_co2e_per_yr_gwp20']['low'])} at GWP-20), rising to "
            f"{_kt(top['kt_ch4_per_yr']['low'])} (up to {_kt(top['kt_ch4_per_yr']['high'])}) at the top of the "
            f"interval for the share of episodes sellers act on ({_pct(top['responseShare'])})."),
        "floorLineSentence": (
            f"Stake, not abatement: the clause puts a price on at least {_kt(pf['stakeLowKtCh4PerYr'])} CH₄ a year "
            f"across the {pf['facilitiesQuantified']} of the portfolio's {pf['facilities']} facilities with a "
            "quantified stake [SIM]."),
        "costLineSentence": (
            f"The ledger is cheap: USD {led['usdPerEpisode']:.2f} of Ethereum Sepolia calls per episode, "
            f"USD {led['usdPerYear']:,.0f} a year for the portfolio, "
            f"{_usd_small(cr['ledgerUsdPerTco2eGwp100']['low'])} per t CO₂e projected at the central action share: "
            "the cost of the records, not the work on site."),
        "methodsSentence": (
            "How we estimated this. Projected tonnes = for each facility with a quantified stake, the stake's low "
            "bound × the fixed share of its emitting time. "
            f"The action share is an ASSUMPTION ({scen}) informed by {FIX}: {_carb(rule)}; the scenarios are that "
            f"share and its {rule['confidence'] * PERCENT:.0f} % sampling interval ({rule['of']} California "
            "incidences), which does not cover the move from a regulator's notice to a rebate under a contract; per "
            f"event it is CARB's Type A share by persistence ({cls}), counted by incidence, as CARB publishes no "
            "emission rate per incidence. "
            "The time share is an ASSUMPTION built from the stake's own satellite passes: following Cusworth et al. "
            "2026, a detection after a non-detection starts an event and a non-detection after a detection ends it; a "
            "fixable event is credited from its fix (its first detection plus Carbon Mapper's publication lag, median "
            f"{diag['portfolio_v3']['lagDays']['median']:.0f} days, plus the seller's response, every California Type "
            f"A notification-to-mitigation delay, median {t4d['responseDelaysDays']['median']:.0f} days) until the "
            "fault's observed end or the facility's last pass, weighted by the chance that no new fault has started "
            f"(re-onset hazard {lam['perYear']:.2f} a year: {lam['restarts']} restarts over {lam['eventFreeYears']} "
            "event-free facility-years on the stake's own passes; the bias runs both ways: sparse passes miss "
            "restarts, and a single missed detection of a persistent source creates a false one); time before the "
            "fix, after the last pass and after a new fault is never credited. The credited days are divided by the "
            "facility's emitting days as the stake counts them (duty cycle × days to its last pass), "
            f"{sc4['denominatorOverEventLength']:.2f} times the summed event lengths, so the time share errs low. "
            "Each member is credited over its window since MARS's first plume there, as if the covenant had started "
            f"then: {sc4['selectingShareOfCredit'] * PERCENT:.0f} % of the credit is for events containing the plume "
            "that selected the member, and a forward hold-out (members chosen before a date, credit only after it) "
            "is future work. The action share and the response delay are California's, under a regulator; "
            f"{tops['stakeShare'] * PERCENT:.0f} % of the portfolio's stake is in {tops['countries'][0]} and "
            f"{tops['countries'][1]}, where a buyer, not a regulator, would ask. The stake assumes what satellites "
            "saw on their passes holds between them. "
            f"{evidence_sentence} These figures are a {PROJ}."),
        "projectionSentence": (
            f"[PROJECTION] A scenario, not a measurement: if importers covering the {pf['facilities']}-facility "
            f"portfolio [SIM] wrote the clause, {acted}, and {time}, {ck(kt)} {of_stake(pf)} would stop, "
            f"{_mt(cr['mt_co2e_per_yr_gwp100']['low'])} CO₂e/yr at GWP-100 ({_mt(cr['mt_co2e_per_yr_gwp20']['low'])} "
            f"at GWP-20) — a {PROJ}."),
        "scenariosSentence": (
            f"[PROJECTION] By action scenario (ASSUMPTION), portfolio [SIM], {of_stake(pf)}: "
            + ", ".join(f"{_pct(r['responseShare'])} acted on → {ck(r['kt_ch4_per_yr'])}" for r in pf["scenarios"])
            + f"; every value is in data/derived/projection.json — a {PROJ}."),
        "widerSetSentence": (
            f"[PROJECTION] Over all {bs['facilitiesQuantified']} facilities with a quantified stake (of {selection}), "
            f"the same assumptions (ASSUMPTION) give {ck(br['kt_ch4_per_yr'])} at the central action share "
            f"{of_stake(bs)}, {_mt(br['mt_co2e_per_yr_gwp100']['low'])} CO₂e/yr at GWP-100 — a {PROJ}."),
        "costSentence": (
            f"Ledger cost only, not the cost of the work on site: one episode costs USD {led['usdPerEpisode']:.2f} on "
            f"Ethereum Sepolia ({' + '.join(led['calls']['perEpisode'])} at the stated {led['statedGwei']:g} gwei and "
            f"ETH/USD {led['ethUsd']:,.0f}, measured {led['measuredAt'].split('T')[0]}); at the portfolio's backtest "
            f"rate of {led['episodesPerYear']:.0f} episodes a year plus one createCovenant that is "
            f"USD {led['usdPerYear']:,.0f} a year, or {_usd_small(cr['ledgerUsdPerTco2eGwp100']['low'])} per t CO₂e "
            f"projected at the central action share ({_rusd(costs)} across scenarios and with no recurrence). It "
            "excludes the refund-side calls and the cost of posting the bond."),
        "adoptionSentence": (
            f"The scenario assumes importers covering the {pf['facilities']}-facility portfolio [SIM] write the clause "
            "into their supply contracts, their sellers post the bond and act on attested episodes at the scenario's "
            "rate, the stake's low bound holds, and a fixed fault stays stopped until the fault's observed end, the "
            "facility's last pass or a new fault; it assumes no new satellite and counts no clean pass as a stop."),
        "limitSentence": (
            "The carbon figures are a projection resting on two ASSUMPTIONs, the action share and the time share, "
            "which our own checks neither support nor bound; section 4, 'How we estimated this', states both and the "
            "checks."),
    }


def _ci(ci: list[float], fmt: str) -> str:
    return f"{ci[0]:{fmt}} to {ci[1]:{fmt}}"


def _evidence_cards(evd: dict, p: dict) -> dict:
    """The evidence sentence (spec-v2 §15, projection review: exact wording, numbers generated) and its two details.
    Refuses to write "no difference" unless every interval of both lines includes zero."""
    l1, l2 = evd["line1"], evd["line2"]
    a, j = l2["coverageSets"]["allNotified"], l2["coverageSets"]["joinNeighbourhood"]
    pa, pj = a["primary"], j["primary"]
    d1 = l1["difference"]["ci"]
    if not (d1[0] <= 0 <= d1[1] and a["allCiIncludeZero"] and j["allCiIncludeZero"]):
        raise ValueError("the evidence lines are no longer a null: the evidence sentence needs a new review")
    y, n = l1["byFeedback"]["Yes"], l1["byFeedback"]["No"]
    gy, gn = pa["groups"]["Yes"], pa["groups"]["No"]
    used = gy["sources"] + gn["sources"]
    agree = gy["proxyDatesAgree"] + gn["proxyDatesAgree"]
    conf = f"{l1['confidence'] * PERCENT:.0f} %"
    mde_share = _pct(pa["did"]["minimumDetectableShareOfPreRate"])
    runs = len(a["variants"]) + 1
    return {
        "evidenceSentence": (
            f"Our two checks (MARS 'absent' label by feedback; Carbon Mapper detection rates around {used:,} notified "
            "sources) find no difference between answered and unanswered sources; the design cannot detect a drop "
            f"smaller than {mde_share} of the detection rate and cannot see a partial mass cut at all, so they neither "
            "support nor bound this band."),
        "line1Sentence": (
            "MARS outcome by response: MARS documents a per-source emission_status, but neither its committed nor its "
            "current public file carries it, so the outcome is the 'absent' label (no MARS detection over the last six "
            f"months; a non-detection label, not a stop): {y['absent']} of {y['determined']} notified oil and gas "
            f"sources with feedback = Yes (operator or government) against {n['absent']:,} of {n['determined']:,} with "
            f"feedback = No, a difference of {l1['difference']['yesMinusNo'] * PERCENT:+.1f} points ({conf} CI "
            f"{_ci([v * PERCENT for v in d1], '+.1f')}); "
            f"{l1['mantelHaenszelDifference']['yesMinusNo'] * PERCENT:+.1f} within plume-count strata; "
            f"{y['undeterminedLeftOut']} and {n['undeterminedLeftOut']} sources with an undetermined label are left "
            "out."),
        "line2Sentence": (
            "Event study: Carbon Mapper per-pass detection rates in the year before and after each source's first "
            "notified MARS plume (a proxy date: MARS publishes no notification date; it is also the first plume for "
            f"{agree} of the {used} sources used), feedback = Yes (operator or government) minus No: "
            f"{pa['did']['yesMinusNo']:+.3f} ({conf} CI {_ci(pa['did']['ci'], '+.3f')}) over {gy['sources']} and "
            f"{gn['sources']} sources, {pj['did']['yesMinusNo']:+.3f} ({_ci(pj['did']['ci'], '+.3f')}) on the committed "
            f"set conditioned on today's 'absent' label; every interval of the {runs} specifications on both sets "
            f"includes zero; minimum detectable effect {pa['did']['minimumDetectableEffect']:.3f} ({mde_share} of the "
            f"responders' starting rate). T0 precedes MARS's feedback tracking (November 2024) for "
            f"{gy['t0BeforeFeedbackTracking']} of {gy['sources']} responders and {gn['t0BeforeFeedbackTracking']} of "
            f"{gn['sources']} non-responders; the post window runs past the catalogue end for "
            f"{gy['postWindowTruncated']} and {gn['postWindowTruncated']}; responders start from a higher rate, so "
            "regression to the mean pushes toward a drop; both groups' rates rise after T0, as tasking toward expected "
            "plumes would make them."),
        "limitSentence": (
            "The carbon figures are a projection resting on two ASSUMPTIONs, the action share and the stop share, which "
            "our own checks neither support nor bound; section 4, 'How we estimated this', states both and the checks."),
    }


def _all_cards(scopes: dict, p: dict, gwp: dict, neg: dict, selection: str, evd: dict, rule: dict) -> dict:
    cards = {**_cards(scopes, p, gwp, neg, selection, rule), **_evidence_cards(evd, p)}
    return {**cards, **_vision_cards(scopes, p, selection, cards["evidenceSentence"], rule)}


def _carb(rule: dict) -> str:
    return (f"when California's air regulator sent operators airborne plume detections and asked them to find the "
            f"source, repair it if possible and report, operators answered {rule['answered']} of {rule['of']} oil and "
            f"gas incidences; {rule['acted']} were faults unknown to the operator, and every one was stopped or "
            f"repaired (CARB 2024)")


def _rkt(d: dict) -> str:
    return f"~{d['low']:,.0f}–{_kt(d['high'])}"


def _rmt(d: dict) -> str:
    return f"~{d['low']:.2f}–{d['high']:.2f} Mt"


def _rusd(costs: list[float]) -> str:
    return f"{_usd_small(min(costs))}–{_usd_small(max(costs)).removeprefix('USD ')}"


def _vision_cards(scopes: dict, p: dict, selection: str, evidence_sentence: str, rule: dict) -> dict:
    """Vision first (user decision 2026-10-16): the headline says what the clause is designed to achieve, labelled
    [PROJECTION] once; scale, floor and cost follow in plain words; the ASSUMPTIONs, the null of our own checks and
    "not a measured reduction" are said once, in the methods paragraph. The headline ends with a pointer to the
    paper's section 4. Interim (board T4b review, 2026-10-21): every tonnage is the range between the two ends of the
    stop share at the central action share; there is no central tonnage until board T4c models time. Every number is
    read from the scopes."""
    pf, bs = scopes["portfolio_v3"], scopes["backtest_set"]
    base, c, rs = p["baselineResponseShare"]["value"], p["ceasedShare"], p["responseScenarios"]
    central = rs["central"]
    cr, br = _row(pf, central), _row(bs, central)
    top = max(bs["scenarios"], key=lambda r: r["responseShare"])
    led = pf["ledger"]
    scen = ", ".join(_pct(v) + (" central" if v == central else "") for v in sorted(rs["values"]))
    return {
        "headlineSentence": (
            "[PROJECTION] The clause turns each attested super-emitter into a priced event the seller pays for "
            f"and can earn back by staying clean: one {pf['facilities']}-facility portfolio covenant [SIM] "
            f"({PORTFOLIO_SELECTION}; stake quantified at {pf['facilitiesQuantified']}: "
            f"{pf['facilities'] - pf['facilitiesEvaluated']} own no Carbon Mapper source, "
            f"{pf['facilitiesEvaluated'] - pf['facilitiesQuantified']} no quantified plume in the window) whose "
            f"sellers act on episodes would stop {_rkt(cr['kt_ch4_per_yr'])} CH₄/yr at the central "
            f"{_pct(central)} action share, from one repair round net of new faults (Ravikumar 2020: "
            f"{_pct(c['low'])}) to every fix holding all year; {_rmt(cr['mt_co2e_per_yr_gwp100'])} CO₂e/yr at "
            f"GWP-100 — {HEADLINE_END}"),
        "scaleSentence": (
            f"Scaled to the backtest set ({bs['facilitiesQuantified']:,} facilities with a quantified stake, of "
            f"{selection}), the same arithmetic gives {_rkt(br['kt_ch4_per_yr'])} CH₄ a year "
            f"({_rmt(br['mt_co2e_per_yr_gwp100'])} CO₂e/yr at GWP-100, {_rmt(br['mt_co2e_per_yr_gwp20'])} at GWP-20), "
            f"rising to {_rkt(top['kt_ch4_per_yr'])} ({_rmt(top['mt_co2e_per_yr_gwp100'])} CO₂e/yr at GWP-100) at "
            "the top of the interval for the share of episodes sellers act on "
            f"({_pct(top['responseShare'])})."),
        "floorLineSentence": (
            f"Stake, not abatement: the clause puts a price on at least {_kt(pf['stakeLowKtCh4PerYr'])} CH₄ a year "
            f"across the {pf['facilitiesQuantified']} of the portfolio's {pf['facilities']} facilities with a "
            "quantified stake [SIM]."),
        "costLineSentence": (
            f"The ledger is cheap: USD {led['usdPerEpisode']:.2f} of Ethereum Sepolia calls per episode, "
            f"USD {led['usdPerYear']:,.0f} a year for the portfolio, "
            f"{_rusd(list(cr['ledgerUsdPerTco2eGwp100'].values()))} per t CO₂e projected at the central action share: "
            "the cost of the records, not the work on site."),
        "methodsSentence": (
            "How we estimated this. Projected tonnes = the stake's low bound × the share of attested episodes sellers "
            "act on, each a fixable fault fixed, above today's "
            f"{_pct(base)} reply rate (a reply, not an action) to MARS alerts ({rule['baselineCite']}), which bounds "
            "the fixes replies already drive, × the share of a fixed source's yearly emissions that stops. "
            f"The action share is an ASSUMPTION ({scen}) informed by {FIX}: {_carb(rule)}; the scenarios are that "
            f"share and its {rule['confidence'] * PERCENT:.0f} % sampling interval ({rule['of']} California "
            "incidences), which does not cover the move from a regulator's notice to a rebate under a contract; it "
            "counts incidences, as CARB publishes no emission rate per incidence. "
            "The stop share is an ASSUMPTION with two ends from Ravikumar et al. 2020's re-survey of repaired leaks at "
            f"Alberta facilities 6 to 13 months later: {_pct(c['low'])} at the low end, the fall in leak emissions "
            f"after one repair round net of new leaks; {_pct(c['high'])} at the high end, the share of the found "
            "leaks' emissions that was fixed (a repair rate), with fixed leaks not re-appearing, read as every fix "
            "holding all year; neither end was measured after a satellite notification. Time is not yet modelled: "
            "the delay from detection to repair, how long an event lasts, new faults and sources that stop on their "
            "own are not separated, and the range spans them, so there is no central tonnage. The stake assumes what "
            "satellites saw on their passes holds between them. "
            f"{evidence_sentence} These figures are a {PROJ}."),
    }


def _row(scope: dict, r: float) -> dict:
    return next(x for x in scope["scenarios"] if x["responseShare"] == r)


def _cards(scopes: dict, p: dict, gwp: dict, neg: dict, selection: str, rule: dict) -> dict:
    pf, bs = scopes["portfolio_v3"], scopes["backtest_set"]
    base, c, central = p["baselineResponseShare"]["value"], p["ceasedShare"], p["responseScenarios"]["central"]
    cr, br = _row(pf, central), _row(bs, central)
    stop = (f"a fixed source were assumed to stop {_pct(c['low'])}–{_pct(c['high'])} of its yearly emissions "
            "(ASSUMPTION, two ends informed by Ravikumar et al. 2020's re-survey of repaired leaks: one repair round "
            "net of new faults, every fix holding all year; neither measured after a satellite notification)")
    acted = (f"their sellers acted on {_pct(central)} of attested episodes (ASSUMPTION, informed by {FIX}: "
             f"{_carb(rule)}; today's MARS reply rate is {_pct(base)}, {rule['baselineCite']})")

    def of_stake(s: dict) -> str:
        return f"of the stake's low bound of {_kt(s['stakeLowKtCh4PerYr'])} (stake, not abatement)"

    led = pf["ledger"]
    costs = [v for r in pf["scenarios"] for v in r["ledgerUsdPerTco2eGwp100"].values()]
    return {
        "floorSentence": (
            f"Stake, not abatement: at least {_kt(pf['stakeLowKtCh4PerYr'])} CH₄/yr across the portfolio's "
            f"{pf['facilitiesQuantified']} quantified facilities [SIM] ({PORTFOLIO_SELECTION}) and "
            f"{_kt(bs['stakeLowKtCh4PerYr'])} across {bs['facilitiesQuantified']} (of {selection}), assuming what "
            "satellites saw on their passes holds between them; it is the methane the clause puts a price on, not a "
            "change in emissions."),
        "projectionSentence": (
            f"[PROJECTION] A scenario, not a measurement: if importers covering the {pf['facilities']}-facility "
            f"portfolio [SIM] wrote the clause, {acted}, and {stop}, {_rkt(cr['kt_ch4_per_yr'])} CH₄/yr "
            f"{of_stake(pf)} would stop, {_rmt(cr['mt_co2e_per_yr_gwp100'])} CO₂e/yr at GWP-100 "
            f"({_rmt(cr['mt_co2e_per_yr_gwp20'])} at GWP-20), with no central value until time is modelled, counting "
            f"only action above the {_pct(base)} reply rate — a {PROJ}."),
        "scenariosSentence": (
            f"[PROJECTION] By action scenario (ASSUMPTION), portfolio [SIM], over the two ends of the stop share, "
            f"{of_stake(pf)}: "
            + ", ".join(f"{_pct(r['responseShare'])} acted on → {_rkt(r['kt_ch4_per_yr'])} CH₄/yr "
                        f"({_rmt(r['mt_co2e_per_yr_gwp100'])} CO₂e at GWP-100)" for r in pf["scenarios"])
            + f"; every edge is in data/derived/projection.json — a {PROJ}."),
        "widerSetSentence": (
            f"[PROJECTION] Over all {bs['facilitiesQuantified']} facilities with a quantified stake (of {selection}), "
            f"the same assumptions (ASSUMPTION) give {_rkt(br['kt_ch4_per_yr'])} CH₄/yr at the central action share "
            f"{of_stake(bs)}, {_rmt(br['mt_co2e_per_yr_gwp100'])} CO₂e/yr at GWP-100 — a {PROJ}."),
        "costSentence": (
            f"Ledger cost only, not the cost of the work on site: one episode costs USD {led['usdPerEpisode']:.2f} on "
            f"Ethereum Sepolia ({' + '.join(led['calls']['perEpisode'])} at the stated {led['statedGwei']:g} gwei and "
            f"ETH/USD {led['ethUsd']:,.0f}, measured {led['measuredAt'].split('T')[0]}); at the portfolio's backtest "
            f"rate of {led['episodesPerYear']:.0f} episodes a year plus one createCovenant that is "
            f"USD {led['usdPerYear']:,.0f} a year, or {_rusd(list(cr['ledgerUsdPerTco2eGwp100'].values()))} per t "
            f"CO₂e projected at the central action share ({_rusd(costs)} across scenarios and the two ends of the "
            "stop share). It excludes the refund-side calls and the cost of posting the bond."),
        "adoptionSentence": (
            f"The scenario assumes importers covering the {pf['facilities']}-facility portfolio [SIM] write the clause "
            "into their supply contracts, their sellers post the bond and act on attested episodes at the scenario's "
            "rate, the stake's low bound holds, and a fixed source stops a share of its yearly emissions between the "
            "two ends of the stop share (the time from detection to repair, new faults and sources that stop on "
            "their own are spanned by the range, not modelled); it assumes no new satellite and counts no clean pass "
            "as a stop."),
        "negativeFindingSentence": neg["sentence"],
    }


# ===================================================== board B1: the forward hold-out (projection_time.FORWARD)
def _forward_scope(inp: dict, prm: dict, bt_by: dict, gwp: dict, members: dict[str, set], credit_end: str,
                   stake_from: str = "atD", published_by_d: bool = False, bootstrap: dict | None = None,
                   origin: str | None = None) -> dict:
    """T4d option d on members chosen at D, credited only for events whose first detection is after D, up to
    credit_end (projection_time.FORWARD). stake_from 'atD' = the stake known at D; 'backtest' = backtest.json's.
    published_by_d (FORWARD_POST_RESULT): drop the detections dated up to D that were published only on or after D.
    bootstrap (FORWARD_POST_RESULT.bootstrap): the design's draws, seed and confidence -> out['bootstrap']."""
    from stake import band_from

    fw, p = ptime.FORWARD, prm["parameters"]
    d0 = origin or fw["D"]  # origin: FORWARD_ROLLING's other D dates
    fac, inc = inp["facilities"]["byName"], inp["carbSummary"]
    delays, rcls = ptime.response_delays(inc, prm), ptime.r_by_plume_class(inc, prm)
    central = p["responseScenarios"]["central"]
    pre_evs, pre_lps, rows, no_pass_after, quantified, parts = [], [], {}, [], {}, {}
    dropped = 0
    for n in sorted(members):
        s = fac[n]
        if published_by_d:
            s, late = ptime.without_late_published(s, d0)
            dropped += len(late)
        pre = ptime.truncated(s, d0)
        if ptime.has_pass_after_start(pre):
            pre_evs.append(ptime.censored_events(pre))
            pre_lps.append(ptime.last_pass(pre))
            parts[n] = ptime.reonset_parts(pre_evs[-1], pre_lps[-1])
        if stake_from == "atD":
            after = [x for x in pre["passes"] if x["date"] > s["start"]]
            st = band_from(len(after), sum(x["detected"] for x in after),
                           [r for day, r in s["rates"] if s["start"] < day <= d0])
        else:
            st = bt_by[n]["stake"]
        post = ptime.truncated(s, credit_end)
        after_d = [x["date"] for x in post["passes"] if x["date"] > d0]
        if not after_d:
            no_pass_after.append(n)
        if st.get("status") == "quantified":
            quantified[n] = st["kt_ch4_per_yr"]["low"]
        if st.get("status") != "quantified" or not after_d:
            continue
        evs = [e for e in ptime.censored_events(post) if e["f"] > d0]
        lags = ptime.publication_lags(s)
        if any(e["f"] not in lags for e in evs):
            raise ValueError(f"forward: detections without a published plume at {n}")
        window = date.fromisoformat(max(after_d)).toordinal() - date.fromisoformat(d0).toordinal()
        rows[n] = {"post": post, "evs": evs, "lags": lags, "stake": st, "emit": st["duty_cycle"] * window,
                   "windowDays": window}
    lam = ptime.reonset_hazard(pre_evs, pre_lps, DAYS_PER_YEAR)
    if lam["perYear"] is None:
        raise ValueError("forward: the re-onset hazard on the members' passes up to D is undefined (FORWARD.hazard)")

    def run(scale: float, hazard: float) -> dict:
        kt = credit = marked = cens = 0.0
        capped = 0
        for n, r in rows.items():
            r_of = {c: rcls[c]["share"] * scale for c in rcls}
            cr = ptime.facility_credit(r["post"], r["evs"], r["lags"], delays, r_of, hazard, "central", DAYS_PER_YEAR,
                                       mark_dates=members[n])
            ti = cr["credit"] / r["emit"] if r["emit"] > 0 else 0.0
            capped += ti > 1
            kt += r["stake"]["kt_ch4_per_yr"]["low"] * min(1.0, ti)
            credit += cr["credit"]
            marked += cr["markedCredit"]
            cens += cr["censoredCredit"]
        return {"kt": round(kt, 2), "capped": capped,
                "selecting": round(marked / credit, ptime.T_DIGITS) if credit else 0.0,
                "censored": round(cens / credit, ptime.T_DIGITS) if credit else 0.0}

    scen = []
    stake_cr = round(sum(r["stake"]["kt_ch4_per_yr"]["low"] for r in rows.values()), 2)
    for s in sorted(p["responseScenarios"]["values"]):
        lo, hi = run(s / central, lam["perYear"]), run(s / central, 0)
        kt = {"low": lo["kt"], "high": hi["kt"]}
        scen.append({"responseShare": s, "kt_ch4_per_yr": kt,
                     "mt_co2e_per_yr_gwp100": {b: round(v * gwp["gwp100"] / KT_PER_MT, 3) for b, v in kt.items()},
                     "mt_co2e_per_yr_gwp20": {b: round(v * gwp["gwp20"] / KT_PER_MT, 3) for b, v in kt.items()},
                     "shareOfStake": {b: round(v / stake_cr, ptime.T_DIGITS) if stake_cr else 0.0
                                      for b, v in kt.items()},
                     "facilitiesAtCap": {"low": lo["capped"], "high": hi["capped"]},
                     "censoredShareOfCredit": {"low": lo["censored"], "high": hi["censored"]},
                     "selectingShareOfCredit": {"low": lo["selecting"], "high": hi["selecting"]}})
    base = run(1, lam["perYear"])
    if base["selecting"] or any(r["selectingShareOfCredit"]["high"] for r in scen):
        raise ValueError("forward: credit falls on an event containing a selecting plume (FORWARD.selectingShare)")
    out = {"creditWindow": [d0, credit_end], "stakeFrom": stake_from, "members": len(members),
            "memberNames": sorted(members), "membersQuantified": len(quantified),
            "membersWithoutPassAfterD": len(no_pass_after), "membersCredited": len(rows),
            "stakeLowKtCh4PerYr": round(sum(quantified.values()), 2),
            "stakeLowKtCh4PerYrCredited": stake_cr,
            "reonsetHazard": lam, "creditedEvents": sum(len(r["evs"]) for r in rows.values()),
            "censoredEvents": sum(e["censored"] for r in rows.values() for e in r["evs"]),
            "selectingShareOfCredit": base["selecting"], "scenarios": scen}
    if published_by_d:
        out["detectionsPublishedOnOrAfterDDropped"] = dropped
    if bootstrap:
        r_of = {c: rcls[c]["share"] for c in rcls}
        terms = {n: ptime.credit_terms(r["post"], r["evs"], r["lags"], delays, r_of) for n, r in rows.items()}
        out["bootstrap"] = _forward_bootstrap(sorted(members), parts, rows, terms, bootstrap,
                                              _row(out, central)["kt_ch4_per_yr"]["low"])
    return out


def _forward_bootstrap(names: list[str], parts: dict, rows: dict, terms: dict, design: dict, point: float) -> dict:
    """Members resampled with replacement (FORWARD_POST_RESULT.bootstrap): each draw re-measures the re-onset hazard on
    the drawn members' passes up to D and recomputes the central figure (central action share, that hazard) and its
    share of the drawn credited members' stake. Fails closed unless the undrawn sample reproduces the point figure."""
    import random

    import numpy as np

    def figure(sample: list[str]) -> tuple[float, float]:
        restarts = sum(parts[n][0] for n in sample if n in parts)
        off = sum(parts[n][1] for n in sample if n in parts)
        if off <= 0:
            raise ValueError("forward bootstrap: a draw without event-free time (hazard undefined)")
        lam = restarts / (off / DAYS_PER_YEAR)
        kt = stake = 0.0
        for n in sample:
            if n in rows:
                r = rows[n]
                c = ptime.credit_at(terms[n], lam, DAYS_PER_YEAR)
                kt += r["stake"]["kt_ch4_per_yr"]["low"] * min(1.0, c / r["emit"] if r["emit"] > 0 else 0.0)
                stake += r["stake"]["kt_ch4_per_yr"]["low"]
        return kt, (kt / stake if stake else 0.0)

    kt0, _ = figure(names)
    if round(kt0, 2) != point:
        raise ValueError(f"forward bootstrap: the undrawn sample gives {kt0:.2f}, not the scope's {point}")
    rng = random.Random(design["seed"])
    draws = [figure([rng.choice(names) for _ in names]) for _ in range(design["draws"])]
    conf = design["confidence"]
    q = [(1 - conf) / 2, (1 + conf) / 2]
    kt_ci = np.quantile([k for k, _ in draws], q)
    sh_ci = np.quantile([s for _, s in draws], q)
    return {"draws": design["draws"], "seed": design["seed"], "confidence": conf, "unit": "member",
            "ktCh4PerYrCentral": {"point": point, "ci": [round(float(v), 2) for v in kt_ci]},
            "shareOfStakeCentral": {"ci": [round(float(v), ptime.T_DIGITS) for v in sh_ci]},
            "rule": design["rule"]}


def _forward_members(inp: dict, bt: dict, join_rule: bool = False, origin: str | None = None) -> dict[str, set]:
    """FORWARD.members over the universe (the backtest's evaluated facilities): absent at D and a Carbon Mapper plume
    published before D (join_rule: its scene inside [D - WINDOW_MONTHS, D]). Values: the selecting plumes' scene dates."""
    from common import WINDOW_MONTHS, minus_months

    d0 = origin or ptime.FORWARD["D"]
    ws = minus_months(d0, WINDOW_MONTHS)
    fac = inp["facilities"]["byName"]
    out = {}
    for f in bt["facilities"]:
        if not f.get("evaluated") or not ptime.absent_at(fac[f["name"]]["mars_dates"], d0, ws):
            continue
        sel = {r["scene_timestamp"][:10] for r in ptime.facility_plumes(fac[f["name"]])
               if r["published_at"] and r["published_at"][:10] < d0
               and (not join_rule or ws <= r["scene_timestamp"][:10] <= d0)}
        if sel:
            out[f["name"]] = sel
    return out


def _forward_universe_leak(inp: dict) -> dict:
    """FORWARD.leaks.universe, counted: notified oil and gas MARS sources outside today's targets (not labelled absent
    today) that meet absentAtD, and how many have a cm_timelines_notified.json source within RADIUS_KM detected before
    D (their plume rows, rates and publication times are not committed)."""
    import numpy as np

    from common import WINDOW_MONTHS, minus_months

    d0 = ptime.FORWARD["D"]
    ws = minus_months(d0, WINDOW_MONTHS)
    dates: dict[str, list[str]] = {}
    for r in inp["marsPlumes"]:
        dates.setdefault(r["source_name"], []).append(r["tile_date"])
    notified = [r for r in inp["mars"] if r["sector"] == "Oil and Gas" and r["notified"] == "Yes"]
    outside = [r for r in notified if r["persistency_category"] != "absent"]
    absent = [r for r in outside if ptime.absent_at(dates.get(r["source_name"], []), d0, ws)]
    cm = [(t["lat"], t["lon"]) for t in inp["cmTimelinesNotified"].values() if t["det"] and min(t["det"]) < d0]
    lat, lon = np.array([a for a, _ in cm]), np.array([b for _, b in cm])
    near = sum(haversine_km(float(r["lat"]), float(r["lon"]), lat, lon).min() <= RADIUS_KM for r in absent) if cm else 0
    return {"notifiedOutsideUniverse": len(outside), "absentAtDOutsideUniverse": len(absent),
            "withCarbonMapperDetectionBeforeD": int(near), "rule": ptime.FORWARD["leaks"]["universe"]}


def _forward(inp: dict, prm: dict, bt: dict, gwp: dict, pv3: dict, pv3_pub: dict, ledger_at) -> dict:
    """Board B1 (projection_time.FORWARD): the forward scope, its registered sensitivity rows, the counted universe
    leak, the twelve-month control (FORWARD_CONTROL_LENGTH), the forward-basis ledger cost and its card. Fails closed
    if D or the credit end no longer match their stated sources. pv3: T4d's portfolio_v3 scope; pv3_pub: the published
    portfolio_v3 scope (its ledger and per-t cost are the whole-window comparison); ledger_at(episodes a year)."""
    fw = ptime.FORWARD
    d0, cend = fw["D"], fw["creditEnd"]
    as_of = date.fromisoformat(inp["join"]["summary"]["mars_as_of"])
    if as_of.replace(year=as_of.year - 1).isoformat() != d0 or bt["catalogueEnd"] != cend:
        raise ValueError("FORWARD.D / creditEnd no longer match the MARS as-of date and the catalogue end")
    bt_by = {f["name"]: f for f in bt["facilities"]}
    members = _forward_members(inp, bt)
    ev = prm["parameters"]["evidence"]
    bd = ptime.FORWARD_POST_RESULT["bootstrap"]
    design = {"draws": ev[bd["drawsFrom"]], "seed": ev[bd["seedFrom"]], "confidence": ev[bd["confidenceFrom"]],
              "rule": bd["rule"]}
    sc = _forward_scope(inp, prm, bt_by, gwp, members, cend, bootstrap=design)
    win0 = date.fromisoformat(inp["join"]["summary"]["window"][0])
    early_end = date.fromordinal(win0.toordinal() - 1).isoformat()
    join_m = _forward_members(inp, bt, join_rule=True)
    post = ptime.FORWARD_POST_RESULT
    ctl = post["controlShortWindow"]
    variants = {"fwd_stake_backtest": (members, cend, "backtest", False, None),
                "fwd_join_rule": (join_m, cend, "atD", False, None),
                "fwd_credit_before_absent_window": (members, early_end, "atD", False, None),
                "fwd_stake_published_by_D": (members, cend, "atD", True, None),
                "fwd_control_short_window": (_forward_members(inp, bt, origin=ctl["origin"]), ctl["creditEnd"], "atD",
                                             False, ctl["origin"])}
    central = prm["parameters"]["responseScenarios"]["central"]
    sens = []
    for sid, rule, post_result in [*((k, v, False) for k, v in fw["sensitivity"].items()),
                                   *((k, v, True) for k, v in post["sensitivity"].items())]:
        m, end, sf, pub, origin = variants[sid]
        v = _forward_scope(inp, prm, bt_by, gwp, m, end, sf, published_by_d=pub, origin=origin)
        row = _row(v, central)
        sens.append({"id": sid, "rule": rule, "setsValue": False, "postResult": post_result, "members": v["members"],
                     "membersQuantified": v["membersQuantified"], "origin": origin or fw["D"], "creditEnd": end,
                     "ktCh4PerYrCentralAction": row["kt_ch4_per_yr"], "shareOfStakeCentralAction": row["shareOfStake"],
                     "stakeLowKtCh4PerYrCredited": v["stakeLowKtCh4PerYrCredited"],
                     "reonsetHazardPerYear": v["reonsetHazard"]["perYear"],
                     "selectingShareOfCredit": v["selectingShareOfCredit"]}
                    | ({"detectionsDropped": v["detectionsPublishedOnOrAfterDDropped"]} if pub else {}))
    _control_reading(sens, _row(sc, central)["shareOfStake"]["low"], ctl)
    full = _control_full_length(inp, prm, bt, bt_by, gwp, central, early_end, _row(sc, central)["shareOfStake"]["low"],
                                {r["id"]: r for r in sens}["fwd_credit_before_absent_window"], sc["bootstrap"])
    led = _forward_ledger(bt_by, sc, central, gwp, ledger_at, _row(pv3_pub, central))
    leak = _forward_universe_leak(inp)
    late = _forward_late_published(inp, members)
    boot = sc.pop("bootstrap")
    rolling = _forward_rolling(inp, prm, bt, bt_by, gwp, sc, central)
    return {"preRegistration": fw, "postResult": post, "scopes": {fw["id"]: sc}, "bootstrap": boot, "rolling": rolling,
            "sensitivity": sens, "controlFullLength": full, "ledger": led,
            "leaks": {"universe": leak, "marsPublication": fw["leaks"]["marsPublication"],
                      "passPublication": fw["leaks"]["passPublication"],
                      "detectionPublication": late | {"rule": post["leaks"]["detectionPublication"]}},
            "card": _forward_card(sc, central, gwp, pv3, {"universe": leak, "detectionPublication": late}, boot,
                                  {r["id"]: r for r in sens}, rolling, full, led)}


def _control_reading(sens: list[dict], primary_share: float, ctl: dict) -> None:
    """FORWARD_POST_RESULT.controlShortWindow.reads, applied: adds shortWindowExplainsMost and a generated clause to
    the control row."""
    by = {r["id"]: r for r in sens}
    c, cut = by["fwd_control_short_window"], by["fwd_credit_before_absent_window"]
    cs, xs = c["shareOfStakeCentralAction"]["low"], cut["shareOfStakeCentralAction"]["low"]
    short = abs(cs - xs) < abs(cs - primary_share)
    c["shortWindowExplainsMost"] = short
    c["clause"] = (
        f"the same six-month credit length from {c['origin']} to {c['creditEnd']}, also before the label window, gives "
        f"~{c['ktCh4PerYrCentralAction']['low']:.2f} kt ({cs * PERCENT:.2f} % of stake, against "
        f"{xs * PERCENT:.2f} % for the credit ending before the label and {primary_share * PERCENT:.2f} % over the full "
        "window), so " + ("the short window, not the label, explains most of that drop" if short else
                          "the short window explains little of that drop"))


def _control_full_length(inp: dict, prm: dict, bt: dict, bt_by: dict, gwp: dict, central: float, early_end: str,
                         primary_share: float, cut: dict, boot: dict) -> dict:
    """projection_time.FORWARD_CONTROL_LENGTH: FORWARD's rules at its origin with credit up to its creditEnd, read by
    its rule against the full-window share and the credit ending before the label window. Reported only. Fails closed
    if its creditEnd is no longer the day before today's absent window or its length is not FORWARD's."""
    rule = ptime.FORWARD_CONTROL_LENGTH
    o0, end = rule["origin"], rule["creditEnd"]
    if end != early_end:
        raise ValueError("FORWARD_CONTROL_LENGTH.creditEnd is not the day before today's absent window")

    def months(a: str, b: str) -> int:  # the window's length in months, to the nearest month
        return round((date.fromisoformat(b) - date.fromisoformat(a)).days / (DAYS_PER_YEAR / len(MONTH_WORDS[1:])))

    n, n_fw = months(o0, end), months(ptime.FORWARD["D"], ptime.FORWARD["creditEnd"])
    if n != n_fw:
        raise ValueError(f"FORWARD_CONTROL_LENGTH covers {n} months, FORWARD {n_fw}")
    v = _forward_scope(inp, prm, bt_by, gwp, _forward_members(inp, bt, origin=o0), end, origin=o0)
    row = _row(v, central)
    cs, xs = row["shareOfStake"]["low"], cut["shareOfStakeCentralAction"]["low"]
    ci = boot["shareOfStakeCentral"]["ci"]
    near = ci[0] <= cs <= ci[1] and abs(cs - primary_share) < abs(cs - xs)
    reading = rule["readsIfNear"] if near else rule["readsOtherwise"]
    clause = (f"a {MONTH_WORDS[n]}-month credit window from {o0} to {end}, also before the label window, gives "
              f"~{row['kt_ch4_per_yr']['low']:.2f} kt ({cs * PERCENT:.2f} % of stake, against "
              f"{xs * PERCENT:.2f} % for the credit ending before the label and {primary_share * PERCENT:.2f} % over "
              "the full window), " + (f"so {reading}" if near else f"but {reading}"))
    return {"id": rule["id"], "preRegistration": rule, "setsValue": False, "origin": o0, "creditEnd": end, "months": n,
            "members": v["members"], "membersCredited": v["membersCredited"],
            "stakeLowKtCh4PerYrCredited": v["stakeLowKtCh4PerYrCredited"],
            "reonsetHazardPerYear": v["reonsetHazard"]["perYear"],
            "ktCh4PerYrCentralAction": row["kt_ch4_per_yr"], "shareOfStakeCentralAction": row["shareOfStake"],
            "selectingShareOfCredit": v["selectingShareOfCredit"],
            "fullWindowShare": primary_share, "cutRowShare": xs, "bootstrapShareCi": ci,
            "nearFullWindow": near, "reading": reading, "clause": clause}


def _forward_ledger(bt_by: dict, sc: dict, central: float, gwp: dict, ledger_at, pv3_row: dict) -> dict:
    """The ledger cost on the forward basis: the members' own backtest episodes dated after D up to creditEnd, per
    year of that window, priced as the portfolio's ledger (one createCovenant a year), over the forward scope's
    projected t CO2e; the published whole-window per-t cost is kept as the comparison."""
    fw = ptime.FORWARD
    d0, end = fw["D"], fw["creditEnd"]
    eps = sum(1 for n in sc["memberNames"] for e in bt_by[n].get("episodes", []) if d0 < e["date"] <= end)
    years = (date.fromisoformat(end) - date.fromisoformat(d0)).days / DAYS_PER_YEAR
    led = ledger_at(round(eps / years, 3))
    per_t = [_sig4(led["usdPerYear"] / (v * T_PER_KT * gwp["gwp100"]))
             for r in sc["scenarios"] for v in r["kt_ch4_per_yr"].values()]
    t_central = _row(sc, central)["kt_ch4_per_yr"]["low"] * T_PER_KT * gwp["gwp100"]
    return {"basis": ("the forward members' backtest episodes dated after D up to creditEnd, per year of that window, "
                      "plus one createCovenant a year"),
            "window": [d0, end], "episodes": eps, "windowYears": round(years, 4)} | led | {
            "usdPerTco2eGwp100Central": _sig4(led["usdPerYear"] / t_central),
            "usdPerTco2eGwp100Range": [min(per_t), max(per_t)],
            "usdPerTco2eGwp100WholeWindow": pv3_row["ledgerUsdPerTco2eGwp100"]["low"]}


def _forward_rolling(inp: dict, prm: dict, bt: dict, bt_by: dict, gwp: dict, primary: dict, central: float) -> dict:
    """projection_time.FORWARD_ROLLING: FORWARD's rules at each other origin, per origin and pooled with FORWARD.D,
    as kt CH4/yr and as a share of the credited stake. Reported only."""
    rr = ptime.FORWARD_ROLLING
    if rr["creditEnd"] != ptime.FORWARD["creditEnd"]:
        raise ValueError("FORWARD_ROLLING.creditEnd is not FORWARD's")

    def summary(d0: str, sc: dict) -> dict:
        row = _row(sc, central)
        return {"origin": d0, "members": sc["members"], "membersCredited": sc["membersCredited"],
                "stakeLowKtCh4PerYrCredited": sc["stakeLowKtCh4PerYrCredited"],
                "reonsetHazardPerYear": sc["reonsetHazard"]["perYear"],
                "ktCh4PerYrCentralAction": row["kt_ch4_per_yr"], "shareOfStakeCentralAction": row["shareOfStake"],
                "selectingShareOfCredit": sc["selectingShareOfCredit"]}

    rows = [summary(ptime.FORWARD["D"], primary)]
    for d0 in rr["origins"]:
        m = _forward_members(inp, bt, origin=d0)
        rows.append(summary(d0, _forward_scope(inp, prm, bt_by, gwp, m, rr["creditEnd"], origin=d0)))
    rows.sort(key=lambda r: r["origin"])
    if any(r["selectingShareOfCredit"] for r in rows):
        raise ValueError("forward rolling: credit falls on an event containing a selecting plume")
    stake = sum(r["stakeLowKtCh4PerYrCredited"] for r in rows)
    pooled = {b: round(sum(r["ktCh4PerYrCentralAction"][b] for r in rows) / stake, ptime.T_DIGITS) for b in ("low", "high")}
    return {"preRegistration": rr, "origins": rows,
            "pooled": {"origins": len(rows), "shareOfStakeCentralAction": pooled,
                       "ktCh4PerYrCentralActionSum": {b: round(sum(r["ktCh4PerYrCentralAction"][b] for r in rows), 2)
                                                     for b in ("low", "high")},
                       "stakeLowKtCh4PerYrCreditedSum": round(stake, 2)}}


def _forward_late_published(inp: dict, members: dict[str, set]) -> dict:
    """FORWARD_POST_RESULT.leaks.detectionPublication, counted on the members: detections dated after the start and
    up to D that no plume published before D shows."""
    fac, d0 = inp["facilities"]["byName"], ptime.FORWARD["D"]
    late = {n: ptime.without_late_published(fac[n], d0)[1] for n in members}
    return {"detections": sum(len(v) for v in late.values()), "members": sum(bool(v) for v in late.values())}


# The share-of-stake denominator every scope uses (forward rows and the T4d nonSelecting field): T4d credits every
# quantified facility (each has a pass after its start); the forward scope credits the members quantified at D with a
# pass after D (stakeLowKtCh4PerYrCredited).
STAKE_DENOMINATOR = ("the stake low bound of the facilities the scope credits: quantified, with a satellite pass in the "
                     "credit window")
MONTH_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
               "twelve")


def _kt1(x: float) -> str:
    """One decimal for the forward scope's small tonnages."""
    return f"{x:,.1f}"


def _forward_card(sc: dict, central: float, gwp: dict, pv3: dict, leaks: dict, boot: dict, rows: dict,
                  rolling: dict, full: dict, led: dict) -> dict:
    """The forward scope's sentences and figures, read from the scope (vision first: [PROJECTION] once). The headline
    is the reviewed sentence (task P1b, 2026-10-24) with its fields generated; the band runs from the lowest action
    scenario with the measured re-onset hazard to the highest with no recurrence."""
    from common import WINDOW_MONTHS

    fw = ptime.FORWARD
    row = _row(sc, central)
    kt = row["kt_ch4_per_yr"]
    lo = min(sc["scenarios"], key=lambda r: r["responseShare"])
    hi = max(sc["scenarios"], key=lambda r: r["responseShare"])
    band = (lo["kt_ch4_per_yr"]["low"], hi["kt_ch4_per_yr"]["high"])
    pv_row = _row(pv3, central)
    ci = boot["ktCh4PerYrCentral"]["ci"]
    uni, late = leaks["universe"], leaks["detectionPublication"]
    pub_row, cut_row = rows["fwd_stake_published_by_D"], rows["fwd_credit_before_absent_window"]
    lam, whole = sc["reonsetHazard"], pv3["reonsetHazard"]
    more = [r["shareOfStakeCentralAction"]["low"] for r in rolling["origins"] if r["origin"] != fw["D"]]
    months = MONTH_WORDS[WINDOW_MONTHS]
    return {
        "centralKtCh4PerYr": kt["low"], "upToNoRecurrenceKtCh4PerYr": kt["high"],
        "lowKtCh4PerYr": band[0], "highKtCh4PerYr": band[1],
        "bandNote": ("central = the central action share with the measured re-onset hazard; low = the lowest action "
                     "scenario with it; high = the highest action scenario with no recurrence"),
        "bootstrapKtCh4PerYrCentralCi": ci, "bootstrapConfidence": boot["confidence"],
        "members": sc["members"], "membersQuantified": sc["membersQuantified"],
        "membersCredited": sc["membersCredited"], "stakeLowKtCh4PerYrCredited": sc["stakeLowKtCh4PerYrCredited"],
        "selectingShareOfCredit": sc["selectingShareOfCredit"],
        "shareOfStakeCentral": row["shareOfStake"]["low"],
        "shareOfStakeAllQuantifiedCentral": round(kt["low"] / sc["stakeLowKtCh4PerYr"], ptime.T_DIGITS),
        "stakeLowKtCh4PerYrAllQuantified": sc["stakeLowKtCh4PerYr"],
        "comparedWith": {"scope": "portfolio_v3", "denominator": STAKE_DENOMINATOR,
                         "forwardCentralShareOfStake": row["shareOfStake"]["low"],
                         "centralKtCh4PerYr": pv_row["kt_ch4_per_yr"]["low"],
                         "allCreditCentralShareOfStake": pv3["nonSelecting"]["allCreditCentralShareOfStake"],
                         "nonSelectingKtCh4PerYrCentral": pv3["nonSelecting"]["ktCh4PerYrCentral"],
                         "nonSelectingCentralShareOfStake": pv3["nonSelecting"]["centralShareOfStake"],
                         "selectingShareOfCredit": pv3["selectingShareOfCredit"]},
        "headlineSentence": (
            f"[PROJECTION] Pre-registered forward hold-out: a {sc['members']}-facility portfolio covenant [SIM], "
            f"chosen with Carbon Mapper plumes published before {fw['D']} at sources MARS had not seen for {months} "
            "months and lists as absent today, and credited only for faults first detected after that date up to "
            f"{fw['creditEnd']}, would stop ~{_kt1(kt['low'])} kt CH₄/yr at the central {_pct(central)} action share "
            f"({boot['confidence'] * PERCENT:.0f} % member bootstrap ~{_kt1(ci[0])}–{_kt1(ci[1])} kt; "
            f"~{_kt1(band[0])}–{_kt1(band[1])} kt across action and recurrence scenarios), "
            f"{_mt(row['mt_co2e_per_yr_gwp100']['low'])} CO₂e/yr at GWP-100, none of it on the plume that selected a "
            f"member — {HEADLINE_END}"),
        "stakeSentence": (
            f"Stake, not abatement: ~{sc['stakeLowKtCh4PerYrCredited']:,.0f} kt CH₄/yr across the "
            f"{sc['membersCredited']} members with a stake known on {fw['D']} and a satellite pass after it; the "
            f"projection credits {row['shareOfStake']['low'] * PERCENT:.1f} % of it "
            f"({kt['low'] / sc['stakeLowKtCh4PerYr'] * PERCENT:.1f} % of the ~{sc['stakeLowKtCh4PerYr']:,.0f} kt stake "
            f"of all {sc['membersQuantified']} quantified members)."),
        "intervalSentence": (
            f"Resampling the members ({boot['draws']:,} draws, the re-onset hazard re-measured in each) puts the "
            f"central figure between ~{_kt1(ci[0])} and ~{_kt1(ci[1])} kt CH₄/yr "
            f"({boot['confidence'] * PERCENT:.0f} % interval)."),
        "rollingShareOfStakeRange": [min(more), max(more)],
        "rollingSentence": (
            f"At {MONTH_WORDS[len(more)]} more origins registered after this result, with overlapping credit windows, "
            f"the share is {min(more) * PERCENT:.1f}–{max(more) * PERCENT:.1f} %."),
        "comparisonSentence": (
            f"Credited over each member's whole window instead, the same model gives {_kt(pv_row['kt_ch4_per_yr']['low'])} "
            f"CH₄/yr for the portfolio covenant set v3, {_pct(pv3['selectingShareOfCredit'])} of it on events containing "
            "the plume that selected the member; the forward hold-out credits none."),
        "hazardSentence": (
            f"In the forward hold-out the re-onset hazard is measured on the members' own passes up to {fw['D']} "
            f"only, {lam['perYear']:.2f} a year: {lam['restarts']} restarts over {lam['eventFreeYears']} event-free "
            f"facility-years (over each member's whole window, {whole['perYear']:.2f} a year: {whole['restarts']} "
            f"restarts over {whole['eventFreeYears']}); each fix is credited until the fault's observed end, the last "
            f"pass on or before {fw['creditEnd']} or a new fault."),
        "costSentence": (
            f"Ledger cost only, not the cost of the work on site: on the forward basis the {sc['members']} members had "
            f"{led['episodes']} episodes from {fw['D']} to {fw['creditEnd']}; at USD {led['usdPerEpisode']:.2f} an "
            f"episode plus one createCovenant that is USD {led['usdPerYear']:,.0f} a year, or "
            f"{_usd_small(led['usdPerTco2eGwp100Central'])} per t CO₂e projected at the central action "
            f"share ({_rusd(led['usdPerTco2eGwp100Range'])} across scenarios and with no recurrence); credited over "
            f"each member's whole window instead, the portfolio covenant set v3 costs "
            f"{_usd_small(led['usdPerTco2eGwp100WholeWindow'])} per t CO₂e."),
        "limitsSentence": (
            f"The forward members come from today's MARS targets (notified and labelled absent today; "
            f"{uni['absentAtDOutsideUniverse']} other notified oil and gas sources MARS had not seen for {months} "
            f"months on {fw['D']} lie outside them because their Carbon Mapper plume rows are not committed), and "
            "both catalogues are read as they stand today: MARS rows and Carbon Mapper passes without a detection "
            f"carry no publication date, and {late['detections']} detections dated up to {fw['D']} were published "
            f"only on or after it (without them, ~{_kt1(pub_row['ktCh4PerYrCentralAction']['low'])} kt); today's "
            "'absent' label is decided inside the credit window; credit ending before it (to "
            f"{cut_row['creditEnd']}) gives ~{_kt1(cut_row['ktCh4PerYrCentralAction']['low'])} kt; "
            f"{full['clause']}."),
    }


def main() -> None:
    out = build(load_inputs())
    write_json(OUT, out)
    print(f"projection -> {OUT.relative_to(ROOT)}: " + out["card"]["projectionSentence"])


if __name__ == "__main__":
    main()
