"""METHANECLAUSE_RAW: an optional pointer to a raw-response cache outside this checkout (a worktree reading the main
checkout's data/raw/). Unset, the cache is this checkout's data/raw/; a pointer to a missing directory fails closed."""
import os
import subprocess
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent
ROOT = PIPELINE.parent
PROBE = "import common, scenes; print(common.RAW); print(scenes.CACHE)"


def _run(env_raw):
    env = {k: v for k, v in os.environ.items() if k != "METHANECLAUSE_RAW"}
    if env_raw is not None:
        env["METHANECLAUSE_RAW"] = env_raw
    return subprocess.run([sys.executable, "-c", PROBE], cwd=PIPELINE, env=env, capture_output=True, text=True)


def test_default_is_this_checkouts_data_raw():
    r = _run(None)
    assert r.returncode == 0, r.stderr
    raw, cache = r.stdout.split()
    assert Path(raw) == ROOT / "data" / "raw" and Path(cache) == ROOT / "data" / "raw" / "cm" / "cases"


def test_env_points_the_raw_cache_elsewhere(tmp_path):
    r = _run(str(tmp_path))
    assert r.returncode == 0, r.stderr
    raw, cache = r.stdout.split()
    assert Path(raw) == tmp_path.resolve() and Path(cache) == tmp_path.resolve() / "cm" / "cases"


def test_env_to_a_missing_directory_fails_closed(tmp_path):
    r = _run(str(tmp_path / "nope"))
    assert r.returncode != 0 and "METHANECLAUSE_RAW" in r.stderr
