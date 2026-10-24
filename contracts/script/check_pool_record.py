#!/usr/bin/env python3
"""Check a deployment record's IncentivePool entries against the chain (run after DeployPool.s.sol).

`forge script` writes the record while the script runs, before its transactions are confirmed; a failed or
simulated-only broadcast would leave addresses with no code in the record. This check reads the chain and fails if
the record and the chain disagree:
  - every recorded pool, library and sandbox contract has code;
  - the 20 bytes at each library link offset of the pool's deployed code (offsets from the compiled artifact) are the
    address recorded for that library, for the main pool and the sandbox pool;
  - each library's deployed code equals its compiled artifact (out/<Name>.sol/<Name>.json) with the library's own
    address masked, so a contract that is not the compiled library is refused;
  - each recorded size equals the deployed code size, and the pool stays within EIP-170 (24,576 bytes).

usage: check_pool_record.py RECORD RPC_URL [ARTIFACT]   (needs `cast`; exit 1 on any mismatch)
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

EIP170 = 24_576
NAMES = ["PoolVerify", "PoolSetup", "PoolEpisodes", "PoolCorroboration", "PoolInstallments"]


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    record = json.loads(Path(argv[1]).read_text())
    rpc = argv[2]
    artifact = Path(argv[3] if len(argv) == 4 else Path(__file__).resolve().parent.parent
                    / "out/IncentivePool.sol/IncentivePool.json")
    links = json.loads(artifact.read_text())["deployedBytecode"]["linkReferences"]
    offsets = {n: links[f"src/lib/{n}.sol"][n] for n in NAMES}
    lib_code = {
        n: bytes.fromhex(
            json.loads((artifact.parent.parent / f"{n}.sol" / f"{n}.json").read_text())["deployedBytecode"]["object"][2:]
        )
        for n in NAMES
    }

    failures: list[str] = []

    def code(addr: str) -> bytes:
        out = subprocess.run(["cast", "code", addr, "--rpc-url", rpc], capture_output=True, text=True, check=True)
        return bytes.fromhex(out.stdout.strip().removeprefix("0x"))

    def check(cond: bool, msg: str) -> None:
        print(("   ok    " if cond else "   FAIL  ") + msg)
        if not cond:
            failures.append(msg)

    def pool_entry(scope: str, r: dict) -> None:
        pool = r["incentivePool"]
        pc = code(pool)
        check(len(pc) > 0, f"{scope} pool {pool} has code")
        check(len(pc) == r["incentivePoolSizes"]["IncentivePool"] and len(pc) <= EIP170,
              f"{scope} pool: {len(pc)} B deployed (EIP-170 limit {EIP170}), recorded "
              f"{r['incentivePoolSizes']['IncentivePool']}")
        for n in NAMES:
            rec = r["incentivePoolLibraries"][n].lower()
            linked = ["0x" + pc[o["start"]:o["start"] + o["length"]].hex() for o in offsets[n]]
            check(bool(linked) and all(a == rec for a in linked),
                  f"{scope} {n}: pool links {sorted(set(linked))} at its {len(linked)} offset(s), recorded {rec}")
            lc = code(rec)
            check(len(lc) > 0 and len(lc) == r["incentivePoolSizes"][n], f"{scope} {n}: {len(lc)} B deployed, as recorded")
            # the deployed library is the compiled one: equal to its artifact's deployedBytecode once the library's
            # own address (the call guard PUSH20 <address> at bytes 1..20, zero in the artifact) is masked
            masked = lc[:1] + bytes(20) + lc[21:] if len(lc) > 21 and lc[0] == 0x73 else lc
            check(masked == lib_code[n], f"{scope} {n}: code equals out/{n}.sol/{n}.json (own address masked)")

    for key in ("keyRegistry", "methaneCovenant"):
        if key in record:
            check(len(code(record[key])) > 0, f"main {key} {record[key]} has code")
    if "incentivePool" in record:
        pool_entry("main", record)
    sb = record.get("sandbox", {})
    for key in ("keyRegistry", "methaneCovenant"):
        if key in sb:
            check(len(code(sb[key])) > 0, f"sandbox {key} {sb[key]} has code")
    if "incentivePool" in sb:
        pool_entry("sandbox", sb)
    for key, addr in record.get("sandboxPrevious", {}).items():
        check(len(code(addr)) > 0, f"sandboxPrevious {key} {addr} has code")

    if failures:
        print(f"RECORD CHECK FAILED: {len(failures)} mismatch(es) between {argv[1]} and the chain")
        return 1
    print("record check ok: every recorded pool entry matches the chain")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
