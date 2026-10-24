"""Step 0 (spec-v2 §3.1): the Python CleanPass / Corroboration EIP-712 digests are pinned by a committed vector that
Foundry checks too (contracts/test/unit/CleanPassDigest.t.sol), so both languages agree byte for byte."""
from __future__ import annotations

import json

from common import ROOT
from observer import attest
from observer.service import anvil_key

VECTOR = ROOT / "pipeline" / "tests" / "vectors" / "cleanpass_v1.json"
ANVIL_KEY_3 = anvil_key(3)  # public Anvil test mnemonic, account 3 (throwaway; refused off-Anvil by the observer code)


def _msg(raw: dict, order: list[str]) -> dict:
    return {k: (attest.b32(raw[k]) if isinstance(raw[k], str) and raw[k].startswith("0x") else raw[k]) for k in order}


def test_cleanpass_digest_matches_vector():
    v = json.loads(VECTOR.read_text())
    d = attest.pool_digest("CleanPass", _msg(v["cleanPass"], attest.CLEANPASS_ORDER), v["chainId"], v["verifyingContract"])
    assert "0x" + d.hex() == v["cleanPassDigest"]


def test_corroboration_digest_matches_vector():
    v = json.loads(VECTOR.read_text())
    msg = _msg(v["corroboration"], attest.CORROBORATION_ORDER)
    d = attest.pool_digest("Corroboration", msg, v["chainId"], v["verifyingContract"])
    assert "0x" + d.hex() == v["corroborationDigest"]


def test_cleanpass_sign_recover_roundtrip():
    v = json.loads(VECTOR.read_text())
    msg = _msg(v["cleanPass"], attest.CLEANPASS_ORDER)
    sig = attest.pool_sign("CleanPass", msg, ANVIL_KEY_3, v["chainId"], v["verifyingContract"])
    assert len(sig) == 65
    assert attest.pool_recover("CleanPass", msg, sig, v["chainId"], v["verifyingContract"]) == \
        "0x90F79bf6EB2c4f870365E785982E1f101E93b906"


def test_kinds_disjoint_from_core():
    assert {attest.KIND_CLEAN, attest.KIND_CONFIRM_CLEAN, attest.KIND_WITHDRAW_CLEAN, attest.KIND_CORROBORATE} \
        .isdisjoint({attest.KIND_DETECT, attest.KIND_CONFIRM, attest.KIND_WITHDRAW})
