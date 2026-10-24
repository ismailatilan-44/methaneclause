"""Bond sizing from the re-detection forecast (spec §5): the only place the model is used. It never triggers a payout.

As of the covenant start (2026-01-01), for each covered facility:
  p_pass   P(a pass over the facility detects a plume) = max_s p_s over its linked Carbon Mapper sources (the lower
           bound of the union; twin 250 m / 1000 m sources share most detections, so 1 - prod(1 - p_s) would count
           one leak twice). p_s from B1s (smoothed persistence, the product forecaster since the model failed
           the decision rule), B1 (hard ratio) or the model (weighted by the trailing-12-month instrument mix)
  lambda   passes per year (trailing 12 months)
  q, tiers share of detected passes with a quantified plume >= minRate, and their tier mix (history before as-of)
Annual rebate exposure is simulated month by month (Poisson passes; at most one paid episode per month, an
approximation of the 30-day linkWindow; a per-pass probability held constant for 12 months), 20,000 draws, seed 0.
Recommended bond = P90 of the set's annual exposure under the model if the frozen decision rule kept it, else under
B1s: the hard B1 ratio forecasts exactly 0 after a clean six months (the MARS "absent" reading), which the held-out
false-zero rates (7-9 %) and DZA_S_012 show to be wrong.

Backtest: the same forecast over 2026-01-01 .. data cutoff vs what the covenant would actually have paid (real
detections, linkWindow walk, tier of the largest quantified plume in that pass). Writes bond_sizing.json.
"""
from __future__ import annotations

import json
import math
from collections import Counter
import pickle
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import DERIVED, minus_months, write_json  # noqa: E402
from model.baseline import TRAIL_MONTHS  # noqa: E402
from model.train import FEATURES, INSTRUMENTS, add_features  # noqa: E402
from policy import COVENANT_START_DATE, POLICY, TIER_AMOUNTS_WEI, tier_of  # noqa: E402

OUT = DERIVED / "model"
ASOF = COVENANT_START_DATE
N_DRAWS = 20_000
SEED = 0
LINK_DAYS = POLICY["linkWindow"] // 86_400


def _instr(s: str) -> str:
    return s if s in INSTRUMENTS else "multi"


def history_rows(name: str, t: dict, asof: str, instrument: str) -> pd.DataFrame:
    """Labelled rows for passes before `asof` plus one synthetic next pass at `asof` (same columns as build_labels)."""
    det, obs = set(t["det"]), [o for o in t["obs"] if o[0] < asof] + [[asof, instrument]]
    dates = [o[0] for o in obs]
    rows = []
    for i, (day, instr) in enumerate(obs):
        prior = [x for x in dates[:i] if x >= minus_months(day, TRAIL_MONTHS)]
        rows.append({"source_name": name, "date": day, "instrument": instr, "country": "", "M": len(prior),
                     "D": sum(x in det for x in prior), "n_obs_before": i,
                     "n_det_before": sum(x in det for x in dates[:i]), "label": int(day in det and day < asof)})
    return pd.DataFrame(rows)


def facility_passes(cms: list[str], tl: dict, plumes: list[dict]) -> pd.DataFrame:
    """One row per facility pass: date, instrument, detected, largest quantified plume rate within 1.5 km."""
    obs: dict[str, str] = {}
    det: set[str] = set()
    for n in cms:
        for d, i in tl[n]["obs"]:
            obs[d] = _instr(i) if d not in obs or obs[d] == _instr(i) else "multi"
        det |= set(tl[n]["det"])
    rate: dict[str, float] = {}
    for p in plumes:
        if p["emission_auto"] is not None:
            d = p["scene_timestamp"][:10]
            rate[d] = max(rate.get(d, 0.0), float(p["emission_auto"]))
    return pd.DataFrame([{"date": d, "instrument": i, "detected": d in det, "max_rate": rate.get(d, np.nan)}
                         for d, i in sorted(obs.items())])


def simulate(p_pass: float, lam: float, q: float, tier_p: np.ndarray, months: int, rng,
             amounts=None) -> np.ndarray:
    """Annual (or `months`) rebate exposure in wei (or in the given tier amounts), N_DRAWS draws."""
    amounts = np.array(TIER_AMOUNTS_WEI if amounts is None else amounts, dtype=float)
    passes = rng.poisson(lam / 12, size=(N_DRAWS, months))
    p_month = 1 - (1 - p_pass * q) ** passes  # >= 1 payable detection in the month
    hit = rng.random((N_DRAWS, months)) < p_month
    tiers = rng.choice(3, size=(N_DRAWS, months), p=tier_p)
    return (hit * amounts[tiers]).sum(axis=1)


