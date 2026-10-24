"""Carbon Mapper scene records for every claim the backtest opens at the join candidates (+ the demo cases), B7.

    cd pipeline && .venv/bin/python scene_records.py            # fetch on a cache miss, then write data/derived/scenes_v1/
    cd pipeline && OFFLINE=1 .venv/bin/python scene_records.py  # cache only (fails closed on a miss)

Scope: the facilities of `join_v1.json` (the 203 candidates) plus the three demo cases, restricted to those where the
backtest opens at least one claim (an episode after the first MARS plume date). Per facility, keyless and sequential:
  1. `/catalog/search/source/{name}` for every owned Carbon Mapper source (join rule 3)   rule 3 per-source query, origins
  2. `/catalog/scenes/annotated?intersects=<Point>` per plume-origin point (owned-source plumes within 1.5 km),
     instruments EMIT + Tanager (the only instruments with a floor), from the facility's first episode date to the
     catalogue end                                                                      rules 1 (cloud), 2 (footprint), 5
  3. `/catalog/plumes/annotated?bbox=` around the facility (every gas, today's catalogue) rule 3: plumes within 1.5 km
Raw responses are cached under data/raw/cm/backtest/ (gitignored; resumable: a cached response is never re-fetched).
Each network request is appended to data/raw/cm/backtest/requests.log. Only compact derived fields are committed:
`data/derived/scenes_v1/<facility>.json` (exactly what `cleanpass_rules.qualify` needs, plus `sceneRecordSha256` and
the content SHA-256 of every response) and `scenes_v1/INDEX.json` (scope, counts, fetch statistics).
"""
from __future__ import annotations

import json
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from common import (CM_API, CM_LICENCE, DERIVED, RADIUS_KM, RAW, ROOT, UA, content_sha256, haversine_km, now_utc,
                    sha256_bytes, sha256_file, write_json)

CACHE = RAW / "cm" / "backtest"
OUT = DERIVED / "scenes_v1"
LOG = CACHE / "requests.log"
SCENES_URL = f"{CM_API}/catalog/scenes/annotated"
PLUMES_URL = f"{CM_API}/catalog/plumes/annotated"
SOURCE_URL = f"{CM_API}/catalog/search/source/"
INSTRUMENTS = ["emi", "tan"]  # the instruments with a floor in POLICY_V2 (others are refused UNKNOWN_INSTRUMENT)
DELAY_S = 0.25  # between network requests (sequential, polite)
NEAR_MARGIN_KM = 0.2  # bbox half-side = radius + margin; the 1.5 km test is done locally (haversine)
SCHEMA = "methaneclause.scenes/1"
SCENE_FIELDS = ("id", "name", "timestamp", "published_at", "instrument", "cloud_cover_pct", "cloud_cover_pct_assessed",
                "not_cloudy", "published_plume_count")


class OfflineMiss(RuntimeError):
    pass


# ------------------------------------------------------------------------------------------------ polite fetch
_stats = {"requests": 0, "bytes": 0, "retries": 0}


def polite_get(url: str, params: dict | None = None, tries: int = 5) -> bytes:
    """One GET, sequential, DELAY_S before it; retries 429 / 5xx / network errors with exponential backoff (2, 4, 8,
    16 s); any other HTTP status fails at once (no retry storm on a bad query)."""
    last: Exception | None = None
    for i in range(tries):
        time.sleep(DELAY_S if i == 0 else 2 ** i)
        _stats["requests"] += 1
        try:
            r = requests.get(url, params=params, timeout=90, headers={"User-Agent": UA})
            status, body = r.status_code, r.content
            _log(r.url, status, len(body))
            if status == 200:
                _stats["bytes"] += len(body)
                return body
            last = RuntimeError(f"HTTP {status} for {r.url}")
            if status != 429 and status < 500:
                raise last
        except requests.RequestException as e:
            _log(url, -1, 0)
            last = e
        _stats["retries"] += 1
    raise last  # type: ignore[misc]


def _log(url: str, status: int, n: int) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(f"{now_utc()}\t{status}\t{n}\t{url}\n")


