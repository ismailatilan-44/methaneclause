"""scripts/verify_bundle.py --rpc checks what MethaneCovenant checks before it accepts a bundle (C1).

The recorded inputs are a demo run (Anvil, `observer.demo`, public test keys) and the read-only JSON-RPC answers its
`--rpc` run read from that chain (scripts/tests/fixtures/verify_evidence/bundle_*.json, written with `--record`). The
replay goes through the same code path as a live `--rpc` run. The recorded run must pass; each edit below makes one
signer fail a rule of `MethaneCovenant._checkAttestation` / `_verifyQuorum`, and the exit status must turn to 1.
Needs contracts/out (forge build; make check builds it before the lane checks).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest
from eth_abi import decode, encode

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "scripts" / "tests" / "fixtures" / "verify_evidence"
RUN, ANSWERS = FIX / "bundle_run.json", FIX / "bundle_rpc.json"
KEY_T = "(bytes32,uint8,uint8,uint64,uint64,bytes32,address)"  # KeyRegistry.Key, as getKey returns it


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses resolve the module by name
    spec.loader.exec_module(mod)
    return mod


vb = _load("verify_bundle", ROOT / "scripts" / "verify_bundle.py")
ve = sys.modules.get("verify_evidence") or _load("verify_evidence", ROOT / "scripts" / "verify_evidence.py")


@pytest.fixture(scope="module")
def recorded() -> tuple[dict, dict]:
    if not (ROOT / "contracts" / "out" / "MethaneCovenant.sol" / "MethaneCovenant.json").exists():
        pytest.fail("contracts/out missing: run `forge build` in contracts/ first")
    return json.loads(RUN.read_text()), json.loads(ANSWERS.read_text())


DEPLOYMENTS = FIX / "deployments"  # the Anvil record Deploy.s.sol wrote for the recorded run (31337.json)


def replay(tmp_path: Path, run: dict, answers: dict, capsys, deployments: Path = DEPLOYMENTS) -> tuple[int, str]:
    rp, ap = tmp_path / "run.json", tmp_path / "rpc.json"
    rp.write_text(json.dumps(run))
    ap.write_text(json.dumps(answers))
    code = vb.main([str(rp), "--replay", str(ap), "--deployments", str(deployments)])
    return code, capsys.readouterr().out


def getkey_call(answers: dict, run: dict, signer: str) -> str:
    sel = "0x" + ve.selector(ve.function("KeyRegistry", "getKey")).hex()
    reg = run["deployment"]["keyRegistry"].lower()
    (k,) = [c for c in answers["calls"] if c.startswith("eth_call|")
            and json.loads(c.split("|", 1)[1])[0].get("to", "").lower() == reg
            and json.loads(c.split("|", 1)[1])[0].get("data", "").lower() == sel + "00" * 12 + signer[2:].lower()]
    return k


def edit_key(answers: dict, run: dict, signer: str, **fields) -> dict:
    """The recorded getKey(signer) answer with some KeyRegistry.Key fields replaced."""
    fx = copy.deepcopy(answers)
    k = getkey_call(fx, run, signer)
    (key,) = decode([KEY_T], bytes.fromhex(fx["calls"][k][2:]))
    names = ["orgId", "role", "algId", "validFrom", "validUntil", "pqPubKeyHash", "replacedBy"]
    key = dict(zip(names, key), **fields)
    fx["calls"][k] = "0x" + encode([KEY_T], [tuple(key[n] for n in names)]).hex()
    return fx


def proposal_time(answers: dict) -> int:
    (b,) = [v for c, v in answers["calls"].items() if c.startswith("eth_getBlockByNumber|")]
    return int(b["timestamp"], 16)


def signer(run: dict, org: str) -> str:
    return next(e["signer"] for e in run["hybrid"] if e["org"] == org)


# ------------------------------------------------------------------------------------------------ the record passes
def test_recorded_rpc_run_passes(tmp_path, recorded, capsys):
    run, answers = recorded
    code, out = replay(tmp_path, run, answers, capsys)
    assert code == 0, out
    assert "EpisodeProposed" in out and "distinct observer organisations" in out
    # OBS_B's key was revoked later in the demo run: valid at the proposal is what the contract checked, so it passes
    k = getkey_call(answers, run, signer(run, "OBS_B"))
    (key,) = decode([KEY_T], bytes.fromhex(answers["calls"][k][2:]))
    assert key[4] > proposal_time(answers), "the recorded run should show a key revoked after the proposal"


def test_recorded_run_matches_its_deployment_record(recorded):
    run, _ = recorded
    rec = json.loads((DEPLOYMENTS / "31337.json").read_text())
    assert {k: rec[k] for k in ("methaneCovenant", "keyRegistry")} == \
        {k: run["deployment"][k] for k in ("methaneCovenant", "keyRegistry")}


# ------------------------------------------------- the covenant must be the recorded deployment (N1, before any read)
def test_verify_bundle_rpc_refuses_covenant_not_in_deployment_record(tmp_path, recorded, capsys):
    """Genuine MethaneCovenant bytecode deployed by anyone over a registry whose observer orgs they control passes every
    on-chain rule; --rpc refuses a run whose covenant or registry is not the chain's deployment record, or a chain
    with no record, before it reads anything (the replay below holds answers for the recorded run only)."""
    run, answers = recorded
    rogue = "0x" + "c0" * 20
    for key in ("methaneCovenant", "keyRegistry"):
        other = copy.deepcopy(run)
        other["deployment"][key] = rogue
        code, out = replay(tmp_path, other, answers, capsys)
        assert code == 1 and f"run {key} {rogue} != deployment record 31337.json" in out, out
        assert "no recorded answer" not in out  # refused before any chain read

    empty = tmp_path / "no-records"
    empty.mkdir()
    code, out = replay(tmp_path, run, answers, capsys, deployments=empty)
    assert code == 1 and "no deployment record for chain 31337" in out and "refuses" in out

    code, out = replay(tmp_path, run, answers, capsys)  # the recorded run against its record: accepted
    assert code == 0
    assert f"covenant {run['deployment']['methaneCovenant']} on chain 31337" in out
    assert f"run methaneCovenant {run['deployment']['methaneCovenant']} = deployment record 31337.json" in out


# ---------------------------------------------------------------------- each contract rule, one edited answer each
def test_verify_bundle_rejects_key_revoked_before_proposal(tmp_path, recorded, capsys):
    """KeyRegistry.validUntil is an exclusive end: a key closed at (or before) the proposal's block time is RevokedKey."""
    run, answers = recorded
    when = proposal_time(answers)
    for until in (when, when - 1):
        code, out = replay(tmp_path, run, edit_key(answers, run, signer(run, "OBS_A"), validUntil=until), capsys)
        assert code == 1 and "RevokedKey" in out, out


