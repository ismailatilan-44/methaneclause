"""Re-fetch the OpenStreetMap sample around TKM_S_276 via the Overpass API.

One POST request. Writes osm_tkm.geojson (+ osm_tkm.meta.json with counts,
distances and the CORS headers Overpass returned for a non-localhost Origin).
Data (c) OpenStreetMap contributors, ODbL 1.0.  Run: python3.12 -I fetch_osm.py
(macOS /usr/bin/python3 3.9 + LibreSSL 2.8.3 fails the TLS handshake with overpass-api.de; use 3.12.)
"""
import json
import math
import os
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(ROOT, "data", "derived", "story")   # samples the story page is built from (scripts/build_story_data.py)
LAT, LON = 39.48457, 53.72005
RADIUS_M = 6000
RING_KM = 1.5
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]
ENDPOINT = ENDPOINTS[0]
ORIGIN = "http://127.0.0.1:4180"

QUERY = f"""
[out:json][timeout:60];
(
  nwr(around:{RADIUS_M},{LAT},{LON})[man_made=pipeline];
  nwr(around:{RADIUS_M},{LAT},{LON})[pipeline];
  nwr(around:{RADIUS_M},{LAT},{LON})[substance];
  nwr(around:{RADIUS_M},{LAT},{LON})[man_made=petroleum_well];
  nwr(around:{RADIUS_M},{LAT},{LON})[man_made~"^(works|storage_tank|pumping_station|compressor_station|flare|chimney|tower|mast)$"];
  nwr(around:{RADIUS_M},{LAT},{LON})[industrial];
  nwr(around:{RADIUS_M},{LAT},{LON})[landuse=industrial];
  nwr(around:{RADIUS_M},{LAT},{LON})[power];
  way(around:{RADIUS_M},{LAT},{LON})[highway];
  way(around:{RADIUS_M},{LAT},{LON})[railway];
  nwr(around:{RADIUS_M},{LAT},{LON})[place];
  nwr(around:{RADIUS_M},{LAT},{LON})[landuse=residential];
);
out geom;
"""


def hav_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def point_seg_km(lat, lon, a, b):
    """Distance from (lat,lon) to segment a-b on a local equirectangular plane (fine at <10 km)."""
    kx = 111.320 * math.cos(math.radians(lat))
    ky = 110.574
    ax, ay = (a[0] - lon) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lon) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / L2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py)


def min_dist_km(geom):
    t = geom["type"]
    if t == "Point":
        return hav_km(LAT, LON, geom["coordinates"][1], geom["coordinates"][0])
    lines = geom["coordinates"] if t == "MultiLineString" else (
        [geom["coordinates"]] if t == "LineString" else geom["coordinates"])
    if t == "MultiPolygon":
        lines = [ring for poly in geom["coordinates"] for ring in poly]
    best = 1e9
    for line in lines:
        for i in range(len(line) - 1):
            best = min(best, point_seg_km(LAT, LON, line[i], line[i + 1]))
        if len(line) == 1:
            best = min(best, hav_km(LAT, LON, line[0][1], line[0][0]))
    return best


def to_geojson(el):
    tags = el.get("tags", {})
    if el["type"] == "node":
        geom = {"type": "Point", "coordinates": [el["lon"], el["lat"]]}
    elif el["type"] == "way":
        coords = [[p["lon"], p["lat"]] for p in el.get("geometry", []) if p]
        closed = len(coords) > 3 and coords[0] == coords[-1]
        area = closed and ("landuse" in tags or "building" in tags or "industrial" in tags or tags.get("area") == "yes")
        geom = {"type": "Polygon", "coordinates": [coords]} if area else {"type": "LineString", "coordinates": coords}
    else:  # relation: keep member ways as a MultiLineString outline
        parts = []
        for m in el.get("members", []):
            if m.get("type") == "way" and m.get("geometry"):
                parts.append([[p["lon"], p["lat"]] for p in m["geometry"] if p])
        if not parts:
            return None
        geom = {"type": "MultiLineString", "coordinates": parts}
    props = dict(tags)
    props["@id"] = f"{el['type']}/{el['id']}"
    props["@dist_km"] = round(min_dist_km(geom), 3)
    return {"type": "Feature", "id": props["@id"], "properties": props, "geometry": geom}


def classify(p):
    if p.get("man_made") == "pipeline" or ("pipeline" in p and "substance" in p):
        return "pipeline"
    if "pipeline" in p:
        return "pipeline_feature"
    if p.get("man_made") == "petroleum_well":
        return "well"
    if "industrial" in p or p.get("landuse") == "industrial" or p.get("man_made") in (
            "works", "storage_tank", "pumping_station", "compressor_station", "flare", "chimney"):
        return "industrial"
    if "power" in p:
        return "power"
    if "highway" in p:
        return "road"
    if "railway" in p:
        return "railway"
    if "place" in p or p.get("landuse") == "residential":
        return "settlement"
    return "other"


def main():
    global ENDPOINT
    body = urllib.parse.urlencode({"data": QUERY}).encode()
    data = None
    for ep in ENDPOINTS:  # main instance first; public mirrors only if it is overloaded
        req = urllib.request.Request(ep, data=body, headers={
            "Origin": ORIGIN, "User-Agent": "MethaneClause-story/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                hdrs = {k: v for k, v in r.headers.items() if k.lower().startswith("access-control") or k.lower() in ("content-type", "server")}
                data = json.load(r)
            ENDPOINT = ep
            break
        except Exception as e:  # noqa: BLE001 - report and try the next mirror
            print(f"{ep}: {e}")
    if data is None:
        raise SystemExit("all Overpass endpoints failed")
    feats = [f for f in (to_geojson(e) for e in data["elements"]) if f]
    for f in feats:
        f["properties"]["@class"] = classify(f["properties"])
    feats.sort(key=lambda f: f["properties"]["@dist_km"])
    gj = {
        "type": "FeatureCollection",
        "attribution": "© OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)",
        "query": {"endpoint": ENDPOINT, "center": [LON, LAT], "radius_m": RADIUS_M,
                  "osm_base": data.get("osm3s", {}).get("timestamp_osm_base")},
        "features": feats,
    }
    with open(os.path.join(HERE, "osm_tkm.geojson"), "w") as fh:
        json.dump(gj, fh, ensure_ascii=False, separators=(",", ":"))
    counts = {}
    for f in feats:
        counts[f["properties"]["@class"]] = counts.get(f["properties"]["@class"], 0) + 1
    pipes = [f for f in feats if f["properties"]["@class"] == "pipeline"]
    meta = {
        "fetched_with": "fetch_osm.py", "endpoint": ENDPOINT, "origin_sent": ORIGIN,
        "response_headers": hdrs, "osm_base": gj["query"]["osm_base"],
        "n_elements": len(data["elements"]), "counts": counts,
        "pipelines": [{"id": f["id"], "dist_km": f["properties"]["@dist_km"],
                       "inside_ring": f["properties"]["@dist_km"] <= RING_KM,
                       "tags": {k: v for k, v in f["properties"].items() if not k.startswith("@")}} for f in pipes],
        "nearest_by_class": {c: next((dict(id=f["id"], dist_km=f["properties"]["@dist_km"],
                                           name=f["properties"].get("name") or f["properties"].get("name:en"))
                                      for f in feats if f["properties"]["@class"] == c), None) for c in counts},
    }
    with open(os.path.join(HERE, "osm_tkm.meta.json"), "w") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print(json.dumps(meta, ensure_ascii=False, indent=1)[:6000])


if __name__ == "__main__":
    main()
