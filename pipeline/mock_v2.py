"""Write schema-valid MOCK data files to data/derived/mock/ (spec-v2 §11 step 0), so the page could be built before
the pipeline's real numbers existed. Every value here is a placeholder and every file says so; the page must label mock data
as such and never quote it. Real files replace these paths' counterparts in data/derived/.

    cd pipeline && .venv/bin/python mock_v2.py
"""
from __future__ import annotations

import json

from common import DERIVED
from schemas_v2 import validate

OUT = DERIVED / "mock"
MOCK = "MOCK — placeholder for the page, not a measurement"


def scene(i: int, inst: str, qualified: bool, refusal: str | None) -> dict:
    return {"sceneProviderId": f"mock-scene-{i}", "sceneId": "0x" + f"{i:064x}", "sceneAt": 1730000000 + i * 2_000_000,
            "publishedAt": 1730000000 + i * 2_000_000 + 3_000_000, "instrument": inst, "cloudCoverPct": 0,
            "cloudCoverPctAssessed": 0, "notCloudy": qualified, "originIntersect": True,
            "perSourceQueries": [{"cmSource": "MOCK", "url": "MOCK", "params": {}, "responseSha256": "0x" + "00" * 32,
                                  "plumeCount": 0}],
            "plumeOnScene": False, "marsNearby": {"windowDays": 7, "count": 0, "rows": []}, "qualified": qualified,
            "refusal": refusal}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    docs = {
        "cleanruns.json": {"schema": "methaneclause.cleanruns/1", "note": MOCK, "generatedFrom": [MOCK],
                           "definition": MOCK, "sources_with_run": 0, "cells_with_run": 0, "redetected_later": 0,
                           "redetected_later_share": 0.0, "next_pass_detection_rate": 0.0, "by_instrument": {},
                           "upper_bound_bp": [round(10000 * (1 - 0.025 ** (1 / n))) for n in range(1, 33)]},
        "DZA_S_012.clean_passes.json": {"schema": "methaneclause.clean_passes/1", "note": MOCK, "case": "DZA_S_012",
                                        "sourceId": "0x" + "00" * 32, "since": 1730160000,
                                        "scenes": [scene(1, "tan", True, None), scene(2, "emi", True, None),
                                                   scene(3, "tan", False, "CLOUD_FLAG")]},
        "corroboration.json": {"schema": "methaneclause.corroboration/1", "note": MOCK, "windowDays": 7, "cases": {},
                               "catalogue": {}},
        "backtest.json": {"schema": "methaneclause.backtest/1", "note": MOCK, "label": MOCK, "policyHash": "0x" + "00" * 32,
                          "targets": 0, "by_country": {}, "exporter_table": [], "stake_band": {}, "scene_data": MOCK},
        "verification_time.json": {"schema": "methaneclause.verification_time/1", "note": MOCK, "label": MOCK,
                                   "manual": {"steps": [], "n": 0}, "tool": {"n": 0}},
    }
    for name, doc in docs.items():
        validate(doc)
        (OUT / name).write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"mock data -> {OUT} ({len(docs)} files)")


if __name__ == "__main__":
    main()
