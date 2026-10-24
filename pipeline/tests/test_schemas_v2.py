"""Step 0: every v2 data file (mock or real) validates against its schema."""
from __future__ import annotations

import json

import pytest

from common import DERIVED
from schemas_v2 import SCHEMAS, SchemaError, validate


def test_mocks_validate():
    files = sorted((DERIVED / "mock").glob("*.json"))
    assert files, "run `cd pipeline && .venv/bin/python mock_v2.py`"
    for f in files:
        validate(json.loads(f.read_text()))


def test_real_v2_files_validate_when_present():
    for f in [*DERIVED.glob("*.json"), *(DERIVED / "cases").glob("*.clean_passes.json")]:
        doc = json.loads(f.read_text())
        if isinstance(doc, dict) and str(doc.get("schema", "")).startswith("methaneclause.") and doc["schema"] in SCHEMAS:
            validate(doc)


def test_missing_key_is_refused():
    with pytest.raises(SchemaError):
        validate({"schema": "methaneclause.cleanruns/1"})
    with pytest.raises(SchemaError):
        validate({"schema": "no.such/1"})
