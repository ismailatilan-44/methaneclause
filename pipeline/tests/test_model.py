"""D3: bond-sizing helpers and the stake band (the trained model itself is checked by `make model` + audit)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from model.bond import realised, simulate
from policy import TIER_AMOUNTS_WEI, tier_of
from stake import clopper_pearson


def test_tiers_follow_iea_levels():
    assert [tier_of(x) for x in (99, 100, 1499, 1500, 4999, 5000)] == [None, 3, 3, 2, 2, 1]


def test_realised_respects_link_window_and_floor():
    fp = pd.DataFrame([
        {"date": "2026-01-10", "detected": True, "max_rate": 2548.0},   # episode, tier 2
        {"date": "2026-01-25", "detected": True, "max_rate": 6000.0},   # within 30 days: linked, no pay
        {"date": "2026-03-01", "detected": True, "max_rate": 50.0},     # below minRate: observer will not sign
        {"date": "2026-03-02", "detected": True, "max_rate": np.nan},   # unquantified: no episode
        {"date": "2026-03-05", "detected": False, "max_rate": np.nan},
        {"date": "2026-04-01", "detected": True, "max_rate": 700.0},    # > 30 days after parent: tier 3
    ])
    assert realised(fp, "2026-01-01", "2026-12-31") == (2, TIER_AMOUNTS_WEI[1] + TIER_AMOUNTS_WEI[2])


def test_simulate_bounds():
    rng = np.random.default_rng(0)
    zero = simulate(0.0, 12, 1.0, np.array([0, 0, 1.0]), 12, rng)
    assert zero.max() == 0
    always = simulate(1.0, 1200, 1.0, np.array([1.0, 0, 0]), 12, rng)
    assert always.min() == always.max() == 12 * TIER_AMOUNTS_WEI[0]  # one paid episode per month at most


def test_clopper_pearson_all_detected():
    lo, hi = clopper_pearson(10, 10)
    assert hi == 1.0 and abs(lo - 0.025 ** (1 / 10)) < 1e-9
