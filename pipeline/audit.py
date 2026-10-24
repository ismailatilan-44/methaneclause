"""Collect every number the README/PDF may quote into data/derived/audit_numbers.json.

Diffs against the previous audit file and prints changes. STRICT=1 exits non-zero on any change
(provider re-processing shows up here first; it is flagged, not hidden).
"""
from __future__ import annotations

import json
import os
import sys

from common import DERIVED, load_manifest, now_utc, write_json

AUDIT = DERIVED / "audit_numbers.json"


def flat(d, pre=""):
    for k, v in d.items():
        if isinstance(v, dict):
            yield from flat(v, f"{pre}{k}.")
        else:
            yield f"{pre}{k}", v


def _model_numbers() -> dict:
    m = json.loads((DERIVED / "model" / "model_metrics.json").read_text())
    keep = ("n", "brier", "auc", "ece", "false_zero_rate", "n_forecast_below_5pct", "bss_vs_B1",
            "bss_ci95_cell_bootstrap", "bss_vs_B1s", "bss_vs_B1s_ci95_cell_bootstrap")
    return {"decision": m["decision"], "modelHash": m["modelHash"], "featureSpecHash": m["featureSpecHash"],
            **{split: {who: {k: v[k] for k in keep if k in v} for who, v in r.items() if isinstance(v, dict)}
               for split, r in {**m["results"], **m["breakdown"]}.items() if split != "train"}}


def _bond_numbers() -> dict:
    b = json.loads((DERIVED / "model" / "bond_sizing.json").read_text())
    return {"forecaster_used": b["forecaster_used"], "recommended_bond_eth": b["recommended_bond_eth"],
            "set_annual_exposure": b["set_annual_exposure"],
            "set_backtest": {k: v for k, v in b["set_backtest"].items() if k != "note"},
            "p_pass": {n: f["p_pass"] for n, f in b["facilities"].items()}}


def _testnet_numbers() -> dict | str:
    """The operator's public-testnet run (observer/testnet.py): contract addresses, episode, each step's outcome."""
    p = DERIVED / "testnet_run.json"
    if not p.exists():
        return "run `make testnet-run` first"
    r = json.loads(p.read_text())
    return {"network": r["network"], "methaneCovenant": r["methaneCovenant"], "keyRegistry": r["keyRegistry"],
            "covenantId": r["covenantId"], "evidenceHash": r["evidenceHash"], "episodeId": r["episodeId"],
            "steps": {s["step"]: {"status": s["status"], "reason": s.get("reason"), "tx": s["tx"]} for s in r["steps"]}}


def _measurement_numbers() -> dict | str:
    p = DERIVED / "measurements.json"
    if not p.exists():
        return "run `make measure` first"
    m = json.loads(p.read_text())
    return {"latency_seconds": {k: m["latency_seconds"][k] for k in ("n", "p50", "p90")},
            "transactions": {k: v for k, v in m["transactions"].items() if not k.endswith("note")},
            "bundle_bytes_jcs": m["bundle_bytes_jcs"], "eth_usd": m["usd_assumption"]["eth_usd"],
            "gas_sepolia_median": {k: v["gas_sepolia_median"] for k, v in m["functions"].items()}}


def _optional(name: str, fn):
    p = DERIVED / name
    return fn(json.loads(p.read_text())) if p.exists() else f"run the lane-B target that writes {name} first"


def _cleanruns(d: dict) -> dict:
    keep = ("units", "units_ge2_detections", "with_run", "with_next_pass", "no_next_pass", "next_pass_detected",
            "next_pass_detection_rate", "next_pass_detection_ci95", "base_rate_before_run",
            "base_rate_before_run_excl_eligibility", "run_length_median", "redetected_later_right_censored",
            "redetected_later", "redetected_later_share")

    def pick(v: dict) -> dict:
        return {**{k: v[k] for k in keep},
                "hazard_after_run": {str(h["k"]): f"{h['detected']}/{h['at_risk']}" for h in v["hazard_after_run"]},
                "redetected_by_follow_up": {r["passes_after"]: f"{r['redetected']}/{r['units']}"
                                            for r in v["redetected_by_follow_up"]}}

    return {"inputs": {g["path"]: g["sha256"] for g in d["generatedFrom"]}, "unitLabel": d["unitLabel"],
            "cells": d["cells"], "max_cell_diameter_km": d["max_cell_diameter_km"],
            "sources_with_run": d["sources_with_run"], "cells_with_run": d["cells_with_run"],
            "card": d["card"], "selection_windows_not_base_rates": d["selection_windows_not_base_rates"],
            "by_unit": {u: pick(v) for u, v in d["by_unit"].items()},
            "subset_tanager_emit": {u: pick(d["subset_tanager_emit"][u]) for u in ("cell", "source")},
            "by_instrument_cell": {i: {k: v["cell"][k] for k in ("with_run", "with_next_pass", "next_pass_detected",
                                                                    "next_pass_detection_rate")}
                                   for i, v in d["by_instrument"].items()},
            "upper_bound_bp": ",".join(map(str, d["upper_bound_bp"]))}


