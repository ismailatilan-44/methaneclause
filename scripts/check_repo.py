#!/usr/bin/env python3
"""Repository hygiene gate, run by `make check` (also `make words`, `make links`, `make tree`). Exit 1 on any defect.

  words  forbidden claims in product files ("tamper-proof", "trustless", "quantum-safe", "guarantee") — a line is
         allowed only when it is visibly a prohibition or a negation (see ALLOW)
  links  every relative markdown link resolves (anchors and :line suffixes stripped)
  tree   nothing from data/raw/, no .env and no model.pkl is tracked; LICENSE, data licence, AGENTS.md exist
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRODUCT_GLOBS = ["README.md", "AGENTS.md", "LICENSE", "Makefile", ".env.example", "contracts/**/*.sol",
                 "contracts/**/*.md", "pipeline/**/*.py", "pipeline/**/*.md", "scripts/**/*", "app/**/*",
                 "docs/*.md", "docs/pdf/*.md", "docs/clause/*.md", "docs/templates/*.md", "docs/tasks/*.md", "data/derived/*.md"]
EXCLUDE_PARTS = {"lib", ".venv", "node_modules", "out", "cache", "_archive", "broadcast"}
# v2 wording (spec-v2 §1): a clean pass is "observed, not abated"; IEA's own term "abatement-cost" is allowed.
FORBIDDEN = re.compile(r"tamper[- ]?proof|trustless|quantum[- ]safe|\bguarantee[sd]?\b|proof of reduction|clean[- ]site"
                       r"|carbon credits?|verified fix|can be paid in stablecoin|\babatement\b(?!-cost)|\bcompliant\b"  # FORBIDDEN
                       r"|emission reductions?", re.I)  # FORBIDDEN
ALLOW = re.compile(r"never|\bnot\b|\bno\b|do not|without|YOK|forbid|ban|prohibit|~~|we never|instead of|rather than"
                   r"|guaranteed future positive|FORBIDDEN|ALLOW", re.I)

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
LINK_SCOPE_EXCLUDE = {"_archive", "context", "node_modules", ".venv", "lib"}
# Private working-repo extras (not exported): extra globs and link-scope exclusions, one JSON object if present.
_LOCAL = ROOT / "scripts" / "check_repo.local.json"
if _LOCAL.exists():
    import json as _json
    _cfg = _json.loads(_LOCAL.read_text())
    PRODUCT_GLOBS += _cfg.get("product_globs", [])
    LINK_SCOPE_EXCLUDE |= set(_cfg.get("link_scope_exclude", []))


def product_files() -> list[Path]:
    out: list[Path] = []
    for g in PRODUCT_GLOBS:
        for p in ROOT.glob(g):
            if p.is_file() and not (set(p.parts) & EXCLUDE_PARTS):
                out.append(p)
    return sorted(set(out))


def check_words() -> list[str]:
    bad = []
    for p in product_files():
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for i, line in enumerate(lines, 1):
            if FORBIDDEN.search(line) and not ALLOW.search(line):
                bad.append(f"{p.relative_to(ROOT)}:{i}: {line.strip()[:120]}")
    return bad


def check_links() -> list[str]:
    bad = []
    for p in ROOT.rglob("*.md"):
        if set(p.parts) & (EXCLUDE_PARTS | LINK_SCOPE_EXCLUDE):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        for m in LINK_RE.finditer(text):
            target = m.group(1)
            if re.match(r"^(https?:|mailto:|#)", target):
                continue
            target = re.sub(r"[#:].*$", "", target)
            if not target:
                continue
            if not (p.parent / target).exists() and not (ROOT / target).exists():
                bad.append(f"{p.relative_to(ROOT)}: broken link -> {m.group(1)}")
    return bad


def check_tree() -> list[str]:
    bad = []
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    for f in tracked:
        if f.startswith("data/raw/") or f == ".env" or f.startswith(".env.") and f != ".env.example" \
                or f.endswith("model.pkl") or f.startswith("contracts/broadcast/") and "/31337/" in f:
            bad.append(f"tracked but must not be: {f}")
    for must in ("LICENSE", "data/derived/DATA_LICENSE.md", ".env.example", "AGENTS.md"):
        if not (ROOT / must).exists():
            bad.append(f"missing: {must}")
    return bad


def main() -> None:
    what = sys.argv[1:] or ["words", "links", "tree"]
    failed = False
    for w in what:
        problems = {"words": check_words, "links": check_links, "tree": check_tree}[w]()
        print(f"{w}: {'ok' if not problems else str(len(problems)) + ' problem(s)'}")
        for line in problems:
            print("  " + line)
        failed |= bool(problems)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
