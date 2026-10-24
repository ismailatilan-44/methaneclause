"""Credit notes (spec-v2 §5, §15): VERDICT_ONLY verdicts become credit notes in policy units ×100, never payments;
supersession is an off-chain export step (the on-chain CreditNoteSuperseded event is reserved, never emitted)."""
from __future__ import annotations

import json

import pytest
from eth_abi import encode
from eth_utils import keccak

from export import credit_note as cn
from policy import POLICY_V2_VERDICT_ONLY

POOL = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"
COVENANT = "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512"
CLAIM = "0x" + "ab" * 32
UNITS = POLICY_V2_VERDICT_ONLY["pool"]["unitTierAmounts"]


def ref_for(chain_id, contract, item_id):
    return "0x" + keccak(encode(["uint256", "address", "uint256"], [chain_id, contract, item_id])).hex()


def due_event(installment_id=3, amount=None):
    amount = UNITS[2] * 3000 // 10000 if amount is None else amount
    return {"event": "CreditNoteDue", "claimId": CLAIM, "installmentId": installment_id, "amount": amount,
            "ref": ref_for(31337, POOL, installment_id), "tx": "0x" + "12" * 32, "blockTime": 1790000000}


def verdict(state="CONFIRMED", tier=2):
    return {"chain": "Anvil (local) [SIM]", "chainId": 31337, "covenantContract": COVENANT, "covenantId": "0x" + "00" * 31
            + "02", "episodeId": 1, "state": state, "tier": tier, "tx": "0x" + "34" * 32, "blockTime": 1789000000}


def test_rebate_note_from_a_confirmed_verdict():
    n = cn.rebate_note(verdict(), case="TKM_S_276")
    assert n["kind"] == "rebate" and n["status"] == "due"
    assert n["amount"]["units"] == UNITS[1] == 300 and n["amount"]["display"] == "3.00 policy units"
    assert n["payer"].startswith("[SIM] seller") and n["payee"].startswith("[SIM] buyer")
    assert n["ref"] == ref_for(31337, COVENANT, 1) and n["refSource"].startswith("derived off chain")
    assert "not a payment instrument" in n["settlement"] and "CBRT" in n["law"]


def test_no_credit_note_before_the_verdict():
    for state in ("PROPOSED", "CHALLENGED", "CONTESTED", "VOID", "EXPIRED", "LINKED"):
        with pytest.raises(cn.NotDue):
            cn.rebate_note(verdict(state), case="TKM_S_276")


def test_refund_note_follows_the_pool_event():
    n = cn.refund_note(due_event(), case="TKM_S_276", chain="Anvil (local) [SIM]", chain_id=31337, pool=POOL,
                       cap_units=UNITS[2], share_bp=3000)
    assert n["kind"] == "refund" and n["amount"]["units"] == 30 and n["amount"]["display"] == "0.30 policy units"
    assert n["ref"] == due_event()["ref"] and n["refSource"] == "CreditNoteDue event (on chain)"
    assert n["payer"].startswith("[SIM] buyer") and n["payee"].startswith("[SIM] seller")
    assert n["verdict"]["event"] == "CreditNoteDue" and n["verdict"]["installmentId"] == 3


def test_refund_note_refuses_a_ref_that_is_not_the_pools():
    ev = due_event()
    ev["ref"] = ref_for(31337, POOL, 4)  # another installment's ref
    with pytest.raises(ValueError):
        cn.refund_note(ev, case="TKM_S_276", chain="Anvil", chain_id=31337, pool=POOL)
    with pytest.raises(ValueError):
        cn.refund_note(due_event(), case="TKM_S_276", chain="Anvil", chain_id=11155111, pool=POOL)  # other chain


def test_refund_note_refuses_zero_or_inconsistent_amounts():
    with pytest.raises(ValueError):
        cn.refund_note(due_event(amount=0), case="TKM_S_276", chain="Anvil", chain_id=31337, pool=POOL)
    with pytest.raises(ValueError):
        cn.refund_note(due_event(amount=31), case="TKM_S_276", chain="Anvil", chain_id=31337, pool=POOL,
                       cap_units=UNITS[2], share_bp=3000)