def _clean_passes() -> dict:
    from clean_passes import CASES

    out = {}
    for case in CASES:
        f = DERIVED / "cases" / f"{case}.clean_passes.json"
        if not f.exists():
            out[case] = "run `make clean-passes` first"
            continue
        d = json.loads(f.read_text())
        s = d["summary"]
        out[case] = {"since": d["sinceIso"], "episode": d["episode"]["plumeId"], "rateKgH": d["episode"]["rateKgH"],
                     "policyHash": d["covenant"]["policyHash"], "countIsLowerBound": d["countIsLowerBound"],
                     "generatedFrom": {g.get("path") or g["query"]: g.get("sha256") or g["contentSha256"]
                                       for g in d["generatedFrom"]},
                     **{k: s[k] for k in ("candidates", "qualified", "qualifiedBeforeFirstPlume",
                                          "qualifiedBeforeFirstPlumeByInstrument", "statusLabel",
                                          "maxCountedFloorKgH", "countedAtCloudBp2500",
                                          "qualifiedBeforeFirstPlumeAllOriginsToday", "firstRefusal")},
                     "qualifiedScenes": ", ".join(s["qualifiedScenes"])}
    return out


def missing_inputs(numbers: dict) -> list[str]:
    """Keys whose value is a 'run ... first' placeholder (an input file was absent)."""
    return [k for k, v in flat(numbers) if isinstance(v, str) and v.startswith("run ") and v.endswith(" first")]


def refuse_missing(numbers: dict) -> None:
    """STRICT=1: a missing input is a failure, not a string in audit_numbers.json (spec-v2 §15, B1 review)."""
    gone = missing_inputs(numbers)
    if gone and os.environ.get("STRICT") == "1":
        print(f"audit: STRICT and {len(gone)} input(s) missing: " + ", ".join(gone))
        sys.exit(1)


def _floors(d: dict) -> dict:
    return {"floorKgH": {k: v["kgH"] for k, v in d["instrumentFloorKgH"].items()},
            "floorStatus": {k: v["status"] for k, v in d["instrumentFloorKgH"].items()},
            "gwp": {k: {"value": v["value"], "status": v["status"]} for k, v in d["gwp"].items()}}


def _data_quality(d: dict) -> dict:
    keep = ("plumes", "hidden_emission", "uncertainty_exceeds_rate", "emission_version")
    return {"inputs": {g["path"]: g["sha256"] for g in d["generatedFrom"]},
            "neighbourhood": {k: d["neighbourhood"][k] for k in keep},
            "joinCandidates": {k: d["joinCandidates"][k] for k in keep},
            "cases": {c: {k: v[k] for k in keep} for c, v in d["cases"].items()},
            "joinRadiusSensitivity": {str(r["radiusKm"]): {k: r[k] for k in ("n_candidates", "n_candidate_plumes",
                                                                             "n_cm_sources_within_radius",
                                                                             "n_cm_sources_without_plume_records")}
                                      for r in d["joinRadiusSensitivity"]["rows"]},
            "joinCriterionHash": d["joinCriterionHash"]}


def _backtest(d: dict) -> dict:
    t = d["totals"]
    return {"label": d["label"], "policyHash": d["policyHash"], "catalogueEnd": d["catalogueEnd"],
            "inputs": {g["path"]: g["sha256"] for g in d["generatedFrom"]}, "targets": d["targets"],
            "totals": {k: t[k] for k in ("facilitiesEvaluated", "facilitiesWithEpisode", "episodes", "tierMix",
                                         "linkedDetections", "detectionsBelowMinOrUnquantified", "attestedKgH",
                                         "rebatePolicyUnits", "L1Share", "L2Share", "qualified", "upper_bound")},
            "exporter": {k: d["exporter_table"]["totals"][k] for k in ("facilitiesEvaluated", "episodes",
                                                                       "rebatePolicyUnits", "upper_bound")},
            "stake_band": {k: d["stake_band"][k] for k in ("facilitiesQuantified", "kt_ch4_per_yr", "co2e",
                                                           "dutyCycleConcentration")},
            "card": d["card"]}


