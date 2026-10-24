"""Credit notes for VERDICT_ONLY covenants (spec-v2 §5, §15): the chain gives the verdict, the parties settle it
under the supply contract by bank transfer or credit note. Not a payment instrument; not legal advice.

Two kinds:
  rebate  a CONFIRMED core episode in a verdict-only covenant (tier amounts 0, no bond): the seller owes the buyer the
          policy's amount for the episode's tier, `POLICY_V2_VERDICT_ONLY.pool.unitTierAmounts[tier - 1]`
  refund  the pool's `CreditNoteDue(claimId, installmentId, amount, ref)` event: the buyer (or the sponsor the clause
          names) owes the seller a released installment of the capped cure refund
Amounts are policy units x100 (spec-v2 §15: 100 = one policy unit, so every 30/30/40 share is a whole number); a
parameter of the supply contract, not a price.

`ref` of a refund is the event's `ref` = keccak256(abi.encode(chainId, pool, installmentId)), re-derived and checked
here (a mismatch is refused). A rebate has no on-chain ref; it is derived off chain the same way from (chainId,
covenant contract, episodeId) and labelled so.

Supersession is an off-chain export step in v2: `supersede(old, new)` marks the old note superseded and links the new
ref, append-only. The on-chain `CreditNoteSuperseded` event is reserved and never emitted (spec-v2 §15 A3 audit (4)),
so no note claims an on-chain supersession.

    python -m export.credit_note --case TKM_S_276 --verdict verdict.json      # rebate
    python -m export.credit_note --case TKM_S_276 --event credit_note_due.json --chain-id 31337 --pool 0x..
    -> data/derived/export/<case>.credit_note.{json,csv}
"""
from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import io
import json

from eth_abi import encode
from eth_utils import keccak, to_checksum_address

from canon import canonical_bytes
from common import DERIVED
from policy import POLICY_V2_VERDICT_ONLY

SCHEMA = "methaneclause.credit_note/1"
REGISTER_SCHEMA = "methaneclause.credit_notes/1"
OUT = DERIVED / "export"
UNITS_DEFINITION = ("policy units x100: 100 = one policy unit; a parameter of the supply contract, not "
                    "a price; no token and no payment instrument is involved")
SETTLEMENT = ("Settle under clause X of the supply contract by bank transfer or credit note; this is not a payment "
              "instrument.")
LAW = ("Türkiye: the CBRT regulation of 16 April 2021 bans the use of crypto-assets in payments, directly or "
       "indirectly; Law 7518 (2024) regulates crypto-asset service providers and did not change it. Not legal advice.")
SUPERSESSION_NOTE = ("off-chain export step; the on-chain CreditNoteSuperseded event is "
                     "reserved and never emitted, so the chain still shows only the original verdict")
SELLER, BUYER = "[SIM] seller", "[SIM] buyer (Türkiye-type importer)"
CSV_COLUMNS = ["noteHash", "ref", "kind", "status", "units", "display", "payer", "payee", "chain", "contract",
               "verdictId", "tx", "supersedes", "supersededBy", "json"]


class NotDue(ValueError):
    """No credit note before the chain's verdict is final."""


def derive_ref(chain_id: int, contract: str, item_id: int) -> str:
    return "0x" + keccak(encode(["uint256", "address", "uint256"],
                                [chain_id, to_checksum_address(contract), item_id])).hex()


def _display(units: int) -> str:
    return f"{units // 100}.{units % 100:02d} policy units"


def _note(*, kind, case, ref, ref_source, units, payer, payee, verdict) -> dict:
    if not isinstance(units, int) or isinstance(units, bool) or units <= 0:
        raise ValueError(f"amount must be a positive integer number of policy units x100, got {units!r}")
    n = {"schema": SCHEMA, "kind": kind, "case": case, "ref": ref, "refSource": ref_source,
         "amount": {"units": units, "display": _display(units), "definition": UNITS_DEFINITION},
         "payer": payer, "payee": payee, "verdict": verdict, "settlement": SETTLEMENT, "law": LAW,
         "status": "due", "supersedes": None, "supersededBy": None, "supersession": None}
    n["noteHash"] = note_hash(n)
    return n


