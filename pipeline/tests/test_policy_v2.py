"""POLICY_V2 / replay / sandbox (spec-v2 §4) and onchainParamsHash = keccak256(abi.encode(PoolParams)) (§3)."""
from __future__ import annotations

import re
import shutil
import subprocess
from datetime import datetime, timezone

import pytest
from eth_utils import keccak

import policy
from final_reach import final_reach
from canon import evidence_hash
from cleanruns import UPPER_BOUND_BP
from common import ROOT

DAY = 86_400
ALL = {"main": policy.POLICY_V2, "verdictOnly": policy.POLICY_V2_VERDICT_ONLY, "replay": policy.POLICY_V2_REPLAY,
       "sandbox": policy.POLICY_V2_SANDBOX, "multiYear": policy.POLICY_V2_MULTIYEAR}


def _sol_struct(name: str) -> list[tuple[str, str]]:
    src = (ROOT / "contracts" / "src" / "IncentivePool.sol").read_text()
    body = re.search(r"struct\s+" + name + r"\s*\{(.*?)\}", src, re.S).group(1)
    body = re.sub(r"//[^\n]*", "", body)
    return [tuple(f.split()[:2]) for f in (x.strip() for x in body.split(";")) if f]


def _abi_type_from_solidity() -> str:
    rule = "(" + ",".join(t for t, _ in _sol_struct("InstallmentRule")) + ")"
    parts = [t.replace("InstallmentRule", rule) for t, _ in _sol_struct("PoolParams")]
    return "(" + ",".join(parts) + ")"


def test_abi_type_matches_contract_struct():
    assert policy.POOL_PARAMS_ABI == _abi_type_from_solidity()
    assert [n for _, n in _sol_struct("PoolParams")] == policy.POOL_PARAMS_FIELDS


def test_core_policy_unchanged():
    assert policy.POLICY_V2["core"] == policy.POLICY
    assert evidence_hash(policy.POLICY_V2["core"]) == evidence_hash(policy.POLICY)
    assert policy.POLICY_V2_SANDBOX["core"] == policy.SANDBOX_POLICY


def test_spec_values_main():
    p = policy.POLICY_V2["pool"]
    assert p["settlementMode"] == "ONCHAIN" and policy.POLICY_V2_VERDICT_ONLY["pool"]["settlementMode"] == "VERDICT_ONLY"
    assert (p["publicationLag"], p["cleanWindow"], p["resolveWindow"], p["holdWindow"], p["cureTerm"]) == \
        (60 * DAY, 1 * DAY, 7 * DAY, 64 * DAY, 540 * DAY)
    assert p["unitTierAmounts"] == [500, 300, 100]  # spec-v2 §15, 2026-10-16: units x100
    for amount in p["unitTierAmounts"]:  # every installment share of every tier cap is a whole policy unit
        assert all(amount * share % 10000 == 0 for _, share, _, _ in p["installments"])
    assert p["installments"] == [[3, 3000, False, False], [6, 3000, False, False], [10, 4000, True, True]]
    assert p["upperBoundBp"] == UPPER_BOUND_BP
    assert p["instrumentFloorKgH"] == {"tan": 180, "emi": 700}  # spec-v2 §15 2026-10-16 (B1 review)
    assert (p["maxCloudBp"], p["cloudRule"], p["marsWindowDays"], p["euSuperEmitterKgH"]) == \
        (2500, "not_cloudy", 7, 100)


def test_replay_dates():
    core = policy.POLICY_V2_REPLAY["core"]
    assert datetime.fromtimestamp(core["start"], timezone.utc).date().isoformat() == "2024-09-01"
    assert core["term"] == 730 * DAY
    end = datetime.fromtimestamp(core["start"] + core["term"], timezone.utc).date().isoformat()
    assert end == "2026-09-01"
    assert {k: v for k, v in core.items() if k not in ("start", "term")} == \
        {k: v for k, v in policy.POLICY.items() if k not in ("start", "term")}
    assert policy.POLICY_V2_REPLAY["pool"] == policy.POLICY_V2["pool"]


def test_sandbox_values():
    p = policy.POLICY_V2_SANDBOX["pool"]
    assert (p["publicationLag"], p["cleanWindow"], p["resolveWindow"], p["holdWindow"], p["cureTerm"]) == \
        (600, 600, 600, 1800, DAY)
    m = policy.POLICY_V2["pool"]
    for k in ("passBondWei", "challengerBondWei", "observerFeeWei"):
        assert int(p[k]) * 100 == int(m[k])


