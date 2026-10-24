"""Next-pass re-detection forecast vs the frozen B1 persistence rule (spec §5). D3; never triggers a payout.

Same rows, labels and frozen splits as baseline.py (splits_hash is checked, not recomputed). Features use history
strictly before the row date. Hyperparameters are fixed below and were not tuned on test_a/test_b.

Decision rule (pre-registered in spec §5, made operational here before the first run):
  keep the model  iff  on BOTH held-out sets  BSS vs B1 > 0  AND  ECE(model) <= ECE(B1)   (10 equal-width bins)
otherwise the product sizes bonds with the (smoothed) persistence rule and the README says "we measured it; the
persistence rule is enough". Result on split v2: RULE_IS_ENOUGH (MODEL_CARD.md). Also reported, never used for the
decision: B1s (smoothed rule), an ablation without the next-pass instrument, per-country breakdowns, and BSS CIs from
a bootstrap over ~2 km location cells.

Excluded on purpose: MARS persistency_category. The MARS file is a 2026 snapshot computed over all history, so on a
2023 training row it would leak the future.

Writes data/derived/model/model_metrics.json (and model.pkl, gitignored; its SHA-256 is in the metrics file).
"""
from __future__ import annotations

import hashlib
import json
import pickle
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from canon import evidence_hash  # noqa: E402
from common import DERIVED, write_json  # noqa: E402
from model.baseline import HELD_OUT_COUNTRIES, LOW, build_labels, metrics, predict, split_masks  # noqa: E402

OUT = DERIVED / "model"
SEED = 0
INSTRUMENTS = ["emi", "tan", "GAO", "ang", "av3"]  # anything else (combined passes) -> "multi"
FEATURES = ["P_6m", "M_6m", "D_6m", "n_obs_before", "n_det_before", "life_ratio", "days_since_last_det",
            "days_since_last_obs", "prev_detected", "last_rate_log", "instr_next", "abs_lat", "month_sin",
            "month_cos", "cluster_250m"]
CATEGORICAL = ["instr_next"]
PARAMS = {"learning_rate": 0.05, "max_iter": 300, "max_leaf_nodes": 15, "min_samples_leaf": 40,
          "l2_regularization": 1.0, "early_stopping": False, "random_state": SEED}
N_BINS = 10
N_BOOT = 500  # cluster bootstrap for BSS confidence intervals
CELL_DEG = 0.02  # ~2 km location cell = bootstrap cluster


def _days(a: str, b: str) -> int:
    return (date.fromisoformat(a) - date.fromisoformat(b)).days


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    tl = json.loads((DERIVED / "cm_timelines.json").read_text())
    pl = pd.read_csv(DERIVED / "cm_plumes.csv", usecols=["source_name", "scene_timestamp", "emission_auto"])
    pl["date"] = pl["scene_timestamp"].str[:10]
    rate = pl.groupby(["source_name", "date"])["emission_auto"].max().to_dict()  # NaN if never quantified

    feats = []
    for n, g in df.groupby("source_name", sort=False):
        t = tl[n]
        det = set(t["det"])
        last_det, last_obs, prev, last_rate = None, None, np.nan, np.nan
        for day in g["date"]:  # rows are sorted by date within a source; state = history strictly before `day`
            feats.append({
                "days_since_last_det": _days(day, last_det) if last_det else np.nan,
                "days_since_last_obs": _days(day, last_obs) if last_obs else np.nan,
                "prev_detected": prev,
                "last_rate_log": np.log1p(last_rate) if last_rate == last_rate else np.nan,
                "abs_lat": abs(t["lat"]),
                "cluster_250m": int("_250m_" in n),
            })
            last_obs, prev = day, float(day in det)
            if day in det:
                last_det = day
                last_rate = rate.get((n, day), np.nan)
    f = pd.DataFrame(feats, index=df.index)
    out = pd.concat([df, f], axis=1)
    out["P_6m"] = np.where(out["M"] > 0, out["D"] / out["M"].clip(lower=1), np.nan)
    out["M_6m"], out["D_6m"] = out["M"], out["D"]
    out["life_ratio"] = np.where(out["n_obs_before"] > 0, out["n_det_before"] / out["n_obs_before"].clip(lower=1),
                                 np.nan)
    instr = out["instrument"].where(out["instrument"].isin(INSTRUMENTS), "multi")
    out["instr_next"] = pd.Categorical(instr, categories=INSTRUMENTS + ["multi"]).codes
    month = out["date"].str[5:7].astype(int)
    out["month_sin"], out["month_cos"] = np.sin(2 * np.pi * month / 12), np.cos(2 * np.pi * month / 12)
    return out


