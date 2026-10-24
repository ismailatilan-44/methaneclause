"""Carbon Mapper timelines for a not MARS-notified sample: the projection's natural-stop baseline (network, keyless).

    cd pipeline && .venv/bin/python fetch_cm_not_mars_notified.py --raw-dir DIR
        # -> data/derived/cm_timelines_not_mars_notified.json   (make cm-not-mars-notified RAW_DIR=..., ask first)

The sample is pre-registered in data/derived/projection_sources.json -> model.t4d.notMarsNotifiedSample: Carbon Mapper
oil and gas sources (IPCC code 1B2 in the source name) with at least one detection, farther than the join radius from
every MARS source marked notified, not already in cm_timelines.json or cm_timelines_notified.json, drawn with
random.Random(seed).sample(sorted(eligible), size). One request per sampled source with fetch_cm.py's endpoint, thread
pool and retries (common.http_get); raw responses stay in DIR (never committed); the derived timeline, the content
SHA-256 of each response, the licence, the seed and the rule are written to the committed file.
"""
from __future__ import annotations

import csv
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import CM_LICENCE, DERIVED, RADIUS_KM, content_sha256, haversine_km, http_get, now_utc, write_json
from fetch_cm import SRC_URL, WORKERS
from fetch_cm_notified import timeline

OUT = DERIVED / "cm_timelines_not_mars_notified.json"
OIL_AND_GAS = "_1B2_"


def rule() -> dict:
    return json.loads((DERIVED / "projection_sources.json").read_text(encoding="utf-8"))["model"]["t4d"][
        "notMarsNotifiedSample"]


def eligible() -> list[str]:
    """The pre-registered eligibility rule, on committed files only."""
    import numpy as np

    with open(DERIVED / "mars_sources.csv", newline="", encoding="utf-8") as f:
        notified = [(float(r["lat"]), float(r["lon"])) for r in csv.DictReader(f) if r["notified"] == "Yes"]
    nlat, nlon = np.array([a for a, _ in notified]), np.array([b for _, b in notified])
    have = set(json.loads((DERIVED / "cm_timelines.json").read_text(encoding="utf-8")))
    have |= set(json.loads((DERIVED / "cm_timelines_notified.json").read_text(encoding="utf-8"))["sources"])
    out = []
    with open(DERIVED / "cm_sources.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name = r["source_name"]
            if OIL_AND_GAS not in name or name in have or float(r["detection_date_count"] or 0) < 1:
                continue
            lat, lon = float(r["source_latitude"]), float(r["source_longitude"])
            if haversine_km(lat, lon, nlat, nlon).min() > RADIUS_KM:
                out.append(name)
    return sorted(out)


def sample() -> list[str]:
    rl = rule()
    pool = eligible()
    if len(pool) < rl["size"]:
        raise SystemExit(f"only {len(pool)} eligible sources, fewer than the pre-registered {rl['size']}")
    return sorted(random.Random(rl["seed"]).sample(pool, rl["size"]))


def main() -> None:
    args = sys.argv[1:]
    if "--raw-dir" not in args:
        sys.exit("--raw-dir DIR is required (raw responses are kept outside the repository)")
    raw = Path(args[args.index("--raw-dir") + 1])
    raw.mkdir(parents=True, exist_ok=True)
    rl, pool = rule(), eligible()
    names = sample()
    print(f"cm_not_mars_notified: {len(names)} of {len(pool)} eligible sources (seed {rl['seed']}), cached in {raw}",
          flush=True)

    def get(n: str) -> tuple[str, bytes, str | None]:
        p = raw / f"{n}.json"
        if p.exists():
            return n, p.read_bytes(), None
        b = http_get(SRC_URL + n)
        json.loads(b)  # must parse
        p.write_bytes(b)
        return n, b, now_utc()

    out, fetched = {}, []
    with ThreadPoolExecutor(WORKERS) as ex:
        for i, (n, b, t) in enumerate(ex.map(get, names), 1):
            out[n] = {**timeline(json.loads(b)), "contentSha256": content_sha256(b)}
            if t:
                fetched.append(t)
            if i % 100 == 0:
                print(f"  {i}/{len(names)}", flush=True)
    prev = json.loads(OUT.read_text()).get("fetchedAt") if OUT.exists() else None
    write_json(OUT, {"schema": "methaneclause.cm_timelines_not_mars_notified/1", "urlPrefix": SRC_URL,
                     "licence": CM_LICENCE, "fetchedAt": max(fetched) if fetched else prev,
                     "selection": rl["eligible"], "draw": rl["draw"], "seed": rl["seed"], "size": rl["size"],
                     "eligibleCount": len(pool), "sources": out}, indent=None)
    print(f"cm_not_mars_notified: {len(out)} timelines -> {OUT.name}")


if __name__ == "__main__":
    main()
