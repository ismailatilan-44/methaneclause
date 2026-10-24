"""The page's story data (make test-lane-c): every value the layered first screen, the story and the "Why you can trust
it" drawer state comes from app/data/app.json, written by scripts/build_app_data.py from committed files.

The clean-look rule the drawer prints is POLICY_V2's pool part, read from data/derived/policy_v2.json; the builder must
refuse a committed policy file that no longer equals POLICY_V2 (a stale or hand-edited file), so the page can never
state a rule the contracts do not run.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_app_data_page", ROOT / "scripts" / "build_app_data.py")
bad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bad)  # also puts pipeline/ on sys.path

APP = json.loads((ROOT / "app" / "data" / "app.json").read_text())
POOL = bad.policy_mod.POLICY_V2["pool"]
POLICY_FILE = json.loads((ROOT / "data" / "derived" / "policy_v2.json").read_text())


def test_clean_look_rule_is_policy_v2():
    r = bad.clean_look_rules()
    assert [[i["afterPasses"], i["shareBp"], i["twoInstruments"], i["isFinal"]] for i in r["installments"]] \
        == [list(x) for x in POOL["installments"]]
    assert r["cureTermDays"] * 86_400 == POOL["cureTerm"]
    assert r["cureTermMonths"] == round(r["cureTermDays"] / 30)
    assert r["marsWindowDays"] == POOL["marsWindowDays"]
    assert r["maxCloudPct"] * 100 == POOL["maxCloudBp"]
    assert r["cloudRule"] == POOL["cloudRule"]
    assert r["instrumentFloorKgH"] == POOL["instrumentFloorKgH"]
    assert r["cleanWindowSeconds"] == POOL["cleanWindow"]
    assert r["publicationLagDays"] * 86_400 == POOL["publicationLag"]
    assert r["source"].startswith("data/derived/policy_v2.json")


def test_the_last_step_needs_ten_looks_two_satellites_and_the_cure_term():
    """The sentence the drawer prints ("the last step needs 10 of them, two satellites and 18 months") is this row."""
    last = bad.clean_look_rules()["installments"][-1]
    assert last["isFinal"] and last["twoInstruments"]
    assert sum(i["shareBp"] for i in bad.clean_look_rules()["installments"]) == 10_000


def test_committed_app_json_carries_the_current_rule():
    assert APP["cleanLook"] == bad.clean_look_rules()


def test_a_policy_file_that_differs_from_policy_v2_is_refused(tmp_path, monkeypatch):
    edited = copy.deepcopy(POLICY_FILE)
    edited["policies"]["POLICY_V2"]["policy"]["pool"]["marsWindowDays"] = 3
    (tmp_path / "policy_v2.json").write_text(json.dumps(edited))
    monkeypatch.setattr(bad, "DERIVED", tmp_path)
    with pytest.raises(SystemExit, match="policy_v2.json"):
        bad.clean_look_rules()


def test_the_story_steps_are_in_the_committed_record():
    """Every on-chain step the story links is in app.json: the main Sepolia run (create, propose, finalize, the buyer's
    withdrawal, the rejections) and the replay's earn-back transactions."""
    steps = {s["step"]: s for s in APP["sepolia"]["run"]["steps"]}
    for k in ("createCovenant", "proposeEpisode", "finalize", "withdraw_buyer", "reject_tamper"):
        assert steps[k]["tx"].startswith(APP["sepolia"]["explorer"] + "/tx/0x")
    assert steps["reject_tamper"]["status"] == 0 and steps["reject_tamper"]["reason"] == "InvalidSignature"
    txs = APP["replay"]["variants"]["A"]["txs"]
    for k in ("finalize (episode)", "release", "relapse (cancel)", "relapse while the episode is PROPOSED"):
        assert txs[k] and all(t["tx"].startswith("0x") for t in txs[k])
    out = APP["replay"]["variants"]["A"]["outcome"]
    assert out["paidBp"] + out["forfeitedBp"] == 10_000


def test_live_reads_have_keyless_fallback_endpoints():
    """The page reads receipts live; publicnode prunes older ones, so app.json names keyless fallbacks (https, no key
    in the URL), tried in order after the primary endpoint."""
    s = APP["sepolia"]
    urls = [s["rpc"], *s["rpcFallbacks"]]
    assert len(urls) >= 3 and len(set(urls)) == len(urls)
    assert all(u.startswith("https://") and "key" not in u.lower() for u in urls)


def test_the_witness_flow_reads_the_sandbox_windows():
    """The witness panel and the clock card name the judge sandbox's appeal window from app.json, never typed."""
    w = json.loads((ROOT / "app" / "data" / "wallet.json").read_text())["covenant"]["params"]
    assert APP["sepolia"]["sandboxWindows"] == {"challengeWindow": w["challengeWindow"], "contestWindow": w["contestWindow"]}