def realised(fp: pd.DataFrame, start: str, end: str) -> tuple[int, int]:
    """Episodes and rebate (wei) the covenant would have paid on the real detections in [start, end]."""
    parent, n, wei = None, 0, 0
    for _, r in fp[(fp["date"] > start) & (fp["date"] <= end) & fp["detected"]].iterrows():
        t = tier_of(r["max_rate"]) if r["max_rate"] == r["max_rate"] else None
        if t is None:
            continue  # unquantified or below minRate: the observer will not sign, no episode
        if parent is None or (date.fromisoformat(r["date"]) - date.fromisoformat(parent)).days > LINK_DAYS:
            parent, n, wei = r["date"], n + 1, wei + TIER_AMOUNTS_WEI[t - 1]
    return n, wei


def eth(wei: float) -> float:
    return round(float(wei) / 1e18, 6)


def main() -> None:
    metrics = json.loads((OUT / "model_metrics.json").read_text())
    clf = pickle.loads((OUT / "model.pkl").read_bytes())
    frozen = json.loads((OUT / "splits.json").read_text())["frozen_rule_constants"]
    tl = json.loads((DERIVED / "cm_timelines.json").read_text())
    ss = json.loads((DERIVED / "sourceset_v2.json").read_text())
    cutoff = max(o[0] for t in tl.values() for o in t["obs"])
    bt_months = max(1, round((date.fromisoformat(cutoff) - date.fromisoformat(ASOF)).days / 30.44))
    rng = np.random.default_rng(SEED)

    kinds = ("B1s", "B1", "model")
    sa, sb = frozen["B1s_a"], frozen["B1s_b"]
    fac_out, sims, bts, real_total = {}, {k: [] for k in kinds}, {k: [] for k in kinds}, 0
    for f in ss["sources"]:
        cms = f["linked_cm_sources"]
        case = json.loads((DERIVED / "cases" / f"{f['case']}.json").read_text())
        fp = facility_passes(cms, tl, case["cm_plumes_within_radius"])
        before = fp[fp["date"] < ASOF]
        last12 = before[before["date"] >= minus_months(ASOF, 12)]
        lam = float(max(len(last12), 1))
        mix = (last12 if len(last12) else before)["instrument"].value_counts(normalize=True).to_dict()

        p_model_s, p_b1_s, p_b1s_s = [], [], []
        for n in cms:
            ps = 0.0
            for instr, w in mix.items():
                df = add_features(history_rows(n, tl[n], ASOF, instr))
                ps += w * float(clf.predict_proba(df[FEATURES].to_numpy(dtype=float)[-1:])[:, 1][0])
            p_model_s.append(ps)
            last = history_rows(n, tl[n], ASOF, "tan").iloc[-1]
            p_b1_s.append(last["D"] / last["M"] if last["M"] > 0 else frozen["train_base_rate"])
            p_b1s_s.append((last["D"] + sa) / (last["M"] + sb))
        p_pass = {"B1s": max(p_b1s_s), "B1": max(p_b1_s), "model": max(p_model_s)}

        det = before[before["detected"]]
        tiers = [tier_of(r) for r in det["max_rate"] if r == r]
        payable = [t for t in tiers if t]
        q = len(payable) / len(det) if len(det) else 0.0
        tier_p = (np.bincount(np.array(payable) - 1, minlength=3) / len(payable)) if payable else np.array([0, 0, 1.0])

        out = {"linked_cm_sources": cms, "passes_last_12m": int(len(last12)), "instrument_mix": mix,
               "p_pass": {k: round(v, 4) for k, v in p_pass.items()},
               "p_source_model": dict(zip(cms, [round(p, 4) for p in p_model_s])),
               "p_source_B1": dict(zip(cms, [round(p, 4) for p in p_b1_s])),
               "p_source_B1s": dict(zip(cms, [round(p, 4) for p in p_b1s_s])),
               "payable_share_of_detections": round(q, 4), "tier_shares": [round(x, 4) for x in tier_p]}
        n_real, wei_real = realised(fp, ASOF, cutoff)
        real_total += wei_real
        out["backtest"] = {"window": f"{ASOF}..{cutoff}", "months": bt_months,
                           "realised": {"episodes": n_real, "rebate_eth": eth(wei_real)}}
        for k in kinds:
            annual = simulate(p_pass[k], lam, q, tier_p, 12, rng)
            bt = simulate(p_pass[k], lam, q, tier_p, bt_months, rng)
            sims[k].append(annual)
            bts[k].append(bt)
            out[f"annual_{k}"] = {"mean_eth": eth(annual.mean()), "p50_eth": eth(np.percentile(annual, 50)),
                                  "p90_eth": eth(np.percentile(annual, 90))}
            out["backtest"][f"forecast_{k}"] = {"mean_eth": eth(bt.mean()), "p10_eth": eth(np.percentile(bt, 10)),
                                                "p90_eth": eth(np.percentile(bt, 90)),
                                                "realised_percentile": round(float((bt < wei_real).mean()
                                                                                   + 0.5 * (bt == wei_real).mean()), 3)}
        fac_out[f["source_name"]] = out

    used = "model" if metrics["decision"] == "KEEP_MODEL" else "B1s"
    total = {k: np.sum(v, axis=0) for k, v in sims.items()}
    p90 = {k: float(np.percentile(v, 90)) for k, v in total.items()}
    step = 10**15  # round the bond up to 0.001 ETH
    bond = int(math.ceil(p90[used] / step) * step)
    res = {
        "asof": ASOF, "data_cutoff": cutoff, "draws": N_DRAWS, "seed": SEED,
        "method": "monthly Poisson passes x per-pass detection x payable share x tier mix; <= 1 paid episode/month",
        "forecaster_used": used, "decision": metrics["decision"],
        "modelHash": metrics["modelHash"] if used == "model" else None,
        "B1s_constants": {"a": sa, "b": sb},
        "tier_amounts_eth": [eth(x) for x in TIER_AMOUNTS_WEI],
        "set_annual_exposure": {k: {"mean_eth": eth(v.mean()), "p90_eth": eth(p90[k])} for k, v in total.items()},
        "set_backtest": {"window": f"{ASOF}..{cutoff}", "realised_rebate_eth": eth(real_total),
                         **{f"forecast_{k}": {"mean_eth": eth(np.sum(v, axis=0).mean()),
                                              "p10_eth": eth(np.percentile(np.sum(v, axis=0), 10)),
                                              "p90_eth": eth(np.percentile(np.sum(v, axis=0), 90))}
                            for k, v in bts.items()},
                         "note": "3 facilities: a sanity check of the bond logic, not a validation of the model "
                                 "(that is the held-out BSS in model_metrics.json)"},
        "recommended_bond_wei": str(bond), "recommended_bond_eth": eth(bond),
        "ranking_by_expected_exposure": sorted(fac_out, key=lambda n: -fac_out[n][f"annual_{used}"]["mean_eth"]),
        "facilities": fac_out,
    }
    write_json(OUT / "bond_sizing.json", res)
    print(f"bond (P90 annual, {used}): {eth(bond)} ETH   set exposure {res['set_annual_exposure']}\n"
          f"  set backtest {res['set_backtest']}")
    for n, o in fac_out.items():
        b = o["backtest"]
        print(f"  {n}: p_pass {o['p_pass']}  passes/yr {o['passes_last_12m']}  "
              f"realised {b['realised']}  B1s {b['forecast_B1s']}  B1 {b['forecast_B1']}  model {b['forecast_model']}")