def note_hash(n: dict) -> str:
    """SHA-256 of the RFC 8785 form of the note without its own hash."""
    return "0x" + hashlib.sha256(canonical_bytes({k: v for k, v in n.items() if k != "noteHash"})).hexdigest()


def rebate_note(verdict: dict, *, case: str, policy: dict = POLICY_V2_VERDICT_ONLY) -> dict:
    """verdict: {chain, chainId, covenantContract, covenantId, episodeId, state, tier, tx, blockTime} read from the
    chain (getEpisode + the confirming tx). Refused unless the episode is CONFIRMED."""
    if verdict["state"] != "CONFIRMED":
        raise NotDue(f"episode {verdict['episodeId']} is {verdict['state']}: no credit note before CONFIRMED")
    tier = verdict["tier"]
    if tier not in (1, 2, 3):
        raise ValueError(f"tier must be 1, 2 or 3, got {tier!r}")
    units = policy["pool"]["unitTierAmounts"][tier - 1]
    v = {"event": "EpisodeConfirmed (core)", **{k: verdict[k] for k in ("chain", "chainId", "covenantContract",
                                                                        "covenantId", "episodeId", "state", "tier",
                                                                        "tx", "blockTime")}}
    return _note(kind="rebate", case=case, units=units, payer=SELLER, payee=BUYER, verdict=v,
                 ref=derive_ref(verdict["chainId"], verdict["covenantContract"], verdict["episodeId"]),
                 ref_source="derived off chain: keccak256(abi.encode(chainId, covenant, episodeId)); the core emits "
                            "no credit-note ref")


def refund_note(event: dict, *, case: str, chain: str, chain_id: int, pool: str, cap_units: int | None = None,
                share_bp: int | None = None) -> dict:
    """event: the pool's CreditNoteDue log {claimId, installmentId, amount, ref, tx, blockTime}. The ref must be the
    pool's ref for that installment on that chain; with cap_units and share_bp the amount is re-derived too."""
    if event.get("event", "CreditNoteDue") != "CreditNoteDue":
        raise ValueError("not a CreditNoteDue event")
    expected = derive_ref(chain_id, pool, event["installmentId"])
    if event["ref"].lower() != expected:
        raise ValueError(f"ref {event['ref']} is not keccak256(abi.encode({chain_id}, pool, "
                         f"{event['installmentId']})) = {expected}")
    if cap_units is not None and share_bp is not None and event["amount"] != cap_units * share_bp // 10000:
        raise ValueError(f"amount {event['amount']} != cap {cap_units} x {share_bp} / 10000")
    v = {"event": "CreditNoteDue", "chain": chain, "chainId": chain_id, "pool": to_checksum_address(pool),
         "claimId": event["claimId"], "installmentId": event["installmentId"], "tx": event["tx"],
         "blockTime": event["blockTime"]}
    return _note(kind="refund", case=case, units=event["amount"], payer="[SIM] buyer (or the sponsor named in the "
                 "clause)", payee=SELLER, verdict=v, ref=expected, ref_source="CreditNoteDue event (on chain)")


def supersede(old: dict, new: dict, *, reason: str) -> tuple[dict, dict]:
    """Off-chain supersession: returns (old marked superseded, new linked to it). Inputs are not modified."""
    if old["ref"] == new["ref"]:
        raise ValueError("a note cannot supersede itself")
    if old["status"] != "due":
        raise ValueError(f"note {old['ref']} is already {old['status']}")
    if old["kind"] != new["kind"] or old["case"] != new["case"]:
        raise ValueError("a note can only be superseded by a note of the same kind and case")
    o, n = copy.deepcopy(old), copy.deepcopy(new)
    o.update(status="superseded", supersededBy=n["ref"],
             supersession={"reason": reason, "onChainEvent": None, "note": SUPERSESSION_NOTE})
    n.update(supersedes=o["ref"], supersession={"reason": reason, "onChainEvent": None, "note": SUPERSESSION_NOTE})
    o["noteHash"], n["noteHash"] = note_hash(o), note_hash(n)
    return o, n


def register(case: str, notes: list[dict]) -> dict:
    return {"schema": REGISTER_SCHEMA, "case": case, "notes": notes,
            "note": "append-only register; a superseded note stays with its link to the new ref"}


