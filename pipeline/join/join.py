"""MARS x Carbon Mapper join, frozen criterion v1 (see CRITERION.md).

Inputs: data/derived/{mars_sources.csv, mars_plumes_og.csv, cm_sources.csv, cm_plumes.csv}, data/MANIFEST.json
Output: data/derived/join_v1.json
"""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import (DERIVED, JOIN_VERSION, RADIUS_KM, WINDOW_MONTHS, haversine_km, load_manifest,  # noqa: E402
                    mars_target, minus_months, write_json)

CRITERION = Path(__file__).with_name("CRITERION.md")


def nearest(mars: pd.DataFrame, lat: np.ndarray, lon: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Index (into mars) and distance of the nearest MARS target source for each point."""
    best_d = np.full(len(lat), np.inf)
    best_i = np.full(len(lat), -1)
    for i, (mlat, mlon) in enumerate(zip(mars["lat"].to_numpy(), mars["lon"].to_numpy())):
        d = haversine_km(mlat, mlon, lat, lon)
        upd = d < best_d  # strict: ties keep the earlier (alphabetical) MARS source
        best_d[upd], best_i[upd] = d[upd], i
    return best_i, best_d


def _plain(o):
    return o.item() if hasattr(o, "item") else str(o)


def keep_or_refuse(out_path: Path, summary: dict, cands: list[dict]) -> bool:
    """Guard for the frozen output: True = an existing file has the same counts and candidates (only the criterion
    hash may differ) and is kept byte for byte; False = no file yet (the caller writes it); a difference raises
    SystemExit (a changed criterion or input is a new join version, never an edit of v1)."""
    if not out_path.exists():
        return False
    frozen = json.loads(out_path.read_text())
    same = json.loads(json.dumps({"summary": summary, "candidates": cands}, default=_plain))

    def body(x):
        return {k: v for k, v in x["summary"].items() if k != "criterion_sha256"}

    if body(frozen) == body(same) and frozen["candidates"] == same["candidates"]:
        return True
    raise SystemExit(f"{out_path.name} differs from a re-run: a changed criterion or input is a new join version (v2), "
                     "never an edit of v1; write it under a new name")


def inputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, str]:
    """The committed derived inputs and MARS's as-of date (from data/MANIFEST.json)."""
    man = load_manifest()
    as_of = man["files"]["mars_zip"]["mars_as_of_tile_date"][:10]
    return (pd.read_csv(DERIVED / "mars_sources.csv"), pd.read_csv(DERIVED / "cm_sources.csv"),
            pd.read_csv(DERIVED / "cm_plumes.csv"), as_of)


def compute(mars_all: pd.DataFrame, cm_src: pd.DataFrame, plumes: pd.DataFrame, as_of: str, *,
            radius_km: float = RADIUS_KM, window_months: int = WINDOW_MONTHS) -> tuple[dict, list[dict]]:
    """The frozen criterion as a pure function (rules 1-6 of CRITERION.md); `radius_km` is a parameter only for the
    sensitivity table, v1 is RADIUS_KM. Returns (counts, candidates)."""
    win_start = minus_months(as_of, window_months)
    mars = mars_target(mars_all).sort_values("source_name").reset_index(drop=True)
    mars["last_plume_day"] = mars["last_plume_date"].str[:10]
    cm_src = cm_src.copy()

    # (1) source-level neighbourhood: each CM source owned by its nearest MARS target within radius_km
    si, sd = nearest(mars, cm_src["source_latitude"].to_numpy(), cm_src["source_longitude"].to_numpy())
    cm_src["mars_idx"], cm_src["dist_km"] = si, sd
    owned = cm_src[cm_src["dist_km"] <= radius_km]

    # (2) plume level: dedup by plume_id (250 m / 1000 m twins list the same plume ids), keep plumes of
    #     owned sources, assign each plume to the MARS target nearest to the plume origin, require origin
    #     within radius_km, after MARS last_plume_date, inside [as_of - 6 months, as_of].
    p = plumes[plumes["source_name"].isin(owned["source_name"])].copy()
    p = p.dropna(subset=["lat", "lon", "scene_timestamp"])
    twin_rows = len(p)
    p = p.sort_values(["plume_id", "source_name"]).drop_duplicates("plume_id", keep="first")
    twin_dupes = twin_rows - len(p)
    if len(mars) == 0 or len(p) == 0:
        q = p.iloc[0:0].assign(mars_source=[], day=[])
    else:
        pi, pd_ = nearest(mars, p["lat"].to_numpy(), p["lon"].to_numpy())
        p["mars_idx"], p["origin_dist_km"] = pi, pd_
        p["day"] = p["scene_timestamp"].str[:10]
        p["mars_source"] = mars["source_name"].to_numpy()[p["mars_idx"]]
        p["mars_last"] = mars["last_plume_day"].to_numpy()[p["mars_idx"]]
        q = p[(p["origin_dist_km"] <= radius_km) & (p["day"] > p["mars_last"])
              & (p["day"] >= win_start) & (p["day"] <= as_of)]

    cands = []
    for ms, g in q.groupby("mars_source", sort=True):
        m = mars[mars["source_name"] == ms].iloc[0]
        rates = g["emission_auto"].dropna()
        cands.append({
            "mars_source": ms, "country": m["country"], "source_type": m["source_type"],
            "mars_lat": m["lat"], "mars_lon": m["lon"], "mars_persistency_category": m["persistency_category"],
            "mars_last_plume_date": m["last_plume_date"], "mars_n_plumes_detected": int(m["n_plumes_detected"]),
            "mars_feedback": m["feedback"],
            "cm_sources": sorted(g["source_name"].unique()),
            "n_plumes": len(g), "n_detection_days": g["day"].nunique(),
            "instruments": dict(sorted(Counter(g["instrument"]).items())),
            "max_emission_kgh": round(float(rates.max()), 1) if len(rates) else None,
            "n_hidden_emission": int(g["emission_auto"].isna().sum()),
            "plumes": [{"plume_id": r.plume_id, "cm_source": r.source_name, "scene_timestamp": r.scene_timestamp,
                        "instrument": r.instrument,
                        "emission_kgh": None if pd.isna(r.emission_auto) else round(float(r.emission_auto), 1),
                        "uncertainty_kgh": None if pd.isna(r.emission_uncertainty_auto)
                        else round(float(r.emission_uncertainty_auto), 1),
                        "emission_version": r.emission_version, "origin_dist_km": round(float(r.origin_dist_km), 3)}
                       for r in g.sort_values(["scene_timestamp", "plume_id"]).itertuples()],
        })

    counts = {
        "window": [win_start, as_of], "n_mars_target_sources": len(mars),
        "n_cm_sources_within_radius": int(len(owned)),
        "n_mars_targets_with_cm_source": int(owned["mars_idx"].nunique()),
        "n_twin_duplicate_plume_rows_removed": int(twin_dupes),
        "n_candidates": len(cands), "n_candidate_plumes": int(len(q)),
        "n_candidate_cm_sources": int(q["source_name"].nunique()),
        "n_candidate_detection_days": int(sum(c["n_detection_days"] for c in cands)),
        "_q": q,
    }
    return counts, cands


def main() -> None:
    man = load_manifest()
    mars_all, cm_src, plumes, as_of = inputs()
    counts, cands = compute(mars_all, cm_src, plumes, as_of)
    q, win_start = counts.pop("_q"), counts["window"][0]
    peaks = [c["max_emission_kgh"] for c in cands if c["max_emission_kgh"] is not None]
    summary = {
        "join_version": JOIN_VERSION,
        "criterion_sha256": hashlib.sha256(CRITERION.read_bytes()).hexdigest(),
        "mars_zip_sha256": man["files"]["mars_zip"]["sha256"],
        "cm_sources_csv_sha256": man["files"]["cm_sources_csv"]["sha256"],
        "cm_source_responses_aggregate_sha256": man["cm_source_responses"]["aggregate_sha256"],
        "mars_as_of": as_of, "window": [win_start, as_of], "radius_km": RADIUS_KM,
        **{k: counts[k] for k in ("n_mars_target_sources", "n_cm_sources_within_radius",
                                  "n_mars_targets_with_cm_source", "n_twin_duplicate_plume_rows_removed",
                                  "n_candidates", "n_candidate_plumes", "n_candidate_cm_sources",
                                  "n_candidate_detection_days")},
        "candidates_by_country": dict(Counter(c["country"] for c in cands).most_common()),
        "candidate_plumes_by_instrument": dict(sorted(Counter(q["instrument"]).items())),
        "candidates_with_tanager": sum(1 for c in cands if "tan" in c["instruments"]),
        "candidates_peak_below_735kgh": sum(1 for x in peaks if x < 735),
        "candidates_peak_ge_1000kgh": sum(1 for x in peaks if x >= 1000),
    }
    out_path = DERIVED / "join_v1.json"
    if keep_or_refuse(out_path, summary, cands):
        print("join_v1.json: frozen bytes kept (same counts and candidates; the Sepolia policy commits to its "
              "SHA-256, see pipeline/join/README.md)")
        return
    write_json(out_path, {"summary": summary, "candidates": cands})
    print({k: v for k, v in summary.items() if not k.endswith("sha256")})


if __name__ == "__main__":
    main()
