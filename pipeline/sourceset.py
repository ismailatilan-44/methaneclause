"""Covenant source sets: v2 (the three demo facilities) -> sourceset_v2.json; v3 (the portfolio: every join v1
candidate plus the demo cases, spec-v2 §7) -> sourceset_v3.json (`python sourceset.py v3`).

v2 -> data/derived/sourceset_v2.json (root + per-facility sourceId, leaf, proof).

Leaves = covered FACILITIES only (the demo case MARS sources). The Carbon Mapper sources linked to each facility are
listed as metadata (`linked_cm_sources`), not as leaves: the observer checks a plume's CM source against that list,
and the contract can only open an episode keyed by the facility, so CM twin sources around one facility share one
linkWindow anchor and cannot pay twice (v1 had CM sources as leaves too; review 2026-10-14). Order: sorted by
source_name. Format matches contracts/README.md "Interface notes".
"""
from __future__ import annotations

import hashlib
import json

from common import CASE_CM_SOURCES, DERIVED, write_json
from merkle import hx, leaf, proof, root, source_id, verify

CASES = DERIVED / "cases"


def main() -> None:
    members: dict[str, dict] = {}
    claimed: set[str] = set()
    for mars_name in CASE_CM_SOURCES:
        case = json.loads((CASES / f"{mars_name}.json").read_text())
        assert case["sourceId"] == hx(source_id(mars_name))
        linked = sorted(set(case["cm_sources"]) - claimed)  # one CM source never counts for two facilities
        claimed |= set(linked)
        members[mars_name] = {"kind": "facility", "case": mars_name, "linked_cm_sources": linked}

    names = sorted(members)
    sids = [source_id(n) for n in names]
    rt = root(sids)
    out = {"version": "v2",
           "encoding": {"sourceId": "keccak256(utf8(source_name))",
                        "leaf": "keccak256(bytes.concat(keccak256(abi.encode(sourceId))))",
                        "node": "keccak256(abi.encode(min(a,b), max(a,b)))",
                        "layout": "leaves sorted by source_name; pairs (2i,2i+1); odd node promoted"},
           "root": hx(rt), "n": len(names), "sources": []}
    for n, s in zip(names, sids):
        prf = proof(sids, s)
        assert verify(prf, rt, leaf(s)), n
        out["sources"].append({"source_name": n, **members[n], "sourceId": hx(s), "leaf": hx(leaf(s)),
                               "proof": [hx(p) for p in prf]})
    out["sha256"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    write_json(DERIVED / "sourceset_v2.json", out)
    print(f"sourceset_v2: {len(names)} facilities, root {hx(rt)}")


def _write(version: str, members: dict[str, dict], note: str) -> dict:
    names = sorted(members)
    sids = [source_id(n) for n in names]
    rt = root(sids)
    out = {"version": version, "note": note,
           "encoding": {"sourceId": "keccak256(utf8(source_name))",
                        "leaf": "keccak256(bytes.concat(keccak256(abi.encode(sourceId))))",
                        "node": "keccak256(abi.encode(min(a,b), max(a,b)))",
                        "layout": "leaves sorted by source_name; pairs (2i,2i+1); odd node promoted"},
           "root": hx(rt), "n": len(names), "sources": []}
    for n, s in zip(names, sids):
        prf = proof(sids, s)
        assert verify(prf, rt, leaf(s)), n
        out["sources"].append({"source_name": n, **members[n], "sourceId": hx(s), "leaf": hx(leaf(s)),
                               "proof": [hx(p) for p in prf]})
    out["sha256"] = hashlib.sha256(json.dumps(out, sort_keys=True).encode()).hexdigest()
    write_json(DERIVED / f"sourceset_{version}.json", out)
    return out


def main_v3() -> None:
    """Portfolio: join v1 candidates + demo cases. Linked CM sources = those each facility owns under the join's
    rule 3 (nearest target within 1.5 km), so one CM source never counts for two facilities."""
    from backtest import facilities

    join = json.loads((DERIVED / "join_v1.json").read_text())
    cands = {c["mars_source"] for c in join["candidates"]}
    owned = {f["name"]: f["cm_sources"] for f in facilities()[0]}
    members = {}
    for n in sorted(cands | set(CASE_CM_SOURCES)):
        why = ("join v1 candidate and demo case" if n in cands and n in CASE_CM_SOURCES else
               "join v1 candidate" if n in cands else "demo case")
        members[n] = {"kind": "facility", "reason": why, "linked_cm_sources": owned.get(n, [])}
    out = _write("v3", members, "portfolio covenant set (spec-v2 §7): the importer's sources on one screen")
    print(f"sourceset_v3: {out['n']} facilities, root {out['root']}")


if __name__ == "__main__":
    import sys

    main_v3() if sys.argv[1:] == ["v3"] else main()