def cached(kind: str, key: str, url: str, params: dict | None = None) -> tuple[dict, dict]:
    """(parsed JSON, meta). Fetched once; afterwards served from the cache, whose hashes are re-checked (fail closed)."""
    body, meta_p = CACHE / kind / f"{key}.json", CACHE / kind / f"{key}.meta.json"
    if body.exists() and meta_p.exists():
        raw, meta = body.read_bytes(), json.loads(meta_p.read_text())
    else:
        if os.environ.get("OFFLINE") == "1":
            raise OfflineMiss(f"not cached and OFFLINE=1: {kind}/{key}")
        raw = polite_get(url, params)
        json.loads(raw)
        meta = {"url": url, "params": params or {}, "fetchedAt": now_utc(), "sha256": sha256_bytes(raw),
                "contentSha256": content_sha256(raw)}
        body.parent.mkdir(parents=True, exist_ok=True)
        body.write_bytes(raw)
        meta_p.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n")
    if sha256_bytes(raw) != meta["sha256"] or content_sha256(raw) != meta["contentSha256"]:
        raise ValueError(f"cache corrupted: {body}")
    return json.loads(raw), meta


# ------------------------------------------------------------------------------------------------ pure helpers
def ts(s: str) -> int:
    s = s if (s.endswith("Z") or "+" in s[10:]) else s + "Z"
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def point_key(lon: float, lat: float) -> str:
    return f"{lon:.6f}_{lat:.6f}"


def origin_points(fac: dict, source_plumes: dict[str, list[dict]]) -> tuple[list[list[float]], list[int | None]]:
    """Distinct plume-origin points (lon, lat; 6 decimals, sorted) of the owned sources' plumes within RADIUS_KM of the
    facility, each with the earliest `published_at` among its plumes (None = no plume there has a publication time)."""
    pub: dict[tuple[float, float], list[int]] = {}
    for plumes in source_plumes.values():
        for p in plumes:
            lon, lat = (p.get("geometry_json") or {}).get("coordinates", [None, None])[:2]
            if lon is None or float(haversine_km(fac["lat"], fac["lon"], lat, lon)) > RADIUS_KM:
                continue
            pub.setdefault((round(lon, 6), round(lat, 6)), []).extend(
                [ts(p["published_at"])] if p.get("published_at") else [])
    pts = sorted(pub)
    return [list(p) for p in pts], [min(pub[p]) if pub[p] else None for p in pts]


def near_plumes(fac: dict, items: list[dict]) -> list[dict]:
    """Plumes (any gas) whose origin lies within RADIUS_KM of the facility, with their scene id."""
    out = []
    for p in items:
        lon, lat = (p.get("geometry_json") or {}).get("coordinates", [None, None])[:2]
        if lon is None:
            continue
        dkm = float(haversine_km(fac["lat"], fac["lon"], lat, lon))
        if dkm <= RADIUS_KM:
            hidden = bool(p.get("hide_emission")) or p.get("emission_auto") is None
            out.append({"plumeId": p["plume_id"], "sceneId": p.get("scene_id"), "gas": p.get("gas"),
                        "sceneAt": p.get("scene_timestamp"), "instrument": p.get("instrument"),
                        "distKm": round(dkm, 3), "rateHidden": hidden,
                        "rateKgH": None if hidden else round(float(p["emission_auto"]), 1),
                        "publishedAt": p.get("published_at")})
    return sorted(out, key=lambda x: x["plumeId"])


def facility_near_plumes(near: list[dict], src_plumes: dict[str, list[dict]]) -> list[dict]:
    """The nearby plumes as committed: time as unix seconds, and the owned source the plume belongs to (None = a
    neighbour's source, i.e. a Carbon Mapper source that join rule 3 gives to another target, or no source)."""
    owner = {p["plume_id"]: n for n in sorted(src_plumes) for p in src_plumes[n]}
    return [{"plumeId": p["plumeId"], "sceneId": p["sceneId"], "gas": p["gas"], "instrument": p["instrument"],
             "sceneAt": ts(p["sceneAt"]) if p["sceneAt"] else None,
             "publishedAt": ts(p["publishedAt"]) if p["publishedAt"] else None, "distKm": p["distKm"],
             "rateKgH": p["rateKgH"], "rateHidden": p["rateHidden"], "ownSource": owner.get(p["plumeId"])}
            for p in near]


def source_hits(plumes: list[dict], scene: dict) -> list[str]:
    """Plumes of one linked source on this scene: same scene id, or (no scene id) same date and instrument — the rule
    `clean_passes.py` applies to the case files."""
    return sorted(p["plume_id"] for p in plumes
                  if p.get("scene_id") == scene["id"]
                  or (str(p.get("scene_timestamp", ""))[:10] == scene["timestamp"][:10]
                      and p.get("instrument") == scene["instrument"]))