def size_portfolio() -> dict:
    """Set sizing for the portfolio covenant (sourceset_v3.json, spec-v2 §7): bond = P90 of the set's annual rebate
    exposure as of the catalogue end, B1s forecaster (the model failed the frozen decision rule, and model.pkl is not
    committed); the same monthly simulation as v1. Pool = the same P90: refunds are capped at the rebate an episode
    cost, so a pool never needs more than the rebates paid; the unreserved remainder returns to sponsors. Also in
    VERDICT_ONLY policy units. Committed inputs only. Writes model/bond_sizing_v3.json."""
    from common import RADIUS_KM, haversine_km
    from policy import POLICY_V2_VERDICT_ONLY

    frozen = json.loads((OUT / "splits.json").read_text())["frozen_rule_constants"]
    sa, sb = frozen["B1s_a"], frozen["B1s_b"]
    tl = json.loads((DERIVED / "cm_timelines.json").read_text())
    ss = json.loads((DERIVED / "sourceset_v3.json").read_text())
    mars = pd.read_csv(DERIVED / "mars_sources.csv").set_index("source_name")
    pl = pd.read_csv(DERIVED / "cm_plumes.csv").drop_duplicates("plume_id")
    asof = max(o[0] for t in tl.values() for o in t["obs"])
    units = POLICY_V2_VERDICT_ONLY["pool"]["unitTierAmounts"]
    rng = np.random.default_rng(SEED)
    fac, wei_sims, unit_sims = {}, [], []
    for f in ss["sources"]:
        cms = [n for n in f["linked_cm_sources"] if n in tl]
        if not cms:
            fac[f["source_name"]] = {"sized": False, "why": "no linked Carbon Mapper source"}
            continue
        m = mars.loc[f["source_name"]]
        g = pl[pl["source_name"].isin(cms)]
        near = haversine_km(m["lat"], m["lon"], g["lat"].to_numpy(), g["lon"].to_numpy()) <= RADIUS_KM
        plumes = [{"scene_timestamp": r.scene_timestamp,
                   "emission_auto": None if r.emission_auto != r.emission_auto else float(r.emission_auto)}
                  for r in g[near].itertuples() if isinstance(r.scene_timestamp, str)]
        fp = facility_passes(cms, tl, plumes)
        before = fp[fp["date"] < asof]
        last12 = before[before["date"] >= minus_months(asof, 12)]
        lam = float(max(len(last12), 1))
        p_pass = max((lambda r: (r["D"] + sa) / (r["M"] + sb))(history_rows(n, tl[n], asof, "tan").iloc[-1])
                     for n in cms)
        det = before[before["detected"]]
        payable = [t for t in (tier_of(r) for r in det["max_rate"] if r == r) if t]
        q = len(payable) / len(det) if len(det) else 0.0
        tier_p = (np.bincount(np.array(payable) - 1, minlength=3) / len(payable)) if payable else np.array([0, 0, 1.0])
        w = simulate(p_pass, lam, q, tier_p, 12, rng)
        u = simulate(p_pass, lam, q, tier_p, 12, rng, amounts=units)
        wei_sims.append(w)
        unit_sims.append(u)
        fac[f["source_name"]] = {"sized": True, "linked_cm_sources": cms, "p_pass_B1s": round(p_pass, 4),
                                 "passes_last_12m": int(len(last12)), "payable_share": round(q, 4),
                                 "annual_mean_eth": eth(w.mean()), "annual_p90_eth": eth(np.percentile(w, 90))}
    tw, tu = np.sum(wei_sims, axis=0), np.sum(unit_sims, axis=0)
    p90w = float(np.percentile(tw, 90))
    bond = int(math.ceil(p90w / 10**15) * 10**15)
    res = {"asof": asof, "draws": N_DRAWS, "seed": SEED, "forecaster_used": "B1s",
           "sourceset": "data/derived/sourceset_v3.json", "sourceset_root": ss["root"],
           "method": "as v1: monthly Poisson passes x per-pass detection (B1s) x payable share x tier mix; <= 1 paid "
                     "episode per month; summed over the set",
           "set_annual_exposure": {"wei": {"mean": float(tw.mean()), "p90": p90w},
                                   "eth": {"mean": eth(tw.mean()), "p90": eth(p90w)},
                                   "policyUnits": {"mean": round(float(tu.mean()) / 100, 2),
                                                   "p90": round(float(np.percentile(tu, 90)) / 100, 2)}},
           "recommended_bond_wei": str(bond), "recommended_bond_eth": eth(bond),
           "pool": {"wei": str(bond), "eth": eth(bond),
                    "why": "refunds are capped at the rebate an episode cost, so the pool never needs more than the "
                           "rebates paid: sized to the same P90; the unreserved remainder returns to sponsors"},
           "label": "testnet-sized tier amounts (0.005 / 0.003 / 0.001 ETH) [SIM]; policyUnits = the same exposure in "
                    "VERDICT_ONLY policy units",
           "facilities": fac}
    unsized = sorted(n for n, f in fac.items() if not f["sized"])
    reasons = Counter(f["reason"] for f in ss["sources"])
    cands = reasons.get("join v1 candidate", 0) + reasons.get("join v1 candidate and demo case", 0)
    res["card"] = {"portfolioSentence": (
        f"Portfolio [SIM]: {ss['n']} facilities — {cands} join candidates, each chosen because it showed a Carbon "
        f"Mapper plume that MARS did not see, plus the {reasons.get('demo case', 0)} other demo cases; "
        f"{ss['n'] - len(unsized)} of {ss['n']} are sized: {', '.join(unsized)} own no Carbon Mapper source (the join "
        f"matches plumes, ownership uses source centroids; their plumes belong to a nearer target's source) and cannot "
        f"open an episode; bond = pool = "
        f"{eth(bond)} ETH, the P90 of one year's rebate exposure.")}
    write_json(OUT / "bond_sizing_v3.json", res)
    print(f"portfolio ({ss['n']} facilities): bond = pool = {eth(bond)} ETH (P90 annual, B1s); "
          f"policy units P90 {res['set_annual_exposure']['policyUnits']['p90']}")
    return res


if __name__ == "__main__":
    if sys.argv[1:] == ["--set", "v3"]:
        size_portfolio()
        raise SystemExit
    main()
