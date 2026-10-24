"""make verify-evidence: the keyless re-check of the committed Sepolia record (scripts/verify_evidence.py).

The offline mode replays recorded public-RPC answers (scripts/tests/fixtures/verify_evidence/rpc.json) through the same
code path as the live mode. The committed record must pass; every edit to a recorded chain answer or to a committed
record below must turn its row to FAIL and the exit status to 1 (fail closed, never a crash, never a silent pass).
Needs contracts/out (forge build; make check builds it before the lane checks).
"""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("verify_evidence", ROOT / "scripts" / "verify_evidence.py")
ve = importlib.util.module_from_spec(spec)
sys.modules["verify_evidence"] = ve  # dataclasses resolve the module by name
spec.loader.exec_module(ve)

FIXTURE = ROOT / "scripts" / "tests" / "fixtures" / "verify_evidence" / "rpc.json"
RUN = json.loads((ROOT / "data" / "derived" / "testnet_run.json").read_text())
TXS = json.loads((ROOT / "data" / "derived" / "sepolia_txs.json").read_text())


def tx_of(step: str) -> str:
    return next(s["tx"] for s in RUN["steps"] if s["step"] == step).rsplit("/", 1)[-1].lower()


TAMPER, PROPOSAL = tx_of("reject_tamper"), tx_of("proposeEpisode")


@pytest.fixture(scope="module")
def recorded() -> dict:
    if not (ROOT / "contracts" / "out" / "MethaneCovenant.sol" / "MethaneCovenant.json").exists():
        pytest.fail("contracts/out missing: run `forge build` in contracts/ first")
    return json.loads(FIXTURE.read_text())


def run_with(tmp_path: Path, fixture: dict, *extra: str) -> tuple[int, dict[str, ve.Row]]:
    p = tmp_path / "rpc.json"
    p.write_text(json.dumps(fixture))
    rows: list[ve.Row] = []
    code = ve.main(["--offline", "--fixtures", str(p), *extra], sink=rows)
    return code, {r.key: r for r in rows}


def calls(fx: dict, method: str, needle: str) -> list[str]:
    return [k for k in fx["calls"] if k.startswith(method + "|") and needle.lower() in k.lower()]


# ------------------------------------------------------------------------------------------------ the record passes
def test_committed_record_passes_offline(tmp_path, recorded):
    code, rows = run_with(tmp_path, recorded)
    bad = {k: r.detail for k, r in rows.items() if not r.ok}
    assert code == 0 and not bad, bad
    for key in ("chain", "code", "core-code", "pool-code", "registry", "txs", "run-steps", "failed-txs", "tamper",
                "bundle-hash", "calldata", "eip712", "registered", "state", "tamper-calldata", "multiyear"):
        assert key in rows, f"row {key} missing"
    assert "InvalidSignature" in rows["tamper"].detail


def test_every_recorded_transaction_is_checked(tmp_path, recorded):
    _, rows = run_with(tmp_path, recorded)
    assert f"{len(TXS['transactions'])} recorded" in rows["txs"].detail
    assert len(calls(recorded, "eth_getTransactionReceipt", "0x")) >= len(TXS["transactions"])


# ----------------------------------------------------------------------------------- an edited chain answer fails
def test_multiyear_record_with_another_policy_or_covenant_fails(tmp_path, recorded):
    my = json.loads((ROOT / "data" / "derived" / "multiyear_run.json").read_text())
    for edit in ({"policyHash": "0x" + "00" * 32}, {"covenantId": RUN["covenantId"]},
                 {"finalReach": {**my["finalReach"], "lastDetectionDay": my["finalReach"]["lastDetectionDay"] + 1}}):
        p = tmp_path / "multiyear_run.json"
        p.write_text(json.dumps({**my, **edit}))
        code, rows = run_with(tmp_path, recorded, "--multiyear", str(p))
        assert code == 1 and not rows["multiyear"].ok, edit


def test_multiyear_pool_with_other_params_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    my = json.loads((ROOT / "data" / "derived" / "multiyear_run.json").read_text())
    want = my["onchainParamsHash"][2:].lower()
    (k,) = [c for c in calls(fx, "eth_call", "") if isinstance(fx["calls"][c], str) and want in fx["calls"][c]
            and my["covenantId"][2:].lower() in c.lower()]
    fx["calls"][k] = fx["calls"][k].replace(want, "11" * 32)
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["multiyear"].ok and "pool paramsHash" in rows["multiyear"].detail


def test_tamper_tx_reported_as_success_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    (k,) = calls(fx, "eth_getTransactionReceipt", TAMPER)
    fx["calls"][k]["status"] = "0x1"
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["tamper"].ok and not rows["txs"].ok and not rows["run-steps"].ok