@pytest.mark.parametrize("name", sorted(ALL))
def test_fund_rules_hold(name):
    """The checks IncentivePool.fund performs (spec §3) hold for every policy we ship."""
    pol = ALL[name]
    p, core = pol["pool"], pol["core"]
    assert p["holdWindow"] >= p["publicationLag"] + core["challengeWindow"] + core["contestWindow"]
    assert p["resolveWindow"] >= p["cleanWindow"]
    th = [i[0] for i in p["installments"]]
    assert all(a < b for a, b in zip(th, th[1:]))
    assert sum(i[1] for i in p["installments"]) == 10000
    ub = p["upperBoundBp"]
    assert len(ub) == 32 and all(a >= b for a, b in zip(ub, ub[1:]))
    assert pol["onchainParamsHash"] == policy.onchain_params_hash(pol)
    assert pol["v"] == 2


def _words(pol) -> bytes:
    """Independent encoder: PoolParams is fully static, so abi.encode is its fields as 32-byte words in order."""
    t = policy.pool_params(pol)
    flat = list(t[:8]) + list(t[8])
    for r in t[9]:
        flat += [r[0], r[1], int(r[2]), int(r[3])]
    flat += list(t[10])
    assert len(flat) == 55
    return b"".join(int(x).to_bytes(32, "big") for x in flat)


@pytest.mark.parametrize("name", sorted(ALL))
def test_onchain_params_hash_independent_encoding(name):
    pol = ALL[name]
    assert policy.onchain_params_hash(pol) == "0x" + keccak(_words(pol)).hex()


def test_mode_is_not_in_params_hash_but_in_policy_hash():
    assert policy.POLICY_V2["onchainParamsHash"] == policy.POLICY_V2_VERDICT_ONLY["onchainParamsHash"]
    assert policy.policy_hash(policy.POLICY_V2) != policy.policy_hash(policy.POLICY_V2_VERDICT_ONLY)
    assert len({policy.policy_hash(p) for p in ALL.values()}) == 5


def test_params_hash_changes_with_any_field():
    import copy

    pol = copy.deepcopy(policy.POLICY_V2)
    pol["pool"]["holdWindow"] += 1
    assert policy.onchain_params_hash(pol) != policy.POLICY_V2["onchainParamsHash"]


@pytest.mark.skipif(shutil.which("cast") is None and not (ROOT.home() / ".foundry/bin/cast").exists(),
                    reason="Foundry cast not installed")
def test_cast_abi_encode_agrees():
    cast = shutil.which("cast") or str(ROOT.home() / ".foundry/bin/cast")
    t = policy.pool_params(policy.POLICY_V2)

    def lit(x):
        if isinstance(x, bool):
            return "true" if x else "false"
        if isinstance(x, (list, tuple)):
            inner = ",".join(lit(v) for v in x)
            return f"({inner})" if isinstance(x, tuple) else f"[{inner}]"
        return str(x)

    arg = "(" + ",".join(lit(v) for v in t) + ")"
    enc = subprocess.run([cast, "abi-encode", f"f({policy.POOL_PARAMS_ABI})", arg], check=True, capture_output=True,
                         text=True).stdout.strip()
    assert "0x" + keccak(bytes.fromhex(enc[2:])).hex() == policy.POLICY_V2["onchainParamsHash"]


def test_floor_table_matches_policy_and_is_cited():
    import floors

    assert {k: v["kgH"] for k, v in floors.FLOORS.items()} == policy.POOL_V2["instrumentFloorKgH"]
    for v in floors.FLOORS.values():
        assert any(s["status"] == "read" and s["quote"] for s in v["sources"])
    g100, g20 = floors.GWP["gwp100_fossil_ch4"], floors.GWP["gwp20_fossil_ch4"]
    assert (g100["value"], g100["status"]) == (29.8, "read")
    assert (g20["value"], g20["status"]) == (82.5, "read")  # IPCC AR6 WG1 Table 7.15, primary (B7)
    for g in (g100, g20):
        assert g["source"]["url"].startswith("https://") and str(g["value"]) in g["source"]["quote"]
    ipcc = floors.IPCC_TABLE_7_15
    assert g20["source"] is ipcc and ipcc["url"].startswith("https://www.ipcc.ch/") and "1017" in ipcc["locator"]
    assert g100["ipccCheck"]["agrees"] and g100["ipccCheck"]["source"] is ipcc
    assert floors.IPCC_AR6_CH4["fossil"] == {"gwp20": [g20["value"], g20["uncertainty"]["plusMinus"]],
                                             "gwp100": [g100["value"], g100["uncertainty"]["plusMinus"]]}
    assert floors.FLOORS["tan"]["status"] == "read" and floors.FLOORS["emi"]["status"] == "read-secondary"
    assert policy.POLICY["gwp100"] == g100["value"]  # v1 stake band uses the same cited value