def test_supersession_is_an_off_chain_export_step():
    old = cn.refund_note(due_event(3), case="TKM_S_276", chain="Anvil", chain_id=31337, pool=POOL)
    new = cn.refund_note(due_event(5), case="TKM_S_276", chain="Anvil", chain_id=31337, pool=POOL)
    o2, n2 = cn.supersede(old, new, reason="installment re-scheduled after a top-up [SIM]")
    assert o2["status"] == "superseded" and o2["supersededBy"] == new["ref"] and n2["supersedes"] == old["ref"]
    assert o2["supersession"]["onChainEvent"] is None and "never emitted" in o2["supersession"]["note"]
    assert old["status"] == "due"  # inputs untouched (append-only register)
    with pytest.raises(ValueError):
        cn.supersede(o2, new, reason="twice")
    with pytest.raises(ValueError):
        cn.supersede(old, old, reason="self")
    rebate = cn.rebate_note(verdict(), case="TKM_S_276")
    with pytest.raises(ValueError):
        cn.supersede(old, rebate, reason="kind change")


def test_register_round_trips_json_and_csv():
    old = cn.refund_note(due_event(3), case="TKM_S_276", chain="Anvil", chain_id=31337, pool=POOL)
    new = cn.refund_note(due_event(5), case="TKM_S_276", chain="Anvil", chain_id=31337, pool=POOL)
    o2, n2 = cn.supersede(old, new, reason="[SIM]")
    reg = cn.register("TKM_S_276", [cn.rebate_note(verdict(), case="TKM_S_276"), o2, n2])
    assert cn.register_hash(cn.from_json(json.dumps(reg))) == cn.register_hash(reg)
    assert cn.register_hash(cn.from_csv(cn.to_csv(reg))) == cn.register_hash(reg)
    assert [n["noteHash"] for n in reg["notes"]] == [cn.note_hash(n) for n in reg["notes"]]


def test_import_refuses_an_edited_note():
    reg = cn.register("TKM_S_276", [cn.rebate_note(verdict(), case="TKM_S_276")])
    reg["notes"][0]["amount"]["units"] = 500
    with pytest.raises(ValueError):
        cn.from_json(json.dumps(reg))


def test_wording_never_claims_a_payment_or_an_on_chain_supersession():
    n = cn.rebate_note(verdict(), case="TKM_S_276")
    text = json.dumps(n).lower()
    assert "not a payment instrument" in text and "not legal advice" in text
    assert "creditnotesuperseded" not in text or "never emitted" in text


def test_ref_matches_the_cast_vector():
    # cast keccak $(cast abi-encode "f(uint256,address,uint256)" 31337 0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2 3)
    assert cn.derive_ref(31337, POOL, 3) == "0xee5846a7c146b6b39f1a244cb86326bbab6e2c93e7da6832ef64457fa7592b2a"


@pytest.mark.parametrize("column, value", [("units", "500"), ("display", "5.00 policy units"), ("payee", "someone"),
                                           ("status", "superseded"), ("ref", "0x" + "00" * 32)])
def test_csv_import_refuses_an_edited_visible_column(column, value):
    import csv
    import io

    reg = cn.register("TKM_S_276", [cn.rebate_note(verdict(), case="TKM_S_276")])
    rows = list(csv.reader(io.StringIO(cn.to_csv(reg))))
    rows[2][cn.CSV_COLUMNS.index(column)] = value
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    with pytest.raises(ValueError):
        cn.from_csv(buf.getvalue())


def _csv(reg, mutate):
    import csv
    import io

    rows = list(csv.reader(io.StringIO(cn.to_csv(reg))))
    mutate(rows)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()


def test_csv_import_refuses_a_case_cell_that_differs_from_the_notes():
    reg = cn.register("TKM_S_276", [cn.rebate_note(verdict(), case="TKM_S_276")])
    with pytest.raises(ValueError):
        cn.from_csv(_csv(reg, lambda rows: rows[0].__setitem__(1, "DZA_S_012")))


def test_csv_import_refuses_a_short_row_with_value_error():
    reg = cn.register("TKM_S_276", [cn.rebate_note(verdict(), case="TKM_S_276")])
    with pytest.raises(ValueError):
        cn.from_csv(_csv(reg, lambda rows: rows.__setitem__(2, rows[2][:3])))
