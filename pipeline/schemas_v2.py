"""Step 0 (spec-v2 §3.1): the data files the pipeline writes and the page reads. Each file carries a `schema` key; this module
names the required keys per schema and validates a document (no external dependency). The pipeline fills real values;
`mock_v2.py` writes schema-valid placeholders labelled MOCK so the page could be built before the numbers existed.
"""
from __future__ import annotations

SCHEMAS: dict[str, dict] = {
    "methaneclause.cleanruns/1": {
        "required": ["schema", "generatedFrom", "definition", "sources_with_run", "cells_with_run", "redetected_later",
                     "redetected_later_share", "next_pass_detection_rate", "by_instrument", "upper_bound_bp"],
    },
    "methaneclause.clean_passes/1": {
        "required": ["schema", "case", "sourceId", "since", "scenes"],
        "item": ("scenes", ["sceneProviderId", "sceneId", "sceneAt", "publishedAt", "instrument", "cloudCoverPct",
                            "cloudCoverPctAssessed", "notCloudy", "originIntersect", "perSourceQueries",
                            "plumeOnScene", "marsNearby", "qualified", "refusal"]),
    },
    "methaneclause.corroboration/1": {
        "required": ["schema", "windowDays", "cases", "catalogue"],
    },
    "methaneclause.backtest/1": {
        "required": ["schema", "label", "policyHash", "targets", "by_country", "exporter_table", "stake_band",
                     "scene_data"],
    },
    "methaneclause.verification_time/1": {
        "required": ["schema", "label", "manual", "tool"],
    },
}


class SchemaError(ValueError):
    pass


def validate(doc: dict) -> None:
    """Raise SchemaError if `doc` lacks its schema's required keys (and, for item lists, the per-item keys)."""
    name = doc.get("schema")
    if name not in SCHEMAS:
        raise SchemaError(f"unknown schema {name!r}")
    spec = SCHEMAS[name]
    missing = [k for k in spec["required"] if k not in doc]
    if missing:
        raise SchemaError(f"{name}: missing {missing}")
    if "item" in spec:
        key, item_keys = spec["item"]
        for i, it in enumerate(doc[key]):
            gone = [k for k in item_keys if k not in it]
            if gone:
                raise SchemaError(f"{name}: {key}[{i}] missing {gone}")