def reliability(y: np.ndarray, p: np.ndarray) -> tuple[float, list[dict]]:
    """ECE over 10 equal-width bins + the table (mean forecast vs observed frequency per bin)."""
    idx = np.minimum((p * N_BINS).astype(int), N_BINS - 1)
    table, ece = [], 0.0
    for b in range(N_BINS):
        m = idx == b
        if not m.any():
            continue
        mp, my = float(p[m].mean()), float(y[m].mean())
        ece += m.sum() / len(y) * abs(mp - my)
        table.append({"bin": f"{b / N_BINS:.1f}-{(b + 1) / N_BINS:.1f}", "n": int(m.sum()),
                      "mean_forecast": round(mp, 4), "observed": round(my, 4)})
    return round(float(ece), 4), table


def bss_ci(src: np.ndarray, y: np.ndarray, p: np.ndarray, ref: np.ndarray) -> list[float]:
    """95% CI of BSS of p vs ref, resampling whole clusters (location cells)."""
    rng = np.random.default_rng(SEED)
    codes, inv = np.unique(src, return_inverse=True)
    se_p = np.bincount(inv, (p - y) ** 2, len(codes))
    se_r = np.bincount(inv, (ref - y) ** 2, len(codes))
    draws = rng.integers(0, len(codes), size=(N_BOOT, len(codes)))
    bss = 1 - se_p[draws].sum(1) / se_r[draws].sum(1)
    return [round(float(x), 4) for x in np.percentile(bss, [2.5, 97.5])]


def fit_predict(X: np.ndarray, y: np.ndarray, tr: np.ndarray, features: list[str]) -> np.ndarray:
    cat = [features.index(c) for c in CATEGORICAL if c in features]
    clf = HistGradientBoostingClassifier(categorical_features=cat or None, **PARAMS)
    return clf.fit(X[tr], y[tr]).predict_proba(X)[:, 1]


