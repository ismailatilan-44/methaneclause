"""The judge test wallet's refund-side run on Sepolia (pipeline/cure_arc.py): read from the tracked txlog and the chain
record only, every step status 1 and in order, the judge's steps from one wallet; a record that does not show a step
renders nothing (exit 1)."""
import json
import re
import shutil

import pytest

import cure_arc
from common import DERIVED


@pytest.fixture(scope="module")
def arc():
    return cure_arc.arc()


def test_every_step_is_on_chain_in_order_from_one_judge_wallet(arc):
    assert [s["key"] for s in arc["steps"]] == [k for k, *_ in cure_arc.STEPS]
    blocks = [s["block"] for s in arc["steps"]]
    assert blocks == sorted(blocks)
    chain = {t["tx"]: t for t in json.loads((DERIVED / "sepolia_txs.json").read_text())["transactions"]}
    for s, (_, _, label, by_judge) in zip(arc["steps"], cure_arc.STEPS):
        t = chain[s["tx"].rsplit("/", 1)[-1]]
        assert t["status"] == 1, label
        if by_judge:
            assert t["from"] == arc["wallet"], label
    assert "judge test wallet" in (chain[arc["steps"][-1]["tx"].rsplit("/", 1)[-1]]["fromRole"] or "")
    assert arc["counted"] >= 1


def test_case_facts_are_the_committed_relapse_and_a_qualified_scene_after_it(arc):
    doc = json.loads((DERIVED / "cases" / "DZA_S_012.clean_passes.json").read_text())
    assert arc["relapseDate"] == doc["summary"]["firstPlumeAfterSince"]["sceneAtIso"][:10]
    scene = next(x for x in doc["scenes"] if x["sceneName"] == arc["sceneName"])
    assert scene["qualified"] and scene["sceneAtIso"][:10] == arc["sceneDate"] > arc["relapseDate"]


def test_sentences_link_every_step_and_label_the_simulated_parties(arc):
    line, loop = cure_arc.readme_line(arc, 3), cure_arc.loop_sentence(arc, 3)
    for s in arc["steps"]:
        if s["key"] in {"relapseConfirmed", "claim", "counted"}:  # README (25 KB budget)
            assert s["tx"] in line
        if s["key"] in {"join", "relapseConfirmed", "poolOpened", "claim", "pass", "counted"}:  # Devpost / PDF
            assert s["tx"] in loop
    for text in (line, loop):
        assert "judge test wallet" in text and "[SIM]" in text and "independent judge" not in text
        assert "run by the author" in text and "judge's key" not in text  # the author's test wallet, never "the judge"
        assert cure_arc.clean_window_note() in text
    assert "two-sided loop runs" not in loop and loop.startswith("**Both sides of the clause have transactions")
    assert "10-minute sandbox challenge window" in loop and "funded the sandbox pool" in loop
    # a secret scan reads a 64-hex value on a line that says "wallet" as a possible key
    assert not any("wallet" in x.lower() and re.search(r"0x[0-9a-fA-F]{64}", x) for x in line.splitlines())
    assert all(x["status"] == 1 and x["tx"].startswith(cure_arc.SCAN) for x in cure_arc.app_steps(arc))


def test_window_note_is_read_from_the_policy_file():
    assert cure_arc.clean_window_note() == "(10-minute sandbox window [SIM]; one day in the policy)"
    assert cure_arc.days(2 * 86400) == "2 days"
    with pytest.raises(SystemExit):
        cure_arc.minutes(90)


def _copy(tmp_path):
    for f in ("sepolia_txlog.jsonl", "sepolia_txs.json"):
        shutil.copy(DERIVED / f, tmp_path / f)
    (tmp_path / "cases").mkdir()
    for f in ("DZA_S_012.json", "DZA_S_012.clean_passes.json"):
        shutil.copy(DERIVED / "cases" / f, tmp_path / "cases" / f)
    return tmp_path


def _rewrite_chain(tmp_path, fn):
    p = tmp_path / "sepolia_txs.json"
    d = json.loads(p.read_text())
    d["transactions"] = [fn(t) for t in d["transactions"]]
    p.write_text(json.dumps(d))


def test_a_failed_step_on_chain_renders_nothing(tmp_path, monkeypatch, arc):
    _copy(tmp_path)
    claim = arc["steps"][[s["key"] for s in arc["steps"]].index("claim")]["tx"].rsplit("/", 1)[-1]
    _rewrite_chain(tmp_path, lambda t: {**t, "status": 0} if t["tx"] == claim else t)
    monkeypatch.setattr(cure_arc, "DERIVED", tmp_path)
    with pytest.raises(SystemExit, match="openClaim_cure"):
        cure_arc.arc()


def test_a_step_sent_by_another_wallet_renders_nothing(tmp_path, monkeypatch, arc):
    _copy(tmp_path)
    counted = arc["steps"][-1]["tx"].rsplit("/", 1)[-1]
    _rewrite_chain(tmp_path, lambda t: {**t, "from": "0x" + "11" * 20} if t["tx"] == counted else t)
    monkeypatch.setattr(cure_arc, "DERIVED", tmp_path)
    with pytest.raises(SystemExit, match="not the judge test wallet"):
        cure_arc.arc()


def test_no_counted_pass_in_the_log_renders_nothing(tmp_path, monkeypatch):
    _copy(tmp_path)
    p = tmp_path / "sepolia_txlog.jsonl"
    p.write_text("".join(x + "\n" for x in p.read_text().splitlines() if '"countPass_cure"' not in x))
    monkeypatch.setattr(cure_arc, "DERIVED", tmp_path)
    with pytest.raises(SystemExit, match="countPass_cure"):
        cure_arc.arc()
