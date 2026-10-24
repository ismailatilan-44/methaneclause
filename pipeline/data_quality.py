"""Data-quality flags and join sensitivity (the data-quality items of the spec). Committed inputs only.

Flags per plume set (de-duplicated by plume_id, twins counted once):
  hidden_emission           no published rate (`emission_auto` empty): the plume counts as a detection, no kg/h
  uncertainty_exceeds_rate  `emission_uncertainty_auto` > `emission_auto`: the rate's 1-sigma band includes zero
  plume_quality             Carbon Mapper's quality label, with its empty count
  emission_version          quantification version (Carbon Mapper re-quantifies; versions are pinned per bundle)
  processing_software       processing software version
Sets: every plume of the join neighbourhood (`cm_plumes.csv`), the join v1 candidate plumes (`join_v1.json`), and
each demo case's plumes within the facility radius (`cases/<case>.json`).

Join radius sensitivity: the frozen criterion re-run at 1.0 / 1.5 / 2.0 km, beside v1's 1.5 km. At 2.0 km the count is
a lower bound: plume records were fetched only for Carbon Mapper sources within 1.5 km of a target.

Join criterion hash: `join_v1.json` keeps its frozen bytes (the Sepolia covenant's policy commits to their SHA-256);
its recorded criterion hash is the freeze-time text, reported next to the current file's hash and the rules-section
hash (spec-v2 §15 "after B5").

    cd pipeline && .venv/bin/python data_quality.py     # -> data/derived/data_quality.json (in the STRICT audit)
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import pandas as pd

from common import DERIVED, RADIUS_KM, ROOT, sha256_file, write_json

RADII = [1.0, 1.5, 2.0]
PROVENANCE_OLD = ("Derived from [a research script, private path] plus the fixes asked for in [an internal review, "
                  "private path] (twin dedup, plume level).")
PROVENANCE_NEW = ("Derived from an exploratory join scan written during idea selection, plus the fixes an internal "
                  "security review asked for (twin de-duplication, plume-level matching).")
CRITERION = ROOT / "pipeline" / "join" / "CRITERION.md"


def flags(rows: list[dict]) -> dict:
    """Flags over plume rows (dicts with emission_auto, emission_uncertainty_auto, plume_quality, emission_version,
    processing_software); rows are de-duplicated by plume_id first."""
    seen, uniq = set(), []
    for r in rows:
        if r["plume_id"] not in seen:
            seen.add(r["plume_id"])
            uniq.append(r)

    def empty(v):
        return v is None or (isinstance(v, float) and v != v)

    rate = [r for r in uniq if not empty(r.get("emission_auto"))]
    return {
        "plumes": len(uniq),
        "hidden_emission": sum(1 for r in uniq if empty(r.get("emission_auto"))),
        "uncertainty_exceeds_rate": sum(1 for r in rate if not empty(r.get("emission_uncertainty_auto"))
                                        and r["emission_uncertainty_auto"] > r["emission_auto"]),
        "plume_quality": dict(sorted(Counter("(empty)" if empty(r.get("plume_quality")) else str(r["plume_quality"])
                                             for r in uniq).items())),
        "emission_version": dict(sorted(Counter("(empty)" if empty(r.get("emission_version"))
                                                else str(r["emission_version"]) for r in uniq).items())),
        "processing_software": dict(sorted(Counter("(empty)" if empty(r.get("processing_software"))
                                                   else str(r["processing_software"]) for r in uniq).items())),
    }


def quality_label(plumes: pd.DataFrame) -> dict:
    rows, empty = len(plumes), int(plumes["plume_quality"].isna().sum())
    sentence = (f"Carbon Mapper's API v1 returned no plume_quality label (empty in all {rows:,} rows), so no quality "
                "filter is applied." if rows == empty else
                f"Carbon Mapper's plume_quality label is empty in {empty:,} of {rows:,} rows.")
    return {"rows": rows, "emptyRows": empty, "sentence": sentence}


def radius_sensitivity() -> dict:
    from join.join import compute, inputs

    mars_all, cm_src, plumes, as_of = inputs()
    fetched = set(plumes["source_name"])
    rows = []
    for r in RADII:
        c, _ = compute(mars_all, cm_src, plumes, as_of, radius_km=r)
        q = c.pop("_q")
        in_scope = set(q["source_name"]) if len(q) else set()
        note = {1.0: "subset of v1's neighbourhood", 1.5: "v1 (frozen criterion)",
                2.0: "lower bound: plume records were fetched only for Carbon Mapper sources within 1.5 km of a "
                     "target, so sources between 1.5 and 2.0 km add no plumes"}[r]
        from join.join import mars_target, nearest

        mt = mars_target(mars_all).sort_values("source_name").reset_index(drop=True)
        _, d = nearest(mt, cm_src["source_latitude"].to_numpy(), cm_src["source_longitude"].to_numpy())
        owned = set(cm_src.loc[d <= r, "source_name"])
        rows.append({"radiusKm": r, "n_candidates": c["n_candidates"], "n_candidate_plumes": c["n_candidate_plumes"],
                     "n_cm_sources_within_radius": c["n_cm_sources_within_radius"],
                     "n_cm_sources_without_plume_records": len(owned - fetched),
                     "n_candidate_cm_sources": len(in_scope), "note": note})
    return {"criterion": "pipeline/join/CRITERION.md rules 1-6 with the radius as the only parameter",
            "rows": rows}


def main() -> None:
    plumes = pd.read_csv(DERIVED / "cm_plumes.csv")
    join = json.loads((DERIVED / "join_v1.json").read_text())
    cand_rows = [{"plume_id": p["plume_id"], "emission_auto": p["emission_kgh"],
                  "emission_uncertainty_auto": p["uncertainty_kgh"], "emission_version": p["emission_version"],
                  "plume_quality": None, "processing_software": None}
                 for c in join["candidates"] for p in c["plumes"]]
    cases = {}
    for f in sorted((DERIVED / "cases").glob("*_S_*.json")):
        if f.name.count(".") > 1:
            continue
        cases[f.stem] = flags(json.loads(f.read_text())["cm_plumes_within_radius"])
    current = hashlib.sha256(CRITERION.read_bytes()).hexdigest()
    recorded = join["summary"]["criterion_sha256"]
    inputs = [DERIVED / "cm_plumes.csv", DERIVED / "cm_sources.csv", DERIVED / "mars_sources.csv",
              DERIVED / "join_v1.json", ROOT / "data" / "MANIFEST.json", CRITERION, Path(__file__).resolve()]
    out = {
        "schema": "methaneclause.data_quality/1",
        "generatedFrom": [{"path": str(p.relative_to(ROOT)), "sha256": sha256_file(p)} for p in inputs],
        "definitions": __doc__.split("Flags per plume set")[1].split("Join radius sensitivity")[0].strip(),
        "neighbourhood": flags(plumes.to_dict("records")),
        "joinCandidates": {**{k: v for k, v in flags(cand_rows).items()
                              if k not in ("plume_quality", "processing_software")},
                           "note": "join_v1.json keeps rate, uncertainty and emission_version per candidate plume; "
                                   "plume_quality and processing_software are counted on the neighbourhood set only"},
        "cases": cases,
        "joinRadiusSensitivity": radius_sensitivity(),
        "joinCriterionHash": {
            "recordedInJoin": recorded, "currentFile": current, "equal": recorded == current,
            "freezeTimeText": True,
            "rulesSectionSha256": hashlib.sha256(CRITERION.read_text().split("## Rules", 1)[1].encode()).hexdigest(),
            "why": "join_v1.json keeps its frozen bytes because the Sepolia covenant's policy commits to its SHA-256 "
                   "(spec-v2 §15 'after B5'); the recorded criterion hash is the freeze-time text. A later edit changed "
                   "only the provenance sentence; rules 1-6 are byte-identical (see pipeline/join/README.md)",
            "provenanceAtFreeze": PROVENANCE_OLD, "provenanceNow": PROVENANCE_NEW},
        "plumeQualityLabel": quality_label(plumes),
        "v1RadiusKm": RADIUS_KM,
    }
    write_json(DERIVED / "data_quality.json", out)
    n, j = out["neighbourhood"], out["joinCandidates"]
    print(f"data quality: {n['plumes']} neighbourhood plumes ({n['hidden_emission']} hidden rate, "
          f"{n['uncertainty_exceeds_rate']} uncertainty > rate); {j['plumes']} candidate plumes "
          f"({j['hidden_emission']} hidden, {j['uncertainty_exceeds_rate']} unc > rate); radius "
          + ", ".join(f"{r['radiusKm']} km -> {r['n_candidates']}" for r in out["joinRadiusSensitivity"]["rows"]))


if __name__ == "__main__":
    main()
