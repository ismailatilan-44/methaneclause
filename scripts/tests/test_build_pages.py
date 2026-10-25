"""Offline tests for scripts/build_pages.py (make pages; part of make test-scripts).

The static export must work from any static host: only relative paths, every file the page loads present, CDN modules
pinned to exact versions, a BUILD.json whose hashes match the bytes, and nothing internal in it. A bad source must stop
the build with nothing written (fail closed): the output directory is either the complete new export or untouched.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_pages", ROOT / "scripts" / "build_pages.py")
bp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bp)

APP = ROOT / "app"


@pytest.fixture()
def built(tmp_path):
    out = tmp_path / "pages"
    info = bp.build(APP, out)
    return out, info


def test_export_has_the_page_and_every_file_it_loads(built):
    out, info = built
    for f in ("index.html", "app.js", "style.css", "data/app.json", "data/vectors/expected.json", ".nojekyll"):
        assert (out / f).is_file(), f
    for f in bp.local_refs(out):  # recomputed from the exported bytes, not from the builder's own list
        assert (out / f).is_file(), f"the page loads {f} but the export lacks it"
    expected = json.loads((out / "data" / "vectors" / "expected.json").read_text())
    for name in expected:
        assert (out / "data" / "vectors" / name).is_file()
    assert not (out / "scene_ids.json").exists(), "a build input, never loaded by the page"


def test_build_json_hashes_match_the_bytes(built):
    out, _ = built
    rec = json.loads((out / "BUILD.json").read_text())
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()} - {"BUILD.json"}
    assert set(rec["files"]) == files
    for name, h in rec["files"].items():
        assert hashlib.sha256((out / name).read_bytes()).hexdigest() == h
    assert re.fullmatch(r"[0-9a-f]{40}|unknown", rec["sourceCommit"])


def test_only_relative_paths_and_pinned_cdn(built):
    out, _ = built
    html = (out / "index.html").read_text()
    js = (out / "app.js").read_text()
    assert not re.search(r"""(?:src|href)=["']/(?!/)""", html), "root-relative path breaks under a sub-path host"
    assert not re.search(r"""fetch\(\s*[`"']/(?!/)""", js)
    cdn = re.findall(r"https://cdn\.jsdelivr\.net/npm/[^\"'\s)]+", html + js)
    assert cdn and all(re.search(r"@\d+\.\d+\.\d+/", u) for u in cdn), cdn


def test_build_is_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    bp.build(APP, a)
    bp.build(APP, b)
    assert (a / "BUILD.json").read_bytes() == (b / "BUILD.json").read_bytes()


def _src_copy(tmp_path) -> Path:
    src = tmp_path / "app"
    shutil.copytree(APP, src)
    return src


def test_missing_data_file_fails_closed_and_keeps_the_old_export(tmp_path, built):
    out, _ = built
    before = (out / "BUILD.json").read_bytes()
    src = _src_copy(tmp_path)
    (src / "data" / "TKM_S_276.annex_c.example.csv").unlink()
    with pytest.raises(SystemExit, match="export lacks"):
        bp.build(src, out)
    assert (out / "BUILD.json").read_bytes() == before, "a failed build must not touch the previous export"
    assert not any(p.name.startswith(".pages-") for p in out.parent.iterdir()), "no staging directory left behind"


def test_root_relative_path_is_refused(tmp_path):
    src = _src_copy(tmp_path)
    html = (src / "index.html").read_text().replace('href="style.css"', 'href="/style.css"')
    (src / "index.html").write_text(html)
    out = tmp_path / "pages"
    with pytest.raises(SystemExit, match="root-relative"):
        bp.build(src, out)
    assert not out.exists()


def test_unpinned_cdn_is_refused(tmp_path):
    src = _src_copy(tmp_path)
    js = (src / "app.js").read_text().replace("viem@2.57.1", "viem")
    (src / "app.js").write_text(js)
    with pytest.raises(SystemExit, match="not pinned"):
        bp.build(src, tmp_path / "pages")


@pytest.mark.skipif(not (ROOT / "scripts" / "check_public.py").exists(), reason="release gate not in this tree")
def test_internal_label_is_refused(tmp_path):
    src = _src_copy(tmp_path)
    label = " ".join(("lane", "C"))  # built at run time so this fixture is not itself flagged by the release scan
    html = (src / "index.html").read_text().replace("<h2 id=\"limits-h\">", f"<h2 id=\"limits-h\">{label} · ")
    (src / "index.html").write_text(html)
    with pytest.raises(SystemExit, match="WARN label"):
        bp.build(src, tmp_path / "pages")