def main() -> None:
    frozen = json.loads((OUT / "splits.json").read_text())
    df = build_labels()
    labels_sha = hashlib.sha256(df.to_csv(index=False).encode()).hexdigest()
    if labels_sha != frozen["labels_sha256"]:
        sys.exit(f"labels changed since the split was frozen ({labels_sha[:12]} != {frozen['labels_sha256'][:12]});"
                 " re-freeze with baseline.py first and say so")
    masks = split_masks(df)
    df = add_features(df)
    X = df[FEATURES].to_numpy(dtype=float)
    y = df["label"].to_numpy()
    tr = masks["train"].to_numpy()

    try:  # footprint is reported, never required: a missing/failing codecarbon must not block reproduction
        from codecarbon import OfflineEmissionsTracker

        tracker = OfflineEmissionsTracker(country_iso_code="TUR", log_level="error", save_to_file=False)
        tracker.start()
    except Exception as e:  # noqa: BLE001
        print(f"codecarbon unavailable ({e}); footprint not measured")
        tracker = None
    clf = HistGradientBoostingClassifier(categorical_features=[FEATURES.index(c) for c in CATEGORICAL], **PARAMS)
    clf.fit(X[tr], y[tr])
    p_model = clf.predict_proba(X)[:, 1]
    kg = (tracker.stop() or 0.0) if tracker else None

    # Ablation (reported, not part of the decision): same model without the next-pass instrument, which B1 never sees.
    feats_abl = [f for f in FEATURES if f != "instr_next"]
    p_abl = fit_predict(df[feats_abl].to_numpy(dtype=float), y, tr, feats_abl)

    blob = pickle.dumps(clf, protocol=5)
    (OUT / "model.pkl").write_bytes(blob)
    spec = {"features": FEATURES, "categorical": CATEGORICAL, "instruments": INSTRUMENTS, "params": PARAMS,
            "splits_hash": frozen["splits_hash"], "labels_sha256": labels_sha, "sklearn": __import__("sklearn")
            .__version__}
    feature_spec_hash = evidence_hash(spec)
    model_sha = "0x" + hashlib.sha256(blob).hexdigest()
    # Prediction fingerprint: re-running must reproduce it even if pickle bytes differ across platforms.
    pred_hash = "0x" + hashlib.sha256(np.round(p_model, 8).tobytes()).hexdigest()
    model_hash = evidence_hash({"featureSpecHash": feature_spec_hash, "predictionsSha256": pred_hash})

    rc = frozen["frozen_rule_constants"]
    rules = predict(df, rc["train_base_rate"], rc["train_rate_non_absent"], (rc["B1s_a"], rc["B1s_b"]))
    b1_all, b1s_all = rules["B1_persistence"], rules["B1s_smoothed"]
    # Bootstrap clusters = ~2 km location cells: twin 250 m / 1000 m sources at one site are not independent.
    tl = json.loads((DERIVED / "cm_timelines.json").read_text())
    cell = {n: f"{round(t['lat'] / CELL_DEG)}_{round(t['lon'] / CELL_DEG)}" for n, t in tl.items()}
    cells_all = df["source_name"].map(cell).to_numpy()

    def evaluate(mm: np.ndarray) -> dict:
        yk, b1, b1s = y[mm], b1_all[mm], b1s_all[mm]
        cl = cells_all[mm]
        brier_b1 = float(np.mean((b1 - yk) ** 2))
        out_k = {"n_sources": int(df.loc[mm, "source_name"].nunique()), "n_cells": int(len(set(cl)))}
        for name, p in (("B1_persistence", b1), ("B1s_smoothed", b1s), ("model", p_model[mm]),
                        ("ablation_no_instrument", p_abl[mm])):
            ece, rel = reliability(yk, p)
            r = {**metrics(yk, p, None if name == "B1_persistence" else brier_b1), "ece": ece}
            if name != "B1_persistence":
                r["bss_ci95_cell_bootstrap"] = bss_ci(cl, yk, p, b1)
            if name in ("B1_persistence", "model", "B1s_smoothed"):
                r["reliability"] = rel
            out_k[name] = r
        m = out_k["model"]
        m["bss_vs_B1s"] = round(1 - m["brier"] / out_k["B1s_smoothed"]["brier"], 4)
        m["bss_vs_B1s_ci95_cell_bootstrap"] = bss_ci(cl, yk, p_model[mm], b1s)
        return out_k

    res, keep = {}, True
    for k, m in masks.items():
        mm = m.to_numpy()
        res[k] = evaluate(mm)
        if k != "train":
            r = res[k]
            r["passes"] = bool(r["model"]["bss_vs_B1"] > 0 and r["model"]["ece"] <= r["B1_persistence"]["ece"])
            keep &= r["passes"]
    # Breakdowns (reported, not part of the decision): each held-out country alone, and test_b without the
    # test_a countries (53 % of test_b rows are Turkmenistan/Algeria 2026 rows that are also in test_a).
    held = df["country"].isin(HELD_OUT_COUNTRIES).to_numpy()
    breakdown = {f"test_a_{c}": evaluate(masks["test_a"].to_numpy() & (df["country"] == c).to_numpy())
                 for c in HELD_OUT_COUNTRIES}
    breakdown["test_b_without_test_a_countries"] = evaluate(masks["test_b"].to_numpy() & ~held)

    out = {
        "task": "P(detection on the next Carbon Mapper overpass | history strictly before it)",
        "decision_rule": "keep iff BSS vs B1 > 0 and ECE(model) <= ECE(B1) on both test_a and test_b",
        "decision": "KEEP_MODEL" if keep else "RULE_IS_ENOUGH",
        "splits_hash": frozen["splits_hash"], "labels_sha256": labels_sha,
        "featureSpec": spec, "featureSpecHash": feature_spec_hash, "modelFileSha256": model_sha,
        "predictionsSha256": pred_hash, "modelHash": model_hash,
        "false_zero_threshold": LOW,
        "feature_importance_note": "not computed (HGB has no native importances; permutation importance is D5+)",
        "footprint": {"tool": f"codecarbon {__import__('codecarbon').__version__} offline, country TUR",
                      "train_plus_inference_kgCO2e": None if kg is None else round(float(kg), 8)},
        "results": res,
        "breakdown": breakdown,
        "notes": ["B1s_smoothed (D+a)/(M+b) is a rule with two constants fitted on train; it is reported next to the "
                  "model but the pre-registered decision compares with B1",
                  "ablation_no_instrument is reported, never used to choose the model (that would select on test)"],
    }
    write_json(OUT / "model_metrics.json", out)
    print(f"decision: {out['decision']}   modelHash {model_hash[:18]}…   footprint {'n/a' if kg is None else f'{kg * 1000:.4f} gCO2e'}")
    show = {**{k: res[k] for k in ("test_a", "test_b", "train")}, **breakdown}
    for k, r in show.items():
        m = r["model"]
        print(f"  {k:34s} n={m['n']:5d} cells={r['n_cells']:4d}  Brier B1 {r['B1_persistence']['brier']:.4f} "
              f"B1s {r['B1s_smoothed']['brier']:.4f} model {m['brier']:.4f} | BSS model vs B1 {m['bss_vs_B1']:+.4f} "
              f"{m['bss_ci95_cell_bootstrap']}  vs B1s {m['bss_vs_B1s']:+.4f} {m['bss_vs_B1s_ci95_cell_bootstrap']} | "
              f"B1s vs B1 {r['B1s_smoothed']['bss_vs_B1']:+.4f} {r['B1s_smoothed']['bss_ci95_cell_bootstrap']} | "
              f"ECE B1 {r['B1_persistence']['ece']} B1s {r['B1s_smoothed']['ece']} model {m['ece']}"
              + (f"  passes={r['passes']}" if "passes" in r else ""))


if __name__ == "__main__":
    main()
