"""Keyless Carbon Mapper scene / plume / source queries for the case facilities, cached (spec §12).

Network policy: only the three case facilities, tens of requests, cached under
data/raw/cm/cases/ (gitignored). A cached response is never re-fetched; delete the cache file to refresh it. Each cache
entry keeps the exact URL + params, the fetch time, the SHA-256 of the raw bytes and the content SHA-256 (JCS of the
JSON without expiring signed URLs, `common.content_sha256`), so every derived field can be traced to one response.

Endpoints (OpenAPI https://api.carbonmapper.org/api/v1/openapi.json, read 2026-10-15):
  /catalog/scenes/annotated?intersects=<GeoJSON>&datetime=<from>/<to>   scenes whose footprint contains the geometry
  /catalog/search/source/{name}                                          one source: plumes (with scene_id), scenes,
                                                                         observation / detection dates
  /catalog/plumes/annotated?scene_id=<uuid>                              every plume on one scene
Finding (2026-10-15): `/catalog/plumes/annotated` ignores a `source_name` parameter (it returned the whole catalogue,
total_count 45,227), so the per-source plume check uses `/catalog/search/source/{name}`.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from common import CM_API, RAW, content_sha256, http_get, now_utc, sha256_bytes

CACHE = RAW / "cm" / "cases"


def cache_dir() -> Path:
    """CM_CASES_CACHE overrides the cache (the observer's LIVE=1 re-check fetches into a fresh temporary directory)."""
    return Path(os.environ["CM_CASES_CACHE"]) if os.environ.get("CM_CASES_CACHE") else CACHE
SCENES_URL = f"{CM_API}/catalog/scenes/annotated"
PLUMES_URL = f"{CM_API}/catalog/plumes/annotated"
SOURCE_URL = f"{CM_API}/catalog/search/source/"


class OfflineMiss(RuntimeError):
    pass


def cached_get(kind: str, key: str, url: str, params: dict | None = None) -> tuple[object, dict]:
    """Return (parsed JSON, meta). Fetches once; afterwards always serves the cached bytes."""
    body, meta_p = cache_dir() / kind / f"{key}.json", cache_dir() / kind / f"{key}.meta.json"
    if body.exists() and meta_p.exists():
        raw, meta = body.read_bytes(), json.loads(meta_p.read_text())
    else:
        if os.environ.get("OFFLINE") == "1":
            raise OfflineMiss(f"not cached and OFFLINE=1: {kind}/{key}")
        raw = http_get(url, params=params)
        json.loads(raw)
        meta = {"url": url, "params": params or {}, "fetchedAt": now_utc(), "sha256": sha256_bytes(raw),
                "contentSha256": content_sha256(raw)}
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(raw)
        meta_p.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    if sha256_bytes(raw) != meta["sha256"] or content_sha256(raw) != meta["contentSha256"]:
        raise ValueError(f"cache corrupted: {body}")
    return json.loads(raw), meta


def multipoint(points: list[tuple[float, float]]) -> str:
    """GeoJSON MultiPoint of (lon, lat), 6 decimals, sorted and de-duplicated, compact (the pinned parameter)."""
    pts = sorted({(round(lon, 6), round(lat, 6)) for lon, lat in points})
    return json.dumps({"type": "MultiPoint", "coordinates": [list(p) for p in pts]}, separators=(",", ":"))


def scenes_intersecting(key: str, geometry: str, dt_from: str, dt_to: str) -> tuple[list[dict], dict]:
    params = {"intersects": geometry, "datetime": f"{dt_from}/{dt_to}", "limit": 100, "sort": "asc"}
    d, meta = cached_get("scenes", key, SCENES_URL, params)
    if d["count"] > len(d["items"]):
        raise RuntimeError(f"{key}: {d['count']} scenes > one page; paginate")  # not hit for the three cases
    return d["items"], meta


def source(name: str) -> tuple[dict, dict]:
    return cached_get("source", name, SOURCE_URL + name)


def scene_plumes(scene_id: str) -> tuple[list[dict], dict]:
    d, meta = cached_get("scene_plumes", scene_id, PLUMES_URL, {"scene_id": scene_id, "limit": 1000})
    if d["total_count"] > len(d["items"]):
        raise RuntimeError(f"scene {scene_id}: {d['total_count']} plumes > one page")
    return d["items"], meta


def cache_files() -> list[Path]:
    c = cache_dir()
    return sorted(p for p in c.rglob("*.json") if not p.name.endswith(".meta.json")) if c.exists() else []