def test_verify_bundle_passes_after_signer_rotated_later(tmp_path, recorded, capsys):
    """A bundle stays verifiable after its signer rotates: the registry keeps the old key's record with its validity
    window (validUntil closed after the proposal, replacedBy set), and validity at the proposal is what counts."""
    run, answers = recorded
    later, successor = proposal_time(answers) + 30 * 86400, "0x" + "5e" * 20
    for org in ("OBS_A", "OBS_B"):
        fx = edit_key(answers, run, signer(run, org), validUntil=later, replacedBy=successor)
        k = getkey_call(fx, run, signer(run, org))
        (key,) = decode([KEY_T], bytes.fromhex(fx["calls"][k][2:]))
        assert key[4] == later and key[6].lower() == successor  # the edit took
        code, out = replay(tmp_path, run, fx, capsys)
        assert code == 0, out
        assert "distinct observer organisations" in out


def test_verify_bundle_rejects_key_registered_after_proposal(tmp_path, recorded, capsys):
    run, answers = recorded
    fx = edit_key(answers, run, signer(run, "OBS_A"), validFrom=proposal_time(answers) + 1)
    code, out = replay(tmp_path, run, fx, capsys)
    assert code == 1 and "RevokedKey" in out


def test_verify_bundle_rejects_non_observer_role(tmp_path, recorded, capsys):
    run, answers = recorded
    seller_role = ve.role_index("SELLER")
    code, out = replay(tmp_path, run, edit_key(answers, run, signer(run, "OBS_B"), role=seller_role), capsys)
    assert code == 1 and "AttestorNotIndependent" in out


