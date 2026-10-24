"""Terminal wording a judge reads (make demo / make judge-local): every line says what actually happened, with counts
taken from the data it lists. No network, no chain."""
import re

import pytest

from observer import cleanpass as cp
from observer import judge

COV = "0x" + "00" * 31 + "07"
CLAIM = "0x" + "ab" * 32
POOL = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"
QUALIFIED = "2025-02-12 emi"  # DZA: counted pass


@pytest.fixture
def no_raw_cache(tmp_path, monkeypatch):
    """A fresh clone: no raw Carbon Mapper responses, and the judge never set OFFLINE."""
    monkeypatch.setenv("CM_CASES_CACHE", str(tmp_path / "empty"))
    monkeypatch.delenv("OFFLINE", raising=False)


def test_recheck_skip_reason_is_plain(no_raw_cache):
    dza = cp.load_case("DZA_S_012")
    r = cp.recheck(dza, cp.find_scene(dza, QUALIFIED), covenant_id=COV, claim_id=CLAIM)
    assert r["mode"] == "skipped" and r["equal"] is None
    assert "skipped" not in r["why"]  # the printer says "skipped" once
    assert "OFFLINE" not in r["why"]  # the judge never set it; the default re-check reads the cache only
    assert "Carbon Mapper" in r["why"] and "LIVE=1" in r["why"] and "fresh clone" in r["why"]


def test_attest_lines_say_skipped_once_and_the_true_check_count(no_raw_cache, capsys):
    cp.CleanPassObserver("OBS_A", 31337, POOL).attest("DZA_S_012", QUALIFIED, covenant_id=COV, claim_id=CLAIM,
                                                      nonce=7)
    out = capsys.readouterr().out
    recheck = next(line for line in out.splitlines() if line.startswith("[5/5 Re-check]"))
    assert recheck.count("skipped") == 1 and "OFFLINE" not in recheck
    validate = next(line for line in out.splitlines() if line.startswith("[2/5 Validate]"))
    m = re.match(r"\[2/5 Validate\] (\d+) checks pass \(spec-v2 §2 rules 1-6\): (.*)$", validate)
    assert m, validate
    listed = m.group(2).split(", ")
    assert int(m.group(1)) == len(listed) and all(c.endswith(" ok") for c in listed)


def test_window_stop_on_a_public_chain_is_real_time():
    msg = judge.window_stop_message("challenge window", 1_800_000_000, 600, "Ethereum Sepolia", local=False)
    assert "challenge window closes" in msg  # scripts/judge_local.sh greps this
    assert "nothing is skipped on Ethereum Sepolia" in msg and "[SIM]" not in msg


def test_window_stop_on_anvil_says_the_clock_is_moved_next():
    msg = judge.window_stop_message("clean window", 1_800_000_000, 600, "Anvil (local)", local=True)
    assert "clean window closes" in msg  # scripts/judge_local.sh greps this
    assert "nothing is skipped" not in msg  # judge-local advances the Anvil clock right after this line
    assert "[SIM]" in msg and "make judge-cure" in msg


def test_quorum_line_prints_k_and_n_from_the_chain():
    line = judge.quorum_line(2, ["OBS_JUDGE", "SANDBOX_OBS_1"], ["SANDBOX_OBS_1", "SANDBOX_OBS_2", "OBS_JUDGE"],
                             "Anvil (local)")
    assert "k = 2" in line and "n = 3" in line and "2-of-2" not in line
    assert "OBS_JUDGE" in line and "SANDBOX_OBS_1" in line and "SANDBOX_OBS_2" in line
    assert "k = 3" in judge.quorum_line(3, ["A", "B", "C"], ["A", "B", "C", "D"], "x")
