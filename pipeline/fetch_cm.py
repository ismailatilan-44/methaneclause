"""Fetch Carbon Mapper (keyless API v1): the CH4 oil & gas (1B2) sources CSV and per-source timelines.

Timelines are fetched for every Carbon Mapper source within RADIUS_KM of a MARS notified O&G 'absent'
source (the join neighbourhood, ~2,000 sources) plus the spec's case sources. Raw responses are cached
under data/raw/cm/ (gitignored); only derived fields and the SHA-256 of each raw response are kept.
Run fetch_mars.py first. REFRESH=1 re-downloads everything.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pandas as pd

from common import (CASE_CM_SOURCES, CM_API, CM_LICENCE, DERIVED, RADIUS_KM, RAW, haversine_km, http_get,
                    content_sha256, load_manifest, mars_target, now_utc, save_manifest, sha256_bytes, write_json)

CSV_URL = f"{CM_API}/catalog/sources-csv"
CSV_PARAMS = {"plume_gas": "CH4", "sectors": "1B2"}
SRC_URL = f"{CM_API}/catalog/search/source/"
RAW_CM = RAW / "cm"
PLUME_FIELDS = ["source_name", "plume_id", "scene_timestamp", "instrument", "platform", "emission_auto",
                "emission_uncertainty_auto", "plume_quality", "emission_version", "processing_software",
                "published_at", "modified", "lat", "lon"]
WORKERS = 4


def raw_path(name: str) -> Path:
    return RAW_CM / "source" / f"{name}.json"


def fetch_source(name: str, refresh: bool) -> tuple[str, tuple[str, str], str | None]:
    """Return (name, (raw sha256, content sha256), fetchedAt-or-None-if-cached)."""
    p = raw_path(name)
    if refresh or not p.exists():
        b = http_get(SRC_URL + name)
        json.loads(b)  # must parse
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b)
        return name, (sha256_bytes(b), content_sha256(b)), now_utc()
    b = p.read_bytes()
    return name, (sha256_bytes(b), content_sha256(b)), None


def fetch_sources(names: list[str], refresh: bool) -> dict[str, tuple[tuple[str, str], str | None]]:
    out = {}
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (n, h, t) in enumerate(ex.map(lambda n: fetch_source(n, refresh), names), 1):
            out[n] = (h, t)
            if i % 250 == 0:
                print(f"  {i}/{len(names)} source timelines", flush=True)
    return out


def derive_source(name: str) -> tuple[dict, list[dict]]:
    d = json.loads(raw_path(name).read_text())
    instr: dict[str, set] = {}
    for sc in d.get("scenes") or []:
        instr.setdefault(sc["timestamp"][:10], set()).add(sc.get("instrument") or "unk")
    obs = sorted(set(d.get("observation_dates") or []))
    lon, lat = (d.get("point") or {}).get("coordinates", [None, None])
    tl = {"lat": lat, "lon": lon,
          "obs": [[o, "+".join(sorted(instr.get(o, {"unk"})))] for o in obs],
          "det": sorted(set(d.get("detection_dates") or []))}
    plumes = []
    for p in d.get("plumes") or []:
        c = (p.get("geometry_json") or {}).get("coordinates") or [None, None]
        row = {k: p.get(k) for k in PLUME_FIELDS}
        row.update(source_name=name, lon=c[0], lat=c[1])
        plumes.append(row)
    return tl, plumes


def update_manifest_sources(man: dict, got: dict) -> None:
    entry = man.setdefault("cm_source_responses", {"url_prefix": SRC_URL, "licence": CM_LICENCE, "sha256": {}})
    entry["note"] = ("sha256 = raw response bytes (changes on every fetch: responses embed expiring signed image "
                     "URLs); content_sha256 = JCS of the response without signed URLs (stable while the data is).")
    entry["sha256"].update({n: h[0] for n, (h, _) in got.items()})
    entry.setdefault("content_sha256", {}).update({n: h[1] for n, (h, _) in got.items()})
    fetched = [t for _, t in got.values() if t]
    if fetched:
        entry["lastFetchedAt"] = max(fetched)
    lines = "".join(f"{n}:{h}\n" for n, h in sorted(entry["content_sha256"].items()))
    agg = hashlib.sha256(lines.encode()).hexdigest()
    entry["count"], entry["aggregate_sha256"] = len(entry["sha256"]), agg


def main() -> None:
    refresh = os.environ.get("REFRESH") == "1" or "--refresh" in sys.argv
    man = load_manifest()
    csv_raw = RAW_CM / "sources.csv"
    if refresh or not csv_raw.exists():
        csv_raw.parent.mkdir(parents=True, exist_ok=True)
        print("downloading Carbon Mapper sources CSV ...", flush=True)
        csv_raw.write_bytes(http_get(CSV_URL, params=CSV_PARAMS, timeout=180))
        man["files"]["cm_sources_csv"] = {"url": CSV_URL + "?plume_gas=CH4&sectors=1B2", "fetchedAt": now_utc(),
                                          "licence": CM_LICENCE}
    b = csv_raw.read_bytes()
    man["files"].setdefault("cm_sources_csv", {"url": CSV_URL + "?plume_gas=CH4&sectors=1B2", "licence": CM_LICENCE,
                                               "fetchedAt": "unknown (cached)"})
    man["files"]["cm_sources_csv"].update(sha256=sha256_bytes(b), bytes=len(b))
    cm = pd.read_csv(io.BytesIO(b))
    cm = cm[["source_name", "source_latitude", "source_longitude", "observation_date_count",
             "detection_date_count", "source_persistence", "source_emission", "source_emission_uncertainty"]]
    cm = cm.sort_values("source_name").reset_index(drop=True)
    cm.to_csv(DERIVED / "cm_sources.csv", index=False)

    # join neighbourhood: CM sources within RADIUS_KM of any MARS notified O&G 'absent' source
    mars = mars_target(pd.read_csv(DERIVED / "mars_sources.csv"))
    la, lo = cm["source_latitude"].to_numpy(), cm["source_longitude"].to_numpy()
    near = np.zeros(len(cm), bool)
    for lat, lon in zip(mars["lat"], mars["lon"]):
        near |= haversine_km(lat, lon, la, lo) <= RADIUS_KM
    names = sorted(set(cm.loc[near, "source_name"]) | {n for v in CASE_CM_SOURCES.values() for n in v})
    print(f"CM: {len(cm)} sources in CSV; fetching {len(names)} timelines (cached unless REFRESH=1)", flush=True)
    got = fetch_sources(names, refresh)
    update_manifest_sources(man, got)
    save_manifest(man)

    timelines, plumes = {}, []
    for n in names:
        tl, pl = derive_source(n)
        timelines[n] = tl
        plumes += pl
    write_json(DERIVED / "cm_timelines.json", timelines, indent=None)
    pdf = pd.DataFrame(plumes, columns=PLUME_FIELDS).sort_values(["source_name", "scene_timestamp", "plume_id"])
    pdf.to_csv(DERIVED / "cm_plumes.csv", index=False)
    print(f"CM: {len(timelines)} timelines, {len(pdf)} plume rows ({pdf.plume_id.nunique()} unique plume ids)")


if __name__ == "__main__":
    main()