def test_verify_bundle_rejects_signer_in_seller_org(tmp_path, recorded, capsys):
    run, answers = recorded
    seller_org = vb.covenant_of(run, ve.Memo(ve.FixtureRpc(ANSWERS)))["sellerOrg"]
    code, out = replay(tmp_path, run, edit_key(answers, run, signer(run, "OBS_A"), orgId=seller_org), capsys)
    assert code == 1 and "SelfAttestation" in out


def test_verify_bundle_rejects_two_keys_of_one_org(tmp_path, recorded, capsys):
    """k = 2 distinct organisations: two OBSERVER keys of one org are one vote (QuorumNotMet)."""
    run, answers = recorded
    k = getkey_call(answers, run, signer(run, "OBS_A"))
    (key_a,) = decode([KEY_T], bytes.fromhex(answers["calls"][k][2:]))
    code, out = replay(tmp_path, run, edit_key(answers, run, signer(run, "OBS_B"), orgId=key_a[0]), capsys)
    assert code == 1 and "QuorumNotMet" in out


def test_verify_bundle_rejects_bundle_the_covenant_never_accepted(tmp_path, recorded, capsys):
    run, answers = recorded
    fx = copy.deepcopy(answers)
    for c in [c for c in fx["calls"] if c.startswith("eth_getLogs|")]:
        fx["calls"][c] = []
    code, out = replay(tmp_path, run, fx, capsys)
    assert code == 1 and "never accepted" in out


def proposal_logs(fx: dict) -> list[dict]:
    (k,) = [c for c in fx["calls"] if c.startswith("eth_getLogs|")]
    (lg,) = [lg for lg in fx["calls"][k] if ve.decode_log("MethaneCovenant", lg)[0] == "EpisodeProposed"]
    return [lg]


def test_verify_bundle_rejects_log_with_other_evidence_hash(tmp_path, recorded, capsys):
    """The acceptance log must carry this bundle's evidenceHash and this covenant's id; another one is no acceptance."""
    run, answers = recorded
    fx = copy.deepcopy(answers)
    (lg,) = proposal_logs(fx)
    lg["data"] = "0x" + "ab" * 32 + lg["data"][66:]  # EpisodeProposed's first data word is evidenceHash
    code, out = replay(tmp_path, run, fx, capsys)
    assert code == 1 and "never accepted" in out

    fx = copy.deepcopy(answers)
    (lg,) = proposal_logs(fx)
    lg["topics"][2] = "0x" + (99).to_bytes(32, "big").hex()  # topics: signature, episodeId, covenantId, sourceId
    code, out = replay(tmp_path, run, fx, capsys)
    assert code == 1 and "never accepted" in out


def test_verify_bundle_without_proposal_tx_scans_in_chunks(tmp_path, recorded, capsys):
    """A run that names no proposal transaction: blocks 0..latest are read in LOG_CHUNK spans, and the log is found."""
    run, answers = recorded
    run = {k: v for k, v in run.items() if k != "proposal"}
    fx = copy.deepcopy(answers)
    (k,) = [c for c in fx["calls"] if c.startswith("eth_getLogs|")]
    logs, (flt,) = fx["calls"].pop(k), json.loads(k.split("|", 1)[1])
    latest = int(logs[0]["blockNumber"], 16) + ve.LOG_CHUNK  # two chunks: [0, CHUNK-1], [CHUNK, latest]
    fx["calls"][ve._key("eth_blockNumber", [])] = hex(latest)
    for lo, hi, got in ((0, ve.LOG_CHUNK - 1, logs), (ve.LOG_CHUNK, latest, [])):
        fx["calls"][ve._key("eth_getLogs", [{**flt, "fromBlock": hex(lo), "toBlock": hex(hi)}])] = got
    code, out = replay(tmp_path, run, fx, capsys)
    assert code == 0 and "EpisodeProposed(episode 1)" in out, out


def test_verify_bundle_replay_fails_closed_on_a_missing_answer(tmp_path, recorded, capsys):
    run, answers = recorded
    fx = copy.deepcopy(answers)
    del fx["calls"][getkey_call(fx, run, signer(run, "OBS_A"))]
    code, out = replay(tmp_path, run, fx, capsys)
    assert code == 1 and "no recorded answer" in out
