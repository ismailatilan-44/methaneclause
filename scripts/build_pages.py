#!/usr/bin/env python3
"""Static export of the one-page UI for any static host (make pages -> out/pages/). Nothing is published here.

    python3 -I scripts/build_pages.py [OUT_DIR]        # default out/pages

Copies app/ (index.html, app.js, style.css and app/data/: app.json, the committed export files the page offers for
download, the JCS test vectors) and nothing else; there is no server code. The page reads live Ethereum Sepolia through
the public RPC named in app/data/app.json and loads its pinned CDN modules, exactly as app/ does when served locally.

Fails closed (exit 1, the previous export left untouched, no staging directory left behind) when
  - a file the page loads (src/href in index.html, a fetch in app.js, a vector in expected.json) is missing;
  - a path is root-relative ("/x"), which breaks under a sub-path host such as GitHub Pages;
  - a CDN module is not pinned to an exact version;
  - the release gate's content scan (scripts/check_public.py, when present in this tree) finds a forbidden word, a
    private reference, a secret or an internal build label in the exported files.
Writes BUILD.json (source commit, whether app/ had uncommitted changes, SHA-256 of every exported file) and .nojekyll.
Deterministic: two builds of the same tree write the same bytes.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP = {"scene_ids.json",  # build input for scripts/build_app_data.py; the page never loads it
        "BUILD.json"}  # app/BUILD.json is the dev stub (no 404 when app/ is served); the export writes the real record
ATTR = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""")
JS_FETCH = re.compile(r"""fetch\(\s*["'`]([^"'`$]+)["'`]""")
ROOT_REL = [re.compile(r"""(?:src|href)\s*=\s*["']/(?!/)"""), re.compile(r"""fetch\(\s*["'`]/(?!/)""")]
CDN = re.compile(r"""https://cdn\.jsdelivr\.net/npm/[^"'`\s)]+""")
PINNED = re.compile(r"@\d+\.\d+\.\d+/")


def fail(msg: str):
    sys.exit(f"build_pages: {msg}")


def _external(ref: str) -> bool:
    return ref.startswith(("http://", "https://", "//", "#", "mailto:", "data:", "?", "javascript:"))


def local_refs(base: Path) -> list[str]:
    """Every same-origin file the page loads or links, read from the files under base."""
    refs: set[str] = set()
    for f in base.glob("*.html"):
        refs |= {r.split("#", 1)[0].split("?", 1)[0] for r in ATTR.findall(f.read_text()) if not _external(r)}
    for f in base.glob("*.js"):
        text = f.read_text()
        refs |= {r for r in JS_FETCH.findall(text) if not _external(r)}
    exp = base / "data" / "vectors" / "expected.json"
    if exp.is_file():  # app.js fetches each vector named in expected.json
        refs |= {f"data/vectors/{name}" for name in json.loads(exp.read_text())}
    return sorted(r for r in refs if r)


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _leak_scan(base: Path, files: list[Path]) -> list[str]:
    """The release gate's own content rules (scripts/check_public.py), applied to the exported files. Internal build
    labels, a warning in exported code there, fail here: the hosted page is read by people outside the build."""
    gate = ROOT / "scripts" / "check_public.py"
    if not gate.exists():
        print("build_pages: release gate not in this tree; content scan skipped")
        return []
    spec = importlib.util.spec_from_file_location("check_public", gate)
    cp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cp)
    rep = cp.Report()
    cp.check_content(base, files, rep, tree_only=False, public_set=None)
    return rep.fails + [w for w in rep.warns if w.startswith("WARN label")]


def _check(stage: Path) -> None:
    for f in [*stage.glob("*.html"), *stage.glob("*.js"), *stage.glob("*.css")]:
        text = f.read_text()
        for pat in ROOT_REL:
            m = pat.search(text)
            if m:
                fail(f"{f.name}: root-relative path ({m.group(0)}…) breaks on a sub-path host; use a relative path")
        for url in CDN.findall(text):
            if not PINNED.search(url):
                fail(f"{f.name}: CDN module not pinned to an exact version: {url}")
    missing = [r for r in local_refs(stage) if not (stage / r).is_file()]
    if missing:
        fail(f"the page loads files the export lacks: {missing}")


def build(src: Path, out: Path) -> dict:
    """Copy src (app/) into out atomically; returns the BUILD.json record."""
    src, out = Path(src), Path(out)
    if not (src / "index.html").is_file():
        fail(f"{src} has no index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".pages-", dir=out.parent))
    try:
        files: list[Path] = []
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src)
            if not p.is_file() or rel.name in SKIP or any(part.startswith(".") for part in rel.parts):
                continue
            (stage / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(p, stage / rel)
            files.append(rel)
        (stage / ".nojekyll").write_text("")  # GitHub Pages: serve files as they are
        commit = (_git("rev-parse", "HEAD") or "unknown").strip()
        dirty = _git("status", "--porcelain", "--", "app")
        record = {
            "builtBy": "make pages (scripts/build_pages.py)",
            "sourceCommit": commit,
            "sourceDirty": None if dirty is None else bool(dirty.strip()),
            "files": {p.relative_to(stage).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in sorted(stage.rglob("*")) if p.is_file()},
        }
        (stage / "BUILD.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        _check(stage)  # after BUILD.json: the page fetches it
        problems = _leak_scan(stage, files)
        if problems:
            fail("content scan:\n  " + "\n  ".join(problems))
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    if out.exists():
        shutil.rmtree(out)
    os.replace(stage, out)
    return record


def main(argv: list[str]) -> None:
    out = Path(argv[0]) if argv else ROOT / "out" / "pages"
    rec = build(ROOT / "app", out)
    size = sum((out / f).stat().st_size for f in rec["files"])
    try:
        shown = out.resolve().relative_to(ROOT)
    except ValueError:
        shown = out
    print(f"pages -> {shown} ({len(rec['files'])} files, {size:,} bytes; commit {rec['sourceCommit'][:12]}"
          f"{', app/ has uncommitted changes' if rec['sourceDirty'] else ''})")
    print(f"preview: python3 -m http.server 4231 --directory {shown}   (any static host works; nothing is published)")


if __name__ == "__main__":
    main(sys.argv[1:])
