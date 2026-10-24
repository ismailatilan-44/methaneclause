"""Refuse `forge script --resume` unless the cached broadcast run is complete.

    python3 -I script/check_resume.py broadcast/DeployPool.s.sol/11155111/run-latest.json

`make verify-pool-sepolia` / `verify-sandbox-pool-sepolia` retry only the Etherscan verification of a finished run.
`--resume` would also send any transaction the cached run still lists as pending, so this check runs first and exits 1
unless the run has no pending transaction, one receipt per transaction, and every receipt succeeded (status 1).
"""
from __future__ import annotations

import json
import sys


def problems(run: dict) -> list[str]:
    txs, receipts = run.get("transactions") or [], run.get("receipts") or []
    out = []
    if run.get("pending"):
        out.append(f"{len(run['pending'])} pending transaction(s)")
    if not txs:
        out.append("no transactions")
    if len(receipts) != len(txs):
        out.append(f"{len(receipts)} receipts for {len(txs)} transactions")
    failed = [r.get("transactionHash") for r in receipts if int(str(r.get("status", "0x0")), 16) != 1]
    if failed:
        out.append(f"failed receipts: {', '.join(map(str, failed))}")
    return out


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    try:
        run = json.load(open(path))
    except (OSError, ValueError) as e:
        sys.exit(f"check_resume: cannot read {path}: {e}; nothing was sent")
    bad = problems(run)
    if bad:
        sys.exit(f"check_resume: {path} is not a finished run ({'; '.join(bad)}); refusing --resume, nothing was sent")
    print(f"check_resume: {path}: {len(run['transactions'])} transactions, all mined with status 1; verification only")


if __name__ == "__main__":
    main()
