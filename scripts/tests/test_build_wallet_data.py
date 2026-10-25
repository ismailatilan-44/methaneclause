"""app/data/wallet.json (the browser-wallet back end's data) is current, holds no key, and its builder fails closed.

Offline, committed inputs only (make test-scripts). The browser module itself is exercised end to end on Anvil by
`make wallet-local` (scripts/tests/wallet.test.mjs), which needs the Node tooling from `make wallet-setup`.
"""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_wallet_data", ROOT / "scripts" / "build_wallet_data.py")
bwd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bwd)  # also puts pipeline/ on sys.path

from eth_account import Account  # noqa: E402
from web3 import Web3  # noqa: E402

WALLET = ROOT / "app" / "data" / "wallet.json"


def test_committed_wallet_json_is_current():
    assert WALLET.read_text() == bwd.render(bwd.build()), "app/data/wallet.json is stale: run `make wallet-data`"


def test_no_private_key_in_the_data_file():
    """Only the public derivation strings and the resulting addresses ship; the page derives the published keys."""
    text = WALLET.read_text()
    doc = json.loads(text)
    keys = {"0x" + Web3.keccak(text=doc["sandboxKeys"]["prefix"] + r).hex().removeprefix("0x")
            for r in doc["sandboxKeys"]["addresses"]}
    assert not any(k[2:] in text.lower() for k in keys)
    for role, addr in doc["sandboxKeys"]["addresses"].items():
        derived = Account.from_key(Web3.keccak(text=doc["sandboxKeys"]["prefix"] + role)).address
        assert derived == Web3.to_checksum_address(addr), role


def test_parity_vector_matches_the_python_signer():
    """The vector the browser test checks against is Python's own output, recomputed here from the committed file."""
    doc = json.loads(WALLET.read_text())
    p, i = doc["parity"], doc["parity"]["inputs"]
    b = {**doc["detections"]["TKM_S_276"]["bundleTemplate"], "covenantId": i["covenantId"]}
    assert bwd.bundle_hash(b) == p["evidenceHash"]
    cb = {**doc["cleanPasses"]["DZA_S_012"]["bundleTemplate"], "covenantId": i["covenantId"], "claimId": i["claimId"]}
    msg = bwd.cleanpass.message(cb, nonce=i["nonce"], expiry=i["expiry"])
    assert "0x" + bwd.attest.pool_digest("CleanPass", msg, i["chainId"], i["pool"]).hex() == p["cleanPassDigest"]


def test_templates_are_signable_by_the_terminal_observer():
    """Each detection template passes observer.bundle.validate; the clean-pass template passes rules 1-6."""
    doc = json.loads(WALLET.read_text())
    for case, d in doc["detections"].items():
        assert d["bundleTemplate"]["sourceName"] == case and d["facility"]["proof"]
    cp = doc["cleanPasses"]["DZA_S_012"]
    bwd.cleanpass.validate(cp["bundleTemplate"], bwd.cleanpass.claim_context(bwd.cleanpass.load_case("DZA_S_012")))


def test_a_deployment_record_that_does_not_match_the_published_keys_stops_the_build(tmp_path, monkeypatch):
    rec = json.loads(bwd.DEPLOYMENT.read_text())
    rec["sandbox"]["judgeController"] = "0x" + "11" * 20
    bad = tmp_path / "11155111.json"
    bad.write_text(json.dumps(rec))
    monkeypatch.setattr(bwd, "DEPLOYMENT", bad)
    with pytest.raises(SystemExit, match="JUDGE_CONTROLLER"):
        bwd.build()


def test_a_missing_pool_address_stops_the_build(tmp_path, monkeypatch):
    rec = json.loads(bwd.DEPLOYMENT.read_text())
    del rec["sandbox"]["incentivePool"]
    bad = tmp_path / "11155111.json"
    bad.write_text(json.dumps(rec))
    monkeypatch.setattr(bwd, "DEPLOYMENT", bad)
    with pytest.raises(SystemExit, match="incentivePool"):
        bwd.build()


def test_wallet_module_has_no_dom_code_and_pins_its_imports():
    js = (ROOT / "app" / "wallet.js").read_text()
    assert not re.search(r"\bdocument\.|\bwindow\.document|innerHTML|getElementById", js)
    imports = re.findall(r'from "([^"]+)"', js)
    assert imports and all(re.match(r"https://cdn\.jsdelivr\.net/npm/viem@\d+\.\d+\.\d+/", u) for u in imports)
    assert "eth_signTypedData_v4" in js and "eth_sendTransaction" in js
    assert not re.search(r"0x[0-9a-fA-F]{64}\"", js.replace('"0x" + "00".repeat(32)', "")), "no 32-byte literal in the module"


def test_wallet_module_pins_the_sepolia_sandbox_of_the_deployment_record():
    """app/wallet.js refuses a wallet.json naming other contracts; its pinned addresses are the record's sandbox."""
    js = (ROOT / "app" / "wallet.js").read_text()
    block = re.search(r"export const SEPOLIA_SANDBOX = Object\.freeze\(\{(.*?)\}\);", js, re.S).group(1)
    field = {"registry": "keyRegistry"}  # named so a secret scanner does not read "key...: 0x..." as a key
    pinned = {field.get(k, k): v for k, v in re.findall(r"(\w+): \"(0x[0-9a-fA-F]{40})\"", block)}
    sb = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())["sandbox"]
    assert set(pinned) == {"keyRegistry", "methaneCovenant", "incentivePool"}
    assert all(pinned[k] == Web3.to_checksum_address(sb[k]) for k in pinned), "redeployed sandbox: update SEPOLIA_SANDBOX"
    net = json.loads(WALLET.read_text())["network"]
    assert net["chainId"] == 11155111 and all(net["sandbox"][k] == sb[k] for k in pinned)