def _bond_v3(d: dict) -> dict:
    return {"sourceset_root": d["sourceset_root"], "recommended_bond_eth": d["recommended_bond_eth"],
            "pool_eth": d["pool"]["eth"], "set_annual_exposure_eth": d["set_annual_exposure"]["eth"],
            "set_annual_exposure_policy_units": d["set_annual_exposure"]["policyUnits"],
            "portfolioSentence": d["card"]["portfolioSentence"]}


def _projection(d: dict) -> dict:
    """[PROJECTION] bands, ledger cost per tonne, the MARS negative finding and the card sentences (projection.py)."""
    return {"label": d["label"], "inputs": {g["path"]: g["sha256"] for g in d["generatedFrom"]},
            "parameters": {"baselineResponseShare": d["parameters"]["baselineResponseShare"]["value"],
                           "responseScenarios": ", ".join(map(str, d["parameters"]["responseScenarios"]["values"])),
                           "ceasedShare": {b: d["parameters"]["ceasedShare"][b] for b in ("low", "high")},
                           "responseRule": d["responseRule"]},
            # T4b: the chosen pairing, the pairings it was chosen over, and the weighting it could not use
            # interim (T4b review): the headline is a range with no central tonnage until the time factor (T4c)
            "headline": d["headline"],
            "model": {"choice": d["model"]["choice"], "range": d["model"]["range"],
                      "emissionClasses": d["model"]["emissionClasses"], "violations": d["model"]["violations"],
                      "sensitivity": {r["id"]: {k: r[k] for k in ("r", "d", "rangeEnd", "ktCh4PerYrCentral") if k in r}
                                      for r in d["modelSensitivity"]},
                      "weighting": {k: d["weighting"][k] for k in ("incidences", "years", "uniqueJoinKey")}},
            "scopes": {n: {"facilities": s["facilities"], "facilitiesQuantified": s["facilitiesQuantified"],
                           "stakeLowKtCh4PerYr": s["stakeLowKtCh4PerYr"],
                           "ledger": {k: s["ledger"][k] for k in ("episodesPerYear", "usdPerEpisode", "usdPerYear")},
                           "scenarios": {str(r["responseShare"]): {k: r[k] for k in (
                               "kt_ch4_per_yr", "mt_co2e_per_yr_gwp100", "mt_co2e_per_yr_gwp20",
                               "ledgerUsdPerTco2eGwp100")} for r in s["scenarios"]}}
                       for n, s in d["scopes"].items()},
            "negativeFinding": {k: d["negativeFinding"][k] for k in ("notified", "byFeedback")},
            "evidence": {
                "line1": {k: d["evidence"]["line1"][k] for k in ("byFeedback", "difference",
                                                                  "mantelHaenszelDifference")},
                "line2": {**{n: {"runs": {f"{r['spec']['proxy']}_{r['spec']['windowDays']}_{r['spec']['gapDays']}_"
                                          f"{r['spec']['minPassesEachSide']}": {"groups": r["groups"], "did": r["did"]}
                                          for r in [c["primary"], *c["variants"]]}}
                             for n, c in d["evidence"]["line2"]["coverageSets"].items()}}},
            # board T4d: the unpublished time factor (published only when its review passes)
            "t4d": {"headline": d["t4d"]["headline"], "rByPlumeClass": d["t4d"]["rByPlumeClass"],
                    "scopes": {n: {"reonsetHazard": s["reonsetHazard"],
                                   "scenarios": {str(r["responseShare"]): {k: r[k] for k in (
                                       "kt_ch4_per_yr", "mt_co2e_per_yr_gwp100", "mt_co2e_per_yr_gwp20",
                                       "ledgerUsdPerTco2eGwp100")} for r in s["scenarios"]}}
                               for n, s in d["t4d"]["scopes"].items()},
                    "sensitivity": {r["id"]: r.get("ktCh4PerYrCentralAction") for r in d["t4d"]["sensitivity"]},
                    "postResult": {r["id"]: r.get("ktCh4PerYrCentralAction") for r in d["t4d"]["postResultSensitivity"]},
                    "postHoc": {r["id"]: r["ktCh4PerYrCentralAction"] for r in d["t4d"]["postHocSensitivity"]},
                    "diagnostics": {n: {k: s[k] for k in ("reonsetHazardSingleMissMerged", "denominatorOverEventLength",
                                                         "selectingShareOfCredit")}
                                    for n, s in d["t4d"]["scopes"].items()},
                    "notMarsNotifiedSample": {k: v for k, v in d["t4d"]["notMarsNotifiedSample"].items()
                                              if k != "population"}},
            "card": d["card"]}


def _policy_v2(d: dict) -> dict:
    return {n: {"policyHash": v["policyHash"], "onchainParamsHash": v["onchainParamsHash"],
                "settlementMode": v["settlementMode"]} for n, v in d["policies"].items()}