def compact_scene(item: dict, inside: list[int], src_plumes: dict[str, list[dict]], near: list[dict]) -> dict:
    """The fields the rules read, plus the scene record's hash (spec-v2 §4 sceneRecordSha256)."""
    from clean_passes import record_sha256

    per_src = {n: h for n in sorted(src_plumes) if (h := source_hits(src_plumes[n], item))}
    return {"sceneProviderId": item["id"], "sceneName": item["name"], "sceneAt": ts(item["timestamp"]),
            "publishedAt": ts(item["published_at"]) if item.get("published_at") else None,
            "instrument": item["instrument"], "cloudCoverPct": item.get("cloud_cover_pct"),
            "cloudCoverPctAssessed": item.get("cloud_cover_pct_assessed"), "notCloudy": item.get("not_cloudy"),
            "publishedPlumeCount": item.get("published_plume_count"),
            "originPointsInside": sorted(inside), "perSourcePlumeIds": per_src,
            "plumesNearFacility": sorted(p["plumeId"] for p in near if p["sceneId"] == item["id"]),
            "sceneRecordSha256": record_sha256(item)}


# ------------------------------------------------------------------------------------------------ scope
def scope() -> tuple[list[dict], str]:
    """Facilities to fetch: join candidates + demo cases with >= 1 backtest claim; each with its claims' episode dates."""
    import backtest as bt

    cands = {c["mars_source"] for c in json.loads((DERIVED / "join_v1.json").read_text())["candidates"]}
    facs, end, _ = bt.facilities()
    out = []
    for f in facs:
        if f["name"] not in cands | set(bt.CASES) or not f["passes"] or not f["start"]:
            continue
        eps, _ = bt.episodes(f["passes"], mars_dates=f["mars_dates"], start=f["start"], l2=f["l2_dates"])
        if eps:
            out.append({"name": f["name"], "lat": f["lat"], "lon": f["lon"], "cmSources": f["cm_sources"],
                        "joinCandidate": f["name"] in cands, "claims": [e["date"] for e in eps]})
    return out, end


def _scenes_paged(key: str, params: dict) -> tuple[list[dict], list[dict]]:
    items, metas, offset = [], [], 0
    while True:
        p = {**params, "offset": offset} if offset else params
        d, m = cached("scenes", key if not offset else f"{key}_o{offset}", SCENES_URL, p)
        items += d["items"]
        metas.append(m)
        if len(items) >= d["count"] or not d["items"]:
            return items, metas
        offset = len(items)


def _plumes_paged(key: str, params: dict) -> tuple[list[dict], list[dict], int]:
    items, metas, offset = [], [], 0
    while True:
        p = {**params, "offset": offset} if offset else params
        d, m = cached("near", key if not offset else f"{key}_o{offset}", PLUMES_URL, p)
        items += d["items"]
        metas.append(m)
        if len(items) >= d["bbox_count"] or not d["items"]:
            return items, metas, d["bbox_count"]
        offset = len(items)


def build(fac: dict, end: str) -> dict:
    """Fetch (cache first) and derive one facility's scene file."""
    src, src_q = {}, []
    for n in fac["cmSources"]:
        d, m = cached("source", n, SOURCE_URL + n)
        src[n] = [p for p in d.get("plumes") or []]
        src_q.append({"cmSource": n, "url": m["url"], "params": m["params"], "contentSha256": m["contentSha256"],
                      "fetchedAt": m["fetchedAt"], "plumes": len(src[n])})
    pts, pub = origin_points(fac, src)
    dt_from, dt_to = fac["claims"][0] + "T00:00:00Z", (date.fromisoformat(end) + timedelta(days=1)).isoformat() + "T00:00:00Z"
    inside: dict[str, list[int]] = {}
    records: dict[str, dict] = {}
    pt_q = []
    for i, (lon, lat) in enumerate(pts):
        g = json.dumps({"type": "Point", "coordinates": [lon, lat]}, separators=(",", ":"))
        params = {"intersects": g, "datetime": f"{dt_from}/{dt_to}", "instruments": INSTRUMENTS, "limit": 500,
                  "sort": "asc"}
        its, ms = _scenes_paged(f"{point_key(lon, lat)}_{fac['claims'][0]}", params)
        pt_q.append({"point": [lon, lat], "url": ms[0]["url"], "params": ms[0]["params"],
                     "contentSha256": [m["contentSha256"] for m in ms], "fetchedAt": ms[0]["fetchedAt"],
                     "scenes": len(its)})
        for it in its:
            inside.setdefault(it["id"], []).append(i)
            records.setdefault(it["id"], it)
    dlat = (RADIUS_KM + NEAR_MARGIN_KM) / 110.574
    dlon = (RADIUS_KM + NEAR_MARGIN_KM) / (111.320 * max(math.cos(math.radians(fac["lat"])), 1e-6))
    bbox = [round(fac["lon"] - dlon, 6), round(fac["lat"] - dlat, 6), round(fac["lon"] + dlon, 6),
            round(fac["lat"] + dlat, 6)]
    near_items, near_ms, bbox_count = _plumes_paged(fac["name"], {"bbox": bbox, "limit": 1000})
    near = near_plumes(fac, near_items)
    scenes = sorted((compact_scene(records[k], inside[k], src, near) for k in records),
                    key=lambda s: (s["sceneAt"], s["sceneProviderId"]))
    fetched = [q["fetchedAt"] for q in src_q + pt_q] + [m["fetchedAt"] for m in near_ms]
    return {
        "schema": SCHEMA, "facility": fac["name"], "joinCandidate": fac["joinCandidate"],
        "lat": fac["lat"], "lon": fac["lon"], "radiusKm": RADIUS_KM, "claims": fac["claims"],
        "linkedCmSources": sorted(fac["cmSources"]), "sourceQueries": src_q,
        "originPoints": pts, "originPointPublishedAt": pub,
        "sceneWindow": [dt_from, dt_to], "instruments": INSTRUMENTS, "pointQueries": pt_q,
        "nearQuery": {"url": near_ms[0]["url"], "params": near_ms[0]["params"],
                      "contentSha256": [m["contentSha256"] for m in near_ms], "fetchedAt": near_ms[0]["fetchedAt"],
                      "bboxCount": bbox_count, "withinRadius": len(near)},
        "nearPlumes": facility_near_plumes(near, src),
        "evaluatedAt": max(ts(x) for x in fetched),
        "scenes": scenes,
    }