def register_hash(reg: dict) -> str:
    return "0x" + hashlib.sha256(canonical_bytes(reg)).hexdigest()


def _check(reg: dict) -> dict:
    if reg.get("schema") != REGISTER_SCHEMA:
        raise ValueError("not a credit-note register")
    for n in reg["notes"]:
        if n.get("schema") != SCHEMA or note_hash(n) != n["noteHash"]:
            raise ValueError(f"note {n.get('ref')} does not match its noteHash (edited after export)")
    return reg


def from_json(text: str) -> dict:
    return _check(json.loads(text))


def _row(n: dict) -> list[str]:
    """The visible columns, derived from the note (strings, as csv reads them back)."""
    v = n["verdict"]
    cells = [n["noteHash"], n["ref"], n["kind"], n["status"], n["amount"]["units"], n["amount"]["display"],
             n["payer"], n["payee"], v["chain"], v.get("covenantContract") or v.get("pool"),
             v.get("episodeId") if n["kind"] == "rebate" else f"{v['claimId']}/{v['installmentId']}",
             v["tx"], n["supersedes"] or "", n["supersededBy"] or ""]
    return [str(c) for c in cells] + [json.dumps(n, sort_keys=True)]


def to_csv(reg: dict) -> str:
    """One row per note: readable columns, then the full note JSON so the CSV round-trips exactly."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["case", reg["case"]])
    w.writerow(CSV_COLUMNS)
    for n in reg["notes"]:
        w.writerow(_row(n))
    return buf.getvalue()


def from_csv(text: str) -> dict:
    """Import refuses the file if any visible column differs from what its row's JSON says (an edit to the readable
    columns cannot slip through), or if a note no longer matches its noteHash."""
    rows = list(csv.reader(io.StringIO(text)))
    if len(rows) < 2 or len(rows[0]) != 2 or rows[0][0] != "case" or rows[1] != CSV_COLUMNS:
        raise ValueError("not a credit-note register CSV")
    case, notes = rows[0][1], []
    for i, r in enumerate(rows[2:], 3):
        if len(r) != len(CSV_COLUMNS):
            raise ValueError(f"line {i}: {len(r)} columns, expected {len(CSV_COLUMNS)}")
        n = json.loads(r[-1])
        expected = _row(n)
        bad = [CSV_COLUMNS[k] for k in range(len(CSV_COLUMNS) - 1) if r[k] != expected[k]]
        if bad:
            raise ValueError(f"line {i}: visible column(s) {bad} differ from the note's JSON")
        if n.get("case") != case:
            raise ValueError(f"line {i}: note case {n.get('case')!r} differs from the file's case cell {case!r}")
        notes.append(n)
    return _check(register(case, notes))


def write(reg: dict) -> None:
    _check(reg)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{reg['case']}.credit_note.json").write_text(json.dumps(reg, indent=1, ensure_ascii=False) + "\n")
    (OUT / f"{reg['case']}.credit_note.csv").write_text(to_csv(reg))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", default="TKM_S_276")
    ap.add_argument("--verdict", help="JSON file: a CONFIRMED core episode in a verdict-only covenant")
    ap.add_argument("--event", help="JSON file: a CreditNoteDue event")
    ap.add_argument("--chain", default="Anvil (local) [SIM]")
    ap.add_argument("--chain-id", type=int, default=31337)
    ap.add_argument("--pool")
    a = ap.parse_args()
    path = OUT / f"{a.case}.credit_note.json"
    reg = from_json(path.read_text()) if path.exists() else register(a.case, [])
    if a.verdict:
        note = rebate_note(json.loads(open(a.verdict).read()), case=a.case)
    elif a.event:
        note = refund_note(json.loads(open(a.event).read()), case=a.case, chain=a.chain, chain_id=a.chain_id,
                           pool=a.pool)
    else:
        raise SystemExit("--verdict or --event is required")
    if any(n["ref"] == note["ref"] for n in reg["notes"]):
        raise SystemExit(f"note {note['ref']} already in the register")
    reg["notes"].append(note)
    write(reg)
    print(f"credit note {note['kind']} {note['amount']['display']} ref {note['ref'][:18]}… -> {path.name}")


if __name__ == "__main__":
    main()