def main() -> None:
    man = load_manifest()
    join = json.loads((DERIVED / "join_v1.json").read_text())["summary"]
    lead = json.loads((DERIVED / "cases" / "LEAD.json").read_text())
    base = json.loads((DERIVED / "model" / "baseline_metrics.json").read_text())
    cases = {}
    for f in sorted((DERIVED / "cases").glob("*_S_*.json")):
        if f.name.endswith(".clean_passes.json"):
            continue
        c = json.loads(f.read_text())
        # headline = facility-radius basis (sources within RADIUS_KM); the all-linked union is kept, labelled secondary
        pm, al = c["post_mars_last_plume"], c.get("post_mars_last_plume_all_linked") or {}
        cases[f.stem] = {**{k: pm[k] for k in ("since", "radius_km", "n_cm_passes", "n_cm_detected") if k in pm},
                         "n_cm_sources_counted": len(pm.get("cm_sources", [])),
                         "all_linked_secondary": {k: al[k] for k in ("n_cm_passes", "n_cm_detected") if k in al},
                         "join_v1_candidate": c["join_v1_candidate"], "case_evidence_hash": c["case_evidence_hash"]}
    numbers = {
        "inputs": {"mars_zip_sha256": man["files"]["mars_zip"]["sha256"],
                   "cm_sources_csv_sha256": man["files"]["cm_sources_csv"]["sha256"],
                   "cm_source_responses_aggregate_sha256": man["cm_source_responses"]["aggregate_sha256"]},
        "join_v1": {k: v for k, v in join.items() if not isinstance(v, (list, dict)) or k == "window"},
        "lead": {k: lead[k] for k in ("lead", "reproduced", "matched_rate_kgh_on_date")},
        "cases": cases,
        "baseline": {split: {m: {k: v[k] for k in ("n", "brier", "auc", "false_zero_rate", "bss_vs_B1") if k in v}
                             for m, v in r.items()} for split, r in base["results"].items()},
        "splits_hash": base["splits_hash"],
        "model": _model_numbers(),
        "bond_sizing": _bond_numbers(),
        "stake_band": {k: v for k, v in json.loads((DERIVED / "stake_band.json").read_text()).items()
                       if k in ("case", "passes", "detected", "duty_cycle_ci95", "plumes", "kt_ch4_per_yr",
                                "kt_co2e_per_yr_gwp100")},
        "demo_run": (json.loads((DERIVED / "demo_run.json").read_text()) if (DERIVED / "demo_run.json").exists()
                     else "run `make demo` first"),
        "sourceset_v2_root": json.loads((DERIVED / "sourceset_v2.json").read_text())["root"],
        "testnet_run": _testnet_numbers(),
        "adversarial": {k: v for k, v in json.loads((DERIVED / "adversarial.json").read_text()).items()
                        if k in ("n", "caught", "detection_rate", "by_layer")}
        if (DERIVED / "adversarial.json").exists() else "run `make adversarial` first",
        "measurements": _measurement_numbers(),
        "cleanruns": _optional("cleanruns.json", _cleanruns),
        "clean_passes": _clean_passes(),
        "floors": _optional("floors.json", _floors),
        "policy_v2": _optional("policy_v2.json", _policy_v2),
        "data_quality": _optional("data_quality.json", _data_quality),
        "backtest": _optional("backtest.json", _backtest),
        "projection": _optional("projection.json", _projection),
        "portfolio": {"sourceset_v3_root": _optional("sourceset_v3.json", lambda d: d["root"]),
                      "bond": _optional("model/bond_sizing_v3.json", _bond_v3)},
    }
    numbers["join_v1"]["window"] = "..".join(join["window"])
    refuse_missing(numbers)
    changes, had_previous = [], AUDIT.exists()
    if had_previous:
        old = dict(flat(json.loads(AUDIT.read_text()).get("numbers", {})))
        new = dict(flat(numbers))
        changes = [(k, old.get(k), new.get(k)) for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]
    # Keep the timestamp when nothing changed, so `make check` never dirties a tracked file (parallel lanes).
    stamp = now_utc() if (changes or not had_previous) else json.loads(AUDIT.read_text()).get("generatedAt", now_utc())
    write_json(AUDIT, {"generatedAt": stamp, "numbers": numbers})
    if changes:
        print(f"audit: {len(changes)} value(s) changed since the last run:")
        for k, a, b in changes:
            print(f"  {k}: {a} -> {b}")
        if os.environ.get("STRICT") == "1":
            sys.exit(1)
    else:
        print("audit: numbers written; no change vs previous run" if had_previous else "audit: written (first run)")


if __name__ == "__main__":
    main()
