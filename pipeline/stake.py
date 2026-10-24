"""Stake band for the lead case (spec §9 "Prove"): what the MARS *absent* label counts as zero. A STAKE, not abatement.

From the passes after MARS's last plume date (the period MARS calls "absent"):
  duty cycle   detected passes / passes, with a 95% Clopper-Pearson interval
  rates        per-plume attested Carbon Mapper rates within 1.5 km of the MARS facility (kg/h)
  band         low  = P10 plume rate x duty-cycle lower bound x 8,760 h
               mid  = median plume rate x duty-cycle point estimate x 8,760 h
               high = P90 plume rate x duty-cycle upper bound x 8,760 h
CO2e with GWP100 = 29.8 (AR6, fossil CH4). Satellites see snapshots of intermittent plumes; annualising a snapshot
rate assumes the duty cycle seen on passes holds between passes. That assumption is the main uncertainty and is
stated next to the number everywhere. Writes data/derived/stake_band.json.
"""
from __future__ import annotations

import json

import numpy as np
from scipy.stats import beta

from common import DERIVED, write_json
from policy import POLICY

HOURS = 8760


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    lo = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    hi = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return lo, hi


def band_from(passes: int, detected: int, rates, gwp: dict | None = None) -> dict:
    """The v1 band for one source, or a reasoned refusal ({"status": "not_quantified", "reason": ...}) on thin data,
    never an exception. `gwp` maps a label (e.g. "gwp100") to a factor for CO2e columns."""
    rates = np.array([r for r in rates if r is not None and r == r], dtype=float)
    if passes <= 0:
        return {"status": "not_quantified", "reason": "no satellite pass in the window", "passes": passes,
                "detected": detected}
    if detected <= 0 or len(rates) == 0:
        return {"status": "not_quantified",
                "reason": ("no detection in the window" if detected <= 0 else
                           "detections without a quantified plume rate (hidden or missing emission_auto): "
                           "no quantified plume"),
                "passes": passes, "detected": detected}
    lo, hi = clopper_pearson(detected, passes)
    kt = {"low": np.percentile(rates, 10) * lo, "mid": np.median(rates) * detected / passes,
          "high": np.percentile(rates, 90) * hi}
    kt = {key: float(v) * HOURS / 1e6 for key, v in kt.items()}  # kg/h -> kt/yr
    out = {"status": "quantified", "passes": passes, "detected": detected,
           "duty_cycle": round(detected / passes, 4), "duty_cycle_ci95": [round(lo, 4), round(hi, 4)],
           "plumes": int(len(rates)),
           "rate_kgh": {"min": round(float(rates.min()), 1), "p10": round(float(np.percentile(rates, 10)), 1),
                        "median": round(float(np.median(rates)), 1), "p90": round(float(np.percentile(rates, 90)), 1),
                        "max": round(float(rates.max()), 1)},
           "kt_ch4_per_yr": {key: round(v, 2) for key, v in kt.items()}, "kt_raw": kt}
    for label, g in (gwp or {}).items():
        out[f"kt_co2e_per_yr_{label}"] = {key: round(v * g, 1) for key, v in kt.items()}
    return out


def band(case: str) -> dict:
    c = json.loads((DERIVED / "cases" / f"{case}.json").read_text())
    since = c["post_mars_last_plume"]["since"]
    n, k = c["post_mars_last_plume"]["n_cm_passes"], c["post_mars_last_plume"]["n_cm_detected"]
    rates = [p["emission_auto"] for p in c["cm_plumes_within_radius"]
             if p["scene_timestamp"][:10] > since and p["emission_auto"] is not None]
    gwp = POLICY["gwp100"]
    b = band_from(n, k, rates)
    head = {"case": case, "label": "stake, not abatement", "mars_label": c["mars_source"]["persistency_category"],
            "since": since}
    if b["status"] != "quantified":
        return {**head, **b}
    kt = b.pop("kt_raw")
    b.pop("status")
    return {
        **head, **b,
        "kt_co2e_per_yr_gwp100": {key: round(v * gwp, 1) for key, v in kt.items()},
        "gwp100": gwp,
        "assumption": "the duty cycle seen on satellite passes holds between passes; plume rates are snapshots; "
                      "conservative in one way: simultaneous plumes in one pass are not summed",
    }


def main() -> None:
    lead = json.loads((DERIVED / "cases" / "LEAD.json").read_text())["lead"]
    out = band(lead)
    write_json(DERIVED / "stake_band.json", out)
    print(f"{lead}: {out['detected']}/{out['passes']} passes since {out['since']} (duty {out['duty_cycle_ci95']}), "
          f"{out['plumes']} plumes {out['rate_kgh']['min']}-{out['rate_kgh']['max']} kg/h -> "
          f"{out['kt_ch4_per_yr']} kt CH4/yr = {out['kt_co2e_per_yr_gwp100']} ktCO2e/yr ({out['label']})")


if __name__ == "__main__":
    main()
