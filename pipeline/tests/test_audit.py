"""audit.py: in STRICT mode a missing input is a failure, never a string in audit_numbers.json (spec-v2 §15 B1 review)."""
import pytest

import audit


def test_missing_inputs_are_found_at_any_depth():
    numbers = {"a": 1, "b": "run `make demo` first", "c": {"d": "run the lane-B target that writes x.json first",
                                                          "e": "ok"}}
    assert audit.missing_inputs(numbers) == ["b", "c.d"]


def test_strict_refuses_missing_inputs(monkeypatch, capsys):
    monkeypatch.setenv("STRICT", "1")
    with pytest.raises(SystemExit) as e:
        audit.refuse_missing({"x": "run `make measure` first"})
    assert e.value.code == 1 and "x" in capsys.readouterr().out


def test_non_strict_keeps_the_hint(monkeypatch):
    monkeypatch.delenv("STRICT", raising=False)
    audit.refuse_missing({"x": "run `make measure` first"})  # no exit


def test_every_case_needs_its_clean_pass_file(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "DERIVED", tmp_path)
    (tmp_path / "cases").mkdir()
    out = audit._clean_passes()
    assert set(out) == {"DZA_S_012", "TKM_S_276", "USA_S_077"}
    assert all(v.startswith("run ") for v in out.values())