def _log_stats() -> dict:
    if not LOG.exists():
        return {"requests": 0}
    rows = [ln.split("\t") for ln in LOG.read_text().splitlines() if ln.strip()]
    return {"requests": len(rows), "ok": sum(r[1] == "200" for r in rows), "bytes": sum(int(r[2]) for r in rows),
            "first": rows[0][0] if rows else None, "last": rows[-1][0] if rows else None,
            "byEndpoint": {k: sum(1 for r in rows if k in r[3]) for k in ("search/source", "scenes/annotated",
                                                                          "plumes/annotated")}}


def main(only: list[str] | None = None) -> None:
    facs, end = scope()
    if only:  # trial run on named facilities: no clean-up, no INDEX
        facs = [f for f in facs if f["name"] in only]
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    written, failed = [], {}
    for k, f in enumerate(facs):
        try:
            doc = build(f, end)
        except OfflineMiss:
            raise
        except Exception as e:  # one facility failing is recorded and reported, never written half-done
            failed[f["name"]] = f"{type(e).__name__}: {e}"[:300]
            print(f"[{k + 1}/{len(facs)}] {f['name']}: FAILED {failed[f['name']]}", flush=True)
            continue
        write_json(OUT / f"{f['name']}.json", doc, indent=None)
        written.append(f["name"])
        print(f"[{k + 1}/{len(facs)}] {f['name']}: {len(doc['originPoints'])} origins, {len(doc['scenes'])} scenes; "
              f"requests this run {_stats['requests']} ({time.time() - t0:.0f} s)", flush=True)
    if only:
        print(f"trial: {written} written, failed {failed}; requests {_stats['requests']}")
        return
    for p in OUT.glob("*.json"):
        if p.name != "INDEX.json" and p.stem not in written:
            p.unlink()  # a facility that left the scope (or failed) keeps no stale file
    index = {
        "schema": "methaneclause.scenes_index/1",
        "scope": "join_v1 candidates + the three demo cases with >= 1 backtest claim",
        "catalogueEnd": end, "facilities": len(facs), "written": len(written), "failed": failed,
        "claims": sum(len(f["claims"]) for f in facs if f["name"] in written),
        "files": {n: sha256_file(OUT / f"{n}.json") for n in sorted(written)},
        "fetch": _log_stats(),
        "generatedFrom": [{"path": str(Path(__file__).resolve().relative_to(ROOT)), "sha256": sha256_file(Path(__file__))},
                          {"path": "data/derived/join_v1.json", "sha256": sha256_file(DERIVED / "join_v1.json")}],
        "licence": f"{CM_LICENCE} — Data by Carbon Mapper; derived scene fields only (no imagery, no signed URLs)",
    }
    write_json(OUT / "INDEX.json", index)
    print(f"scenes_v1: {len(written)} of {len(facs)} facilities written, {len(failed)} failed; "
          f"requests this run {_stats['requests']} (retries {_stats['retries']}), "
          f"{_stats['bytes'] / 1e6:.1f} MB, {time.time() - t0:.0f} s; log total {index['fetch']}")


if __name__ == "__main__":
    import sys

    main(sys.argv[1:] or None)
