"""Tracked per-transaction log of everything our scripts send on Ethereum Sepolia: data/derived/sepolia_txlog.jsonl.

The scripts also write to out/ (untracked). The 15 Oct judge-sandbox logs lived only in out/ of a worktree that was later
deleted, and with them the raw submit->receipt latency samples; this file is committed, so that cannot happen again.
Only chain 11155111 is logged here (Anvil runs stay in out/). One JSON object per line, one line per transaction hash.

    python txlog.py broadcast contracts/broadcast/<Script>.s.sol/11155111/run-latest.json [...]
        append a Foundry broadcast's mined transactions (no latency: Foundry does not record submit times)
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from common import DERIVED

SEPOLIA = 11155111
TRACKED = DERIVED / "sepolia_txlog.jsonl"
FIELDS = ("command", "label", "tx", "status", "gasUsed", "effectiveGasPrice", "seconds", "revert", "block")


def read(path: Path = TRACKED) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def append(rows: list[dict], chain_id: int, command: str, path: Path = TRACKED) -> int:
    """Append rows for Sepolia only, skipping hashes already logged; returns how many lines were written."""
    if chain_id != SEPOLIA or not rows:
        return 0
    seen = {r["tx"] for r in read(path)}
    new = []
    for r in rows:
        if r["tx"] in seen:
            continue
        seen.add(r["tx"])
        new.append({k: r.get(k) for k in FIELDS} | {"command": r.get("command") or command})
    if new:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for r in new:
                f.write(json.dumps(r, sort_keys=True) + "\n")
    return len(new)


def broadcast_rows(path: Path) -> tuple[int, list[dict]]:
    """Rows from a Foundry broadcast file: each transaction that has a receipt; label as pipeline/sepolia_txs.py."""
    d = json.loads(path.read_text())
    receipts = {r["transactionHash"]: r for r in d.get("receipts", [])}
    rows = []
    for t in d["transactions"]:
        r = receipts.get(t.get("hash"))
        if r is None:  # never mined (an aborted run): nothing on chain to log
            continue
        if t["transactionType"].startswith("CREATE"):
            label = f"deploy_{t['contractName']}"
        elif t.get("contractName") == "IncentivePool":
            label = f"pool_{t['function'].split('(')[0]}"
        else:
            label = f"deploy_{t['function'].split('(')[0]}"
        rows.append({"label": label, "tx": t["hash"], "status": int(r["status"], 16), "gasUsed": int(r["gasUsed"], 16),
                     "effectiveGasPrice": int(r["effectiveGasPrice"], 16), "seconds": None, "revert": None,
                     "block": int(r["blockNumber"], 16)})
    return int(d.get("chain", SEPOLIA)), rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["broadcast"])
    ap.add_argument("files", nargs="+", type=Path)
    a = ap.parse_args()
    for f in a.files:
        chain, rows = broadcast_rows(f)
        n = append(rows, chain, f"deploy:{f.parent.parent.name}")
        print(f"{f}: {n} new line(s) in {TRACKED.relative_to(DERIVED.parent.parent)}")


if __name__ == "__main__":
    main()
