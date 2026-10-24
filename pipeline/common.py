"""Shared helpers: paths, HTTP with a full User-Agent, hashing, manifest, geodesy."""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent


def _raw_dir() -> Path:
    """This checkout's data/raw/, or METHANECLAUSE_RAW=<dir>: a raw-response cache outside it (a worktree reading the
    main checkout's downloads). Optional: tests and `make check` never need raw data. A pointer to a directory that
    does not exist fails closed instead of silently falling back."""
    env = os.environ.get("METHANECLAUSE_RAW")
    if not env:
        return ROOT / "data" / "raw"
    p = Path(env).expanduser().resolve()
    if not p.is_dir():
        raise SystemExit(f"METHANECLAUSE_RAW={env} is not a directory")
    return p


RAW = _raw_dir()
DERIVED = ROOT / "data" / "derived"
MANIFEST = ROOT / "data" / "MANIFEST.json"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 MethaneClause-pipeline/0.1"
)
CM_API = "https://api.carbonmapper.org/api/v1"
CM_LICENCE = ("Carbon Mapper Terms of Use: non-commercial, attribution ('Data by Carbon Mapper®'), redistribution under "
              "the same terms")
MARS_LICENCE = "UNEP IMEO MARS: CC BY-NC-SA 4.0"
EARTH_R_KM = 6371.0088


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def http_get(url: str, params: dict | None = None, timeout: int = 60, tries: int = 4) -> bytes:
    last = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": UA})
            if r.status_code == 200:
                return r.content
            last = RuntimeError(f"HTTP {r.status_code} for {r.url}")
        except requests.RequestException as e:  # network hiccup: back off and retry
            last = e
        time.sleep(1.5 * (i + 1))
    raise last  # type: ignore[misc]


def load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"note": "SHA-256 of every raw response used by the pipeline. Raw files are not committed.",
            "files": {}}


def save_manifest(m: dict) -> None:
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=1, sort_keys=True) + "\n")


def write_json(p: Path, obj, indent: int | None = 1) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, indent=indent, sort_keys=True, ensure_ascii=False) + "\n")


def haversine_km(lat1, lon1, lat2, lon2):
    """Great-circle distance in km. Works on floats or numpy arrays."""
    import numpy as np

    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = p2 - p1
    dl = np.radians(lon2) - np.radians(lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * EARTH_R_KM * np.arcsin(np.sqrt(a))


def minus_months(d: str, months: int) -> str:
    """ISO date minus calendar months (day clamped to month end)."""
    y, m, day = int(d[:4]), int(d[5:7]), int(d[8:10])
    m -= months
    while m <= 0:
        m += 12
        y -= 1
    import calendar

    day = min(day, calendar.monthrange(y, m)[1])
    return f"{y:04d}-{m:02d}-{day:02d}"


assert minus_months("2026-09-11", 6) == "2026-03-11"
assert math.isclose(float(haversine_km(0, 0, 0, 1)), 111.195, rel_tol=1e-3)


# ---- Frozen join criterion v1 constants (see pipeline/join/CRITERION.md) ----
JOIN_VERSION = "v1"
RADIUS_KM = 1.5
WINDOW_MONTHS = 6
CASE_CM_SOURCES = {  # spec §4 demo cases: MARS source -> Carbon Mapper sources named in the spec
    "TKM_S_276": ["CH4_1B2_250m_53.71782_39.48437"],
    "DZA_S_012": ["CH4_1B2_1000m_5.99636_31.77708", "CH4_1B2_250m_5.99640_31.77781"],
    "USA_S_077": ["CH4_1B2_1000m_-101.52270_32.51483"],
}


def mars_target(mars_sources):
    """MARS sources the join looks at: oil & gas, notified, labelled 'absent'."""
    s = mars_sources
    return s[(s["sector"] == "Oil and Gas") & (s["notified"] == "Yes") & (s["persistency_category"] == "absent")]


def strip_signed_urls(o):
    """Drop expiring signed URLs (Carbon Mapper image links change on every request)."""
    if isinstance(o, dict):
        return {k: strip_signed_urls(v) for k, v in o.items()
                if not (isinstance(v, str) and v.startswith("http") and "Signature=" in v)}
    if isinstance(o, list):
        return [strip_signed_urls(v) for v in o]
    return o


def content_sha256(raw: bytes) -> str:
    """Stable hash of a Carbon Mapper response: JCS of the JSON without signed URLs. Same data -> same hash."""
    import rfc8785

    return hashlib.sha256(rfc8785.dumps(strip_signed_urls(json.loads(raw)))).hexdigest()
