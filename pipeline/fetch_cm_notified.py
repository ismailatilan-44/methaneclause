"""Carbon Mapper timelines for the projection's event study beyond the join neighbourhood (network, keyless).

    cd pipeline && .venv/bin/python fetch_cm_notified.py --raw-dir DIR   # -> data/derived/cm_timelines_notified.json

The committed cm_timelines.json covers the Carbon Mapper sources near the MARS sources labelled "absent" today. The
event study (projection_evidence.line2) also needs the sources owned by every other notified oil and gas MARS source
with feedback Yes or No (nearest notified source within the join radius). This script fetches only those missing
timelines, with fetch_cm.py's settings (same endpoint, same thread pool, common.http_get retries with backoff), keeps
raw responses in DIR (never committed) and writes the derived timeline plus the content SHA-256 of each response to
a separate file, so cm_timelines.json and everything the backtest reads stay untouched.
"""
from __future__ import annotations

import csv
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from common import CM_LICENCE, DERIVED, RADIUS_KM, content_sha256, http_get, now_utc, write_json
from fetch_cm import SRC_URL, WORKERS
from projection_evidence import facilities_for_event_study

OUT = DERIVED / "cm_timelines_notified.json"


def missing_names() -> list[str]:
    with open(DERIVED / "mars_sources.csv", newline="", encoding="utf-8") as f:
        mars = list(csv.DictReader(f))
    with open(DERIVED / "cm_sources.csv", newline="", encoding="utf-8") as f:
        cm = list(csv.DictReader(f))
    tl = json.loads((DERIVED / "cm_timelines.json").read_text())
    facs = facilities_for_event_study(mars, cm, tl, RADIUS_KM)
    return sorted({n for f in facs.values() for n in f["cm"] if n not in tl})


def timeline(d: dict) -> dict:
    """The same derived fields as fetch_cm.derive_source."""
    instr: dict[str, set] = {}
    for sc in d.get("scenes") or []:
        instr.setdefault(sc["timestamp"][:10], set()).add(sc.get("instrument") or "unk")
    obs = sorted(set(d.get("observation_dates") or []))
    lon, lat = (d.get("point") or {}).get("coordinates", [None, None])
    return {"lat": lat, "lon": lon, "obs": [[o, "+".join(sorted(instr.get(o, {"unk"})))] for o in obs],
            "det": sorted(set(d.get("detection_dates") or []))}


def main() -> None:
    args = sys.argv[1:]
    if "--raw-dir" not in args:
        sys.exit("--raw-dir DIR is required (raw responses are kept outside the repository)")
    raw = Path(args[args.index("--raw-dir") + 1])
    raw.mkdir(parents=True, exist_ok=True)
    names = missing_names()
    print(f"cm_notified: {len(names)} Carbon Mapper timelines to fetch (cached in {raw})", flush=True)

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
    write_json(OUT, {"schema": "methaneclause.cm_timelines_notified/1", "urlPrefix": SRC_URL, "licence": CM_LICENCE,
                     "fetchedAt": max(fetched) if fetched else prev, "selection":
                     "Carbon Mapper sources owned by notified oil and gas MARS sources with feedback Yes or No "
                     "(nearest within the join radius) that cm_timelines.json does not cover",
                     "sources": out}, indent=None)
    print(f"cm_notified: {len(out)} timelines -> {OUT.name}")


if __name__ == "__main__":
    main()
