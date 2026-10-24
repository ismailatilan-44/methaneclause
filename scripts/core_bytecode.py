#!/usr/bin/env python3
"""The v1 core contracts must stay byte-identical (spec-v2 §1). Hash their deployedBytecode from the forge artefacts.

    python3 scripts/core_bytecode.py write   # maintainers only: record the current hashes
    python3 scripts/core_bytecode.py check   # make check: fail if either hash differs
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF = ROOT / "contracts" / "deployments" / "core_bytecode.sha256"
CORE = ("MethaneCovenant", "KeyRegistry")


def current() -> dict[str, str]:
    out = {}
    for name in CORE:
        art = json.loads((ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json").read_text())
        out[name] = hashlib.sha256(art["deployedBytecode"]["object"].encode()).hexdigest()
    return out


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    now = current()
    if cmd == "write":
        REF.write_text("".join(f"{h}  {n}\n" for n, h in now.items()))
        print(f"core bytecode hashes written -> {REF.relative_to(ROOT)}")
        return
    ref = dict(reversed(line.split()) for line in REF.read_text().splitlines() if line.strip())
    bad = [n for n in CORE if ref.get(n) != now[n]]
    if bad:
        sys.exit(f"core-bytecode: CHANGED {bad} — the v1 core must not change (spec-v2 §1)")
    print("core-bytecode: unchanged")


if __name__ == "__main__":
    main()
