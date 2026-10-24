"""Fetch the UNEP IMEO MARS public GeoJSON zip and keep derived fields only.

Source: https://unepazeconomyadlsstorage.blob.core.windows.net/public/unep_methanedata_detected_plumes_geojson.zip
(linked from https://methanedata.unep.org). Licence CC BY-NC-SA 4.0.
Fallback if the blob ever blocks scripted downloads: download the zip in a browser from
methanedata.unep.org, save it as data/raw/mars/mars.zip and re-run (the script reuses it and records its hash).
"""
from __future__ import annotations

import io
import json
import os
import sys
import zipfile

import pandas as pd

from common import DERIVED, MARS_LICENCE, RAW, http_get, load_manifest, now_utc, save_manifest, sha256_file

URL = "https://unepazeconomyadlsstorage.blob.core.windows.net/public/unep_methanedata_detected_plumes_geojson.zip"
ZIP = RAW / "mars" / "mars.zip"

SRC_FIELDS = ["source_name", "lat", "lon", "country", "sector", "source_type", "persistency", "persistency_std",
              "persistency_category", "n_plumes_detected", "id_last_plume", "last_plume_date", "notified",
              "feedback", "feedback_operator", "feedback_government", "emission_status"]
PLUME_FIELDS = ["id_plume", "source_name", "satellite", "tile_date", "lat", "lon", "ch4_fluxrate",
                "ch4_fluxrate_std", "detection_institution", "country", "sector"]


def main() -> None:
    refresh = os.environ.get("REFRESH") == "1" or "--refresh" in sys.argv
    man = load_manifest()
    if refresh or not ZIP.exists():
        ZIP.parent.mkdir(parents=True, exist_ok=True)
        print("downloading MARS zip ...", flush=True)
        ZIP.write_bytes(http_get(URL, timeout=300))
        fetched = now_utc()
    else:
        fetched = man["files"].get("mars_zip", {}).get("fetchedAt", "unknown (cached)")
    digest = sha256_file(ZIP)

    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        src = json.load(io.TextIOWrapper(z.open(next(n for n in names if n.endswith("sources.geojson")))))
        plu = json.load(io.TextIOWrapper(z.open(next(n for n in names if n.endswith("plumes.geojson")))))

    s = pd.DataFrame([f["properties"] for f in src["features"]]).reindex(columns=SRC_FIELDS)
    s = s.sort_values("source_name")
    p = pd.DataFrame([f["properties"] for f in plu["features"]]).reindex(columns=PLUME_FIELDS)
    p = p[p["sector"] == "Oil and Gas"].sort_values(["source_name", "tile_date", "id_plume"])
    as_of = str(pd.DataFrame([f["properties"] for f in plu["features"]])["tile_date"].max())

    DERIVED.mkdir(parents=True, exist_ok=True)
    s.to_csv(DERIVED / "mars_sources.csv", index=False)
    p.to_csv(DERIVED / "mars_plumes_og.csv", index=False)

    man["files"]["mars_zip"] = {"url": URL, "fetchedAt": fetched, "sha256": digest,
                                "bytes": ZIP.stat().st_size, "licence": MARS_LICENCE,
                                "members": sorted(names), "mars_as_of_tile_date": as_of}
    save_manifest(man)
    print(f"MARS: {len(s)} sources, {len(p)} O&G plumes, as-of {as_of}, zip sha256 {digest[:16]}…")


if __name__ == "__main__":
    main()
