"""First notified MARS plume per oil and gas source, from the committed MARS snapshot (the projection's event study).

    cd pipeline && .venv/bin/python mars_notified.py [--zip PATH]   # -> data/derived/mars_notified.json

MARS publishes no notification or response date. Its plume records carry a `notified` flag ("the plume was notified
to governments and/or OGMP 2.0 companies"), so the tile date of a source's first notified plume is the closest public
proxy for when the source was first notified (the notification follows the plume's validation, within about 15 days
by MARS's own definition). `fetch_mars.py` keeps no plume-level flag, so this small derivative is read from the same zip
it read; the zip's SHA-256 must equal data/MANIFEST.json's (the committed snapshot), otherwise the script refuses.
Raw data stays in data/raw/ (never committed).
"""
from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

from common import DERIVED, RAW, load_manifest, sha256_file, write_json

OUT = DERIVED / "mars_notified.json"


def derive(plumes: list[dict]) -> dict[str, dict]:
    """{source_name: {"firstNotified": date, "notifiedPlumes": n}} over oil and gas plumes with notified == True."""
    out: dict[str, dict] = {}
    for q in plumes:
        if q.get("sector") != "Oil and Gas" or q.get("notified") is not True or not q.get("source_name"):
            continue
        day = q["tile_date"][:10]
        cur = out.setdefault(q["source_name"], {"firstNotified": day, "notifiedPlumes": 0})
        cur["firstNotified"] = min(cur["firstNotified"], day)
        cur["notifiedPlumes"] += 1
    return dict(sorted(out.items()))


def main() -> None:
    args = sys.argv[1:]
    zp = Path(args[args.index("--zip") + 1]) if "--zip" in args else RAW / "mars" / "mars.zip"
    if not zp.exists():
        sys.exit(f"{zp} is absent: data/derived/mars_notified.json is committed; re-derive with --zip PATH")
    want = load_manifest()["files"]["mars_zip"]["sha256"]
    got = sha256_file(zp)
    if got != want:
        sys.exit(f"{zp} is not the committed MARS snapshot (sha256 {got[:12]}… vs MANIFEST {want[:12]}…): refused")
    with zipfile.ZipFile(zp) as z:
        name = next(n for n in z.namelist() if n.endswith("plumes.geojson"))
        plumes = [f["properties"] for f in json.load(io.TextIOWrapper(z.open(name)))["features"]]
    src = derive(plumes)
    write_json(OUT, {"schema": "methaneclause.mars_notified/1", "zipSha256": got,
                     "field": "plume property `notified` (boolean); firstNotified = tile date of the first such plume",
                     "licence": "UNEP IMEO MARS, CC BY-NC-SA 4.0", "sources": src})
    print(f"mars_notified: {len(src)} oil and gas sources with a notified plume -> {OUT.name}")


if __name__ == "__main__":
    main()
