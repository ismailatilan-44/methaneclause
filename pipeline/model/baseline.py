"""Frozen rule baselines for the next-pass re-detection forecast (spec §5). No ML here.

Task: P(detection on Carbon Mapper overpass t of source s | history strictly before t).
Labelled dataset: one row per (source, observation date); label = date is in detection_dates.
Sample: the Carbon Mapper sources fetched by fetch_cm.py (the join neighbourhood, ~2,000), capped at MAX_SOURCES by
a deterministic hash order.

  B1  persistence ratio P = D/M over the trailing 6 months (regulator-style, as MARS); M = 0 -> train base rate.
  B2  MARS-label rule: trailing 6 months observed with no detection ("absent") -> 0; M = 0 ("undetermined") ->
      train base rate; otherwise -> train detection rate of non-absent rows.
  B0  climatology (train base rate) — reference only.
  B1s smoothed persistence (D + a) / (M + b), a and b fitted on train by Brier (added at split v2, see below). It is
      still a rule; it removes B1's hard zero after a clean six months.

Sample (split v2, re-frozen 2026-10-14 after the D3 review): rows where the source already had >= 1 detection before
the pass. Every Carbon Mapper catalogue source exists because it was detected at least once, so the passes before a
source's first detection carry a guaranteed future positive (in v1, 11 % of test_b had label rate 0.64). Those rows
never occur in the product: a covenant covers catalogued, already-detected sources. History features still use the
full history, including the excluded rows. v1 (splits_hash 0x9adbc5e6…) stays in git history.

Splits (frozen, hashed in data/derived/model/splits.json):
  train   date < 2026-01-01 and country not in {Turkmenistan, Algeria}
  test_a  country in {Turkmenistan, Algeria}, all dates
  test_b  date >= 2026-01-01, all countries
Country of a Carbon Mapper source = country of the nearest MARS source (any sector) within 50 km, else "UNK".
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from canon import evidence_hash  # noqa: E402
from common import DERIVED, haversine_km, minus_months, write_json  # noqa: E402

OUT = DERIVED / "model"
MAX_SOURCES = 2000
SPLIT_DATE = "2026-01-01"
HELD_OUT_COUNTRIES = ["Algeria", "Turkmenistan"]
TRAIL_MONTHS = 6
COUNTRY_KM = 50.0
LOW = 0.05  # "forecast says ~zero" threshold for the false-zero rate


def assign_country(tl: dict) -> dict[str, str]:
    mars = pd.read_csv(DERIVED / "mars_sources.csv")
    mla, mlo, mc = mars["lat"].to_numpy(), mars["lon"].to_numpy(), mars["country"].to_numpy()
    out = {}
    for n, t in tl.items():
        d = haversine_km(t["lat"], t["lon"], mla, mlo)
        i = int(np.argmin(d))
        out[n] = str(mc[i]) if d[i] <= COUNTRY_KM else "UNK"
    return out


def build_labels() -> pd.DataFrame:
    tl = json.loads((DERIVED / "cm_timelines.json").read_text())
    names = sorted(tl, key=lambda n: hashlib.sha256(n.encode()).hexdigest())[:MAX_SOURCES]
    tl = {n: tl[n] for n in sorted(names)}
    country = assign_country(tl)
    rows = []
    for n, t in tl.items():
        det = set(t["det"])
        obs = t["obs"]
        dates = [o[0] for o in obs]
        for i, (day, instr) in enumerate(obs):
            lo = minus_months(day, TRAIL_MONTHS)
            prior = [x for x in dates[:i] if x >= lo]  # strictly before t, within trailing window
            rows.append({"source_name": n, "date": day, "instrument": instr, "country": country[n],
                         "M": len(prior), "D": sum(x in det for x in prior),
                         "n_obs_before": i, "n_det_before": sum(x in det for x in dates[:i]),
                         "label": int(day in det)})
    return pd.DataFrame(rows).sort_values(["source_name", "date"]).reset_index(drop=True)


def split_masks(df: pd.DataFrame) -> dict[str, pd.Series]:
    held = df["country"].isin(HELD_OUT_COUNTRIES)
    catalogued = df["n_det_before"] > 0  # split v2 sample rule (module docstring)
    return {"train": (df["date"] < SPLIT_DATE) & ~held & catalogued, "test_a": held & catalogued,
            "test_b": (df["date"] >= SPLIT_DATE) & catalogued}


def fit_smoothing(train: pd.DataFrame) -> tuple[float, float]:
    """(a, b) of (D + a) / (M + b) minimising train Brier on a fixed grid."""
    M, D, y = train["M"].to_numpy(), train["D"].to_numpy(), train["label"].to_numpy()
    best = (np.inf, 0.0, 1.0)
    for a in np.arange(0.05, 3.001, 0.05):
        for b in np.arange(0.1, 10.001, 0.1):
            br = float(np.mean(((D + a) / (M + b) - y) ** 2))
            if br < best[0]:
                best = (br, float(a), float(b))
    return round(best[1], 2), round(best[2], 2)


def predict(df: pd.DataFrame, base: float, rate_present: float,
            smooth: tuple[float, float] | None = None) -> dict[str, np.ndarray]:
    M, D = df["M"].to_numpy(), df["D"].to_numpy()
    b1 = np.where(M > 0, D / np.maximum(M, 1), base)
    b2 = np.where(M == 0, base, np.where(D == 0, 0.0, rate_present))
    out = {"B1_persistence": b1, "B2_mars_absent_zero": b2, "B0_climatology": np.full(len(df), base)}
    if smooth:
        out["B1s_smoothed"] = (D + smooth[0]) / (M + smooth[1])
    return out


def metrics(y: np.ndarray, p: np.ndarray, ref_brier: float | None = None) -> dict:
    brier = float(np.mean((p - y) ** 2))
    low = p < LOW
    out = {"n": int(len(y)), "positives": int(y.sum()), "brier": round(brier, 4),
           "auc": round(float(roc_auc_score(y, p)), 4) if 0 < y.sum() < len(y) else None,
           "n_forecast_below_5pct": int(low.sum()),
           "false_zero_rate": round(float(y[low].mean()), 4) if low.any() else None}
    if ref_brier:
        out["bss_vs_B1"] = round(1 - brier / ref_brier, 4)
    return out


def main() -> None:
    df = build_labels()
    masks = split_masks(df)
    excl = df["n_det_before"] == 0  # rows the v2 sample rule drops: passes before the source's first detection
    v1_test_b = df["date"] >= SPLIT_DATE
    excluded = {"rule": "n_det_before == 0 (pass before the source's first catalogue detection)",
                "n_rows_total": int(excl.sum()), "label_rate_total": round(float(df.loc[excl, "label"].mean()), 4),
                "v1_test_b_rows": int((excl & v1_test_b).sum()),
                "v1_test_b_share": round(float((excl & v1_test_b).sum() / v1_test_b.sum()), 4),
                "v1_test_b_label_rate": round(float(df.loc[excl & v1_test_b, "label"].mean()), 4)}
    tr = df[masks["train"]]
    base = float(tr["label"].mean())
    present = tr[(tr["M"] > 0) & (tr["D"] > 0)]
    rate_present = float(present["label"].mean())
    smooth = fit_smoothing(tr)

    row_id = (df["source_name"] + "|" + df["date"]).to_numpy()
    labels_sha = hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()
    splits = {
        "version": "v2",
        "frozen": "v1 2026-10-14 (D1), before any model training; v2 2026-10-14 after the D3 review: sample rule "
                  "n_det_before > 0 (sample-construction leak in v1) and the B1s smoothing constants",
        "v1_splits_hash": "0x9adbc5e648b1a4e19c9edb22e5053bd15c9a9a1b40889e007dfa2da79aff8a68",
        "definition": {"train": f"date < {SPLIT_DATE} and country not in {HELD_OUT_COUNTRIES}",
                       "test_a": f"country in {HELD_OUT_COUNTRIES}, all dates",
                       "test_b": f"date >= {SPLIT_DATE}, all countries",
                       "row": "(Carbon Mapper source, observation date); label = date in detection_dates",
                       "history": f"strictly before the row date; trailing window {TRAIL_MONTHS} calendar months",
                       "country": f"nearest MARS source (any sector) within {COUNTRY_KM:g} km, else UNK",
                       "sample": f"cm_timelines.json sources, first {MAX_SOURCES} by sha256(name); rows with "
                                 "n_det_before > 0 only (history features use all rows)"},
        "cm_timelines_sha256": hashlib.sha256((DERIVED / "cm_timelines.json").read_bytes()).hexdigest(),
        "labels_sha256": labels_sha,
        "n_sources": int(df["source_name"].nunique()), "n_rows": int(len(df)),
        "splits": {k: {"n_rows": int(m.sum()), "n_pos": int(df.loc[m, "label"].sum()),
                       "n_sources": int(df.loc[m, "source_name"].nunique()),
                       "row_ids_sha256": hashlib.sha256("\n".join(sorted(row_id[m.to_numpy()])).encode()).hexdigest()}
                   for k, m in masks.items()},
        "frozen_rule_constants": {"train_base_rate": round(base, 6), "train_rate_non_absent": round(rate_present, 6),
                                  "B1s_a": smooth[0], "B1s_b": smooth[1]},
    }
    splits["splits_hash"] = evidence_hash(splits)
    splits["excluded_by_v2_sample_rule"] = {**excluded, "note": "descriptive; not part of splits_hash"}
    write_json(OUT / "splits.json", splits)

    res = {"splits_hash": splits["splits_hash"], "labels_sha256": labels_sha, "results": {}}
    for k, m in masks.items():
        sub = df[m.to_numpy()]
        preds = predict(sub, base, rate_present, smooth)
        y = sub["label"].to_numpy()
        b1 = float(np.mean((preds["B1_persistence"] - y) ** 2))
        res["results"][k] = {name: metrics(y, p, None if name == "B1_persistence" else b1) for name, p in preds.items()}
    write_json(OUT / "baseline_metrics.json", res)
    print(f"labels: {len(df)} rows, {df.source_name.nunique()} sources, base rate {base:.3f}; splits_hash {splits['splits_hash'][:18]}…")
    for k, r in res["results"].items():
        print(k, {n: (v["brier"], v.get("bss_vs_B1"), v["false_zero_rate"]) for n, v in r.items()}, "n=", r["B1_persistence"]["n"])


if __name__ == "__main__":
    main()
