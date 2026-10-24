"""Every `generatedFrom` record in data/derived (any depth of any JSON file) names a file whose SHA-256 on disk is the one
recorded, so a number cannot silently outlive the input it was computed from. Raw inputs under data/raw/ are absent in
a clean checkout (gitignored, never exported): those entries are skipped and counted, not checked."""
from __future__ import annotations

import json

from common import DERIVED, ROOT, sha256_file


def _records(node, where=""):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "generatedFrom" and isinstance(v, list):
                for g in v:
                    if isinstance(g, dict) and "path" in g and "sha256" in g:
                        yield where, g["path"], g["sha256"]
            else:
                yield from _records(v, f"{where}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _records(v, f"{where}[{i}]")


def test_every_generated_from_hash_matches_the_file_on_disk():
    checked, skipped_raw, bad = 0, [], []
    for f in sorted(DERIVED.rglob("*.json")):
        for where, path, h in _records(json.loads(f.read_text()), ""):
            p = ROOT / path
            if not p.exists() and path.startswith("data/raw/"):
                skipped_raw.append(path)  # gitignored raw input: absent in a clean checkout, not checkable here
                continue
            if not p.exists():
                bad.append(f"{f.relative_to(ROOT)}{where}: {path} is missing")
            elif sha256_file(p) != h:
                bad.append(f"{f.relative_to(ROOT)}{where}: {path} changed since it was recorded")
            else:
                checked += 1
    assert not bad, "\n".join(bad)
    assert checked > 0
    print(f"generatedFrom: {checked} hashes match; {len(skipped_raw)} raw inputs skipped (absent by design)")