def test_parity_vector_is_current():
    """pipeline/tests/vectors/policy_v2.json is what the contracts' forge test rebuilds PoolParams from; it must not go stale."""
    import json

    v = json.loads((ROOT / "pipeline" / "tests" / "vectors" / "policy_v2.json").read_text())
    t = policy.pool_params(policy.POLICY_V2)
    assert [f["name"] for f in v["fields"]] == policy.POOL_PARAMS_FIELDS
    vals = {f["name"]: f["value"] for f in v["fields"]}
    assert [vals["publicationLag"], vals["cleanWindow"], vals["resolveWindow"], vals["holdWindow"], vals["cureTerm"],
            int(vals["passBond"]), int(vals["challengerBond"]), int(vals["observerFeeWei"])] == list(t[:8])
    assert [int(x) for x in vals["unitTierAmounts"]] == t[8]
    assert [(r["afterPasses"], r["shareBp"], r["twoInstruments"], r["isFinal"]) for r in vals["installments"]] == t[9]
    assert vals["upperBoundBp"] == t[10]
    assert v["abiType"] == policy.POOL_PARAMS_ABI
    assert v["onchainParamsHash"] == policy.POLICY_V2["onchainParamsHash"]
    assert "0x" + keccak(bytes.fromhex(v["abiEncoded"][2:])).hex() == v["onchainParamsHash"]


def test_each_gwp_entry_has_its_read_date():
    import floors

    assert {k: v["readOn"] for k, v in floors.GWP.items()} == {"gwp100_fossil_ch4": "2026-10-16",
                                                                "gwp20_fossil_ch4": "2026-10-15"}
    assert floors.GWP["gwp100_fossil_ch4"]["ipccCheck"]["readOn"] == floors.IPCC_READ_ON


def test_ipcc_recheck_fails_closed_on_another_file(tmp_path):
    """`floors.py --check-ipcc <pdf>` re-reads Table 7.15 from the chapter PDF; any other file is refused (hash)."""
    import pytest

    import floors

    f = tmp_path / "not_the_chapter.pdf"
    f.write_bytes(b"%PDF-1.4 not the IPCC chapter")
    with pytest.raises(ValueError, match="SHA-256"):
        floors.check_ipcc(str(f))


def _final_scheduled(pol: dict, detected_day: int) -> bool:
    """PoolInstallments._tryFinal's term check, in seconds: since + cureTerm + relapseGrace <= end + publicationLag."""
    core, pool = pol["core"], pol["pool"]
    since = core["start"] + detected_day * DAY + core["linkWindow"]
    grace = pool["publicationLag"] + core["challengeWindow"] + core["contestWindow"]
    return since + pool["cureTerm"] + grace <= core["start"] + core["term"] + pool["publicationLag"]


def test_final_reach_matches_the_contract_term_check():
    for pol in ALL.values():
        r = final_reach(pol)
        last = r["lastDetectionDay"]
        if last is None:
            assert not _final_scheduled(pol, 0)
        else:
            assert _final_scheduled(pol, last) and not _final_scheduled(pol, last + 1)
        need = pol["core"]["linkWindow"] + pol["pool"]["cureTerm"] + pol["core"]["challengeWindow"] + \
            pol["core"]["contestWindow"]
        assert (r["minTermDays"] - 1) * DAY < need <= r["minTermDays"] * DAY  # whole days, rounded up
        assert r["nonFinalShareBp"] == sum(s for _, s, _, final in pol["pool"]["installments"] if not final)


def test_final_reach_values():
    assert final_reach(policy.POLICY_V2) == {"minTermDays": 573, "lastDetectionDay": None,
                                                    "nonFinalShareBp": 6000}
    assert final_reach(policy.POLICY_V2_REPLAY)["lastDetectionDay"] == 157
    assert final_reach(policy.POLICY_V2_MULTIYEAR)["lastDetectionDay"] == 522


def test_multiyear_differs_from_main_only_in_term():
    """POLICY_V2_MULTIYEAR: the same clause over three contract years; its pool part (and so the params hash a pool
    emits) is POLICY_V2's, and every episode of the first contract year reaches the final installment."""
    m, main = policy.POLICY_V2_MULTIYEAR, policy.POLICY_V2
    assert m["pool"] == main["pool"] and m["onchainParamsHash"] == main["onchainParamsHash"]
    assert {k: v for k, v in m["core"].items() if k != "term"} == {k: v for k, v in main["core"].items() if k != "term"}
    assert m["core"]["term"] == 3 * 365 * DAY
    assert final_reach(m)["lastDetectionDay"] >= 365
    assert final_reach({**m, "core": {**m["core"], "term": 2 * 365 * DAY}})["lastDetectionDay"] < 365  # 2 years: not


def test_multiyear_vector_is_current():
    """pipeline/tests/vectors/policy_v2_multiyear.json: the windows and term the forge test MultiYearTerm reads."""
    import json

    v = json.loads((ROOT / "pipeline" / "tests" / "vectors" / "policy_v2_multiyear.json").read_text())
    assert v == policy.multiyear_vector()