def test_tamper_revert_with_another_error_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    (k,) = [c for c in calls(fx, "eth_call", "") if json.loads(c.split("|", 1)[1])[0].get("data", "")
            == fx["calls"][calls(fx, "eth_getTransactionByHash", TAMPER)[0]]["input"]]
    fx["calls"][k] = {"__revert__": "0x" + ve.error_selector("SelfAttestation").hex() + "00" * 32}
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["tamper"].ok and "SelfAttestation" in rows["tamper"].detail


def test_revert_data_not_retrievable_is_said_not_hidden(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    tin = fx["calls"][calls(fx, "eth_getTransactionByHash", TAMPER)[0]]["input"]
    (k,) = [c for c in calls(fx, "eth_call", "") if json.loads(c.split("|", 1)[1])[0].get("data", "") == tin]
    fx["calls"][k] = {"__rpc_error__": "missing trie node (not an archive node)"}
    code, rows = run_with(tmp_path, fx)
    assert rows["tamper"].ok and "status 0" in rows["tamper"].detail
    assert "not retrievable" in rows["tamper"].detail


def test_edited_contract_code_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    (k,) = [c for c in calls(fx, "eth_getCode", RUN["methaneCovenant"])]
    code_hex = fx["calls"][k]
    mid = len(code_hex) // 2
    fx["calls"][k] = code_hex[:mid] + ("00" if code_hex[mid:mid + 2] != "00" else "01") + code_hex[mid + 2:]
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["core-code"].ok


def test_missing_code_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    (k,) = [c for c in calls(fx, "eth_getCode", RUN["keyRegistry"])]
    fx["calls"][k] = "0x"
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["code"].ok


def test_edited_signature_in_calldata_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    (k,) = calls(fx, "eth_getTransactionByHash", PROPOSAL)
    inp = fx["calls"][k]["input"]
    sig_a = ve.decode_propose(inp)["sigs"][0].hex()
    fx["calls"][k]["input"] = inp.replace(sig_a, sig_a[:20] + ("0" if sig_a[20] != "0" else "1") + sig_a[21:])
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["eip712"].ok


def test_unregistered_signer_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    for k in calls(fx, "eth_call", ""):
        params = json.loads(k.split("|", 1)[1])
        if params[0].get("to", "").lower() == RUN["keyRegistry"].lower() and isinstance(fx["calls"][k], str):
            fx["calls"][k] = "0x" + "00" * (len(fx["calls"][k]) // 2 - 1)  # getKey -> the empty Key (role NONE)
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["registered"].ok


def test_missing_recorded_answer_fails_closed(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    (k,) = calls(fx, "eth_getTransactionReceipt", PROPOSAL)
    del fx["calls"][k]
    code, rows = run_with(tmp_path, fx)
    assert code == 1 and not rows["txs"].ok and "no recorded answer" in rows["txs"].detail


# --------------------------------------------------------------------------------- an edited committed record fails
def test_edited_recorded_status_fails(tmp_path, recorded):
    txs = copy.deepcopy(TXS)
    t = next(t for t in txs["transactions"] if t["tx"] == PROPOSAL)
    t["status"] = 0
    p = tmp_path / "txs.json"
    p.write_text(json.dumps(txs))
    code, rows = run_with(tmp_path, recorded, "--txs", str(p))
    assert code == 1 and not rows["txs"].ok and PROPOSAL[:12] in rows["txs"].detail


def test_edited_recorded_evidence_hash_fails(tmp_path, recorded):
    run = copy.deepcopy(RUN)
    run["evidenceHash"] = "0x" + "11" * 32
    p = tmp_path / "run.json"
    p.write_text(json.dumps(run))
    code, rows = run_with(tmp_path, recorded, "--run", str(p))
    assert code == 1 and not rows["bundle-hash"].ok


def test_edited_deployment_record_fails(tmp_path, recorded):
    dep = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
    dep["observerA"], dep["observerC"] = dep["observerC"], dep["observerA"]
    d = tmp_path / "deployments"
    d.mkdir()
    (d / "11155111.json").write_text(json.dumps(dep))
    shutil.copy(ROOT / "contracts" / "deployments" / "core_bytecode.sha256", d)
    code, rows = run_with(tmp_path, recorded, "--deployments", str(d / "11155111.json"))
    assert code == 1 and not rows["eip712"].ok


# ------------------------------------------------------------------------- masked bytes are read back, not skipped
DEP = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())


def getter_key(fx: dict, to: str, contract: str, name: str) -> str:
    data = "0x" + ve.selector(ve.function(contract, name)).hex()
    (k,) = [c for c in calls(fx, "eth_call", "") if json.loads(c.split("|", 1)[1])[0].get("to", "").lower() == to.lower()
            and json.loads(c.split("|", 1)[1])[0].get("data") == data]
    return k


def test_edited_immutable_value_fails(tmp_path, recorded):
    """Same bytes as the artifact everywhere else; one immutable (all its references) set to another value."""
    fx = copy.deepcopy(recorded)
    (k,) = calls(fx, "eth_getCode", DEP["methaneCovenant"])
    code = bytearray(bytes.fromhex(fx["calls"][k][2:]))
    refs = next(iter(ve.artifact("MethaneCovenant")["deployedBytecode"]["immutableReferences"].values()))
    for r in refs:
        code[r["start"]:r["start"] + r["length"]] = (12345).to_bytes(32, "big")
    fx["calls"][k] = "0x" + code.hex()
    rc, rows = run_with(tmp_path, fx)
    assert rc == 1 and not rows["core-code"].ok and "immutables" in rows["core-code"].detail


def test_bond_getter_differs_from_record_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    k = getter_key(fx, DEP["methaneCovenant"], "MethaneCovenant", "proposerBond")
    fx["calls"][k] = "0x" + (1).to_bytes(32, "big").hex()
    rc, rows = run_with(tmp_path, fx)
    assert rc == 1 and not rows["core-code"].ok and "proposerBond" in rows["core-code"].detail


def test_pool_covenant_getter_differs_from_record_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    fx["calls"][getter_key(fx, DEP["incentivePool"], "IncentivePool", "covenant")] = "0x" + "00" * 12 + "11" * 20
    rc, rows = run_with(tmp_path, fx)
    assert rc == 1 and not rows["pool-code"].ok and "covenant()" in rows["pool-code"].detail


def test_core_and_pool_rows_name_what_is_masked(tmp_path, recorded):
    _, rows = run_with(tmp_path, recorded)
    for w in ("registry", "proposerBond", "challengerBond", "chain id", "domain separator"):
        assert w in rows["core-code"].detail
    for w in ("covenant", "registry", "link slots", "call guard"):
        assert w in rows["pool-code"].detail


# --------------------------------------------------------------------------------- the tamper row's two claims
def test_readme_without_the_tamper_link_fails(tmp_path, recorded):
    readme = tmp_path / "README.md"
    readme.write_text((ROOT / "README.md").read_text(encoding="utf-8").replace(TAMPER, "0x" + "ab" * 32),
                      encoding="utf-8")
    rc, rows = run_with(tmp_path, recorded, "--readme", str(readme))
    assert rc == 1 and not rows["tamper"].ok and "does not link" in rows["tamper"].detail


def test_tamper_signer_with_an_organisation_fails(tmp_path, recorded):
    fx = copy.deepcopy(recorded)
    sel = "0x" + ve.selector(ve.function("KeyRegistry", "keyStatus")).hex()
    ks = [c for c in calls(fx, "eth_call", "") if json.loads(c.split("|", 1)[1])[0].get("data", "").startswith(sel)]
    assert ks, "keyStatus answers recorded"
    for k in ks:
        fx["calls"][k] = "0x" + "77" * 32 + fx["calls"][k][66:]
    rc, rows = run_with(tmp_path, fx)
    assert rc == 1 and not rows["tamper-calldata"].ok


# ------------------------------------------------------------------------------ a malformed endpoint answer
class _Resp:
    def __init__(self, body):
        self.body = body

    def json(self):
        return self.body


class _Session:
    """A public endpoint answering a JSON-RPC error as a bare string (seen once live: a rate-limit message)."""

    def __init__(self, body):
        self.body, self.headers, self.posts = body, {}, 0

    def post(self, url, json=None, timeout=None):
        self.posts += 1
        return _Resp(self.body)


CALL = [{"to": "0x" + "00" * 20, "data": "0x"}, "latest"]
STRING_ERROR = {"jsonrpc": "2.0", "id": 1, "error": "rate limited, retry later"}


def test_string_error_from_one_endpoint_moves_on_to_the_next():
    live = ve.LiveRpc(["https://a.invalid", "https://b.invalid"], backoff=0)
    bad = _Session(STRING_ERROR)
    good = _Session({"jsonrpc": "2.0", "id": 1, "error": {"code": 3, "message": "execution reverted",
                                                          "data": "0x8baa579f"}})
    live.inners[0].s, live.inners[1].s = bad, good
    assert live("eth_call", CALL, allow_error=True) == {"code": 3, "data": "0x8baa579f"}
    assert bad.posts == 1 and good.posts == 1


def test_string_error_everywhere_is_an_rpc_row_not_a_crash():
    live = ve.LiveRpc(["https://a.invalid"], backoff=0)
    live.inners[0].s = bad = _Session(STRING_ERROR)
    with pytest.raises(ve.RpcError, match="AttributeError"):
        live("eth_call", CALL, allow_error=True)
    assert bad.posts == 3  # retried twice
    assert "__rpc_error__" in live.calls[ve._key("eth_call", CALL)]

    rows = {r.key: r for r in ve.run_checks(ve.Memo(live), TXS, RUN, DEP, "")}
    assert not rows["chain"].ok and rows["chain"].detail.startswith("RPC:")
    assert not rows["failed-txs"].ok and rows["failed-txs"].detail.startswith("RPC:")
    assert not rows["tamper"].ok and not rows["txs"].ok
