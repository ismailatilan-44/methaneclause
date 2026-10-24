"""Re-fetch Carbon Mapper's public scene-footprint vector tile over TKM_S_276 (keyless) and decode it.

The bboxes in scene_tkm.js are axis-aligned; this tile carries the real footprint polygons, so the map can
draw each pass's actual swath outline. One GET (z10 tile containing the facility). Pure-python MVT decoder.
Writes cm_scene_footprints_tkm.geojson (only scenes whose footprint contains the facility).
Licence: Carbon Mapper Data Terms of Use (non-commercial); attribution "Data by Carbon Mapper®".
Run: python3.12 -I fetch_carbonmapper_footprints.py
"""
import gzip
import json
import math
import os
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(ROOT, "data", "derived", "story")   # samples the story page is built from (scripts/build_story_data.py)
LAT, LON = 39.48457, 53.72005
Z = 10
ORIGIN = "http://127.0.0.1:4180"
UA = "MethaneClause-story/1.0"


def tile_xy(lat, lon, z):
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


# ---- minimal protobuf / MVT decoding ----
def varint(b, i):
    r = s = 0
    while True:
        c = b[i]
        i += 1
        r |= (c & 0x7F) << s
        if c < 0x80:
            return r, i
        s += 7


def fields(b):
    i = 0
    while i < len(b):
        key, i = varint(b, i)
        f, w = key >> 3, key & 7
        if w == 0:
            v, i = varint(b, i)
        elif w == 1:
            v, i = b[i:i + 8], i + 8
        elif w == 2:
            ln, i = varint(b, i)
            v, i = b[i:i + ln], i + ln
        elif w == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError(f"wire type {w}")
        yield f, w, v


def packed(b):
    out, i = [], 0
    while i < len(b):
        v, i = varint(b, i)
        out.append(v)
    return out


def value(b):
    import struct
    for f, w, v in fields(b):
        if f == 1:
            return v.decode()
        if f == 2:
            return struct.unpack("<f", v)[0]
        if f == 3:
            return struct.unpack("<d", v)[0]
        if f in (4, 5):
            return v
        if f == 6:
            return (v >> 1) ^ -(v & 1)
        if f == 7:
            return bool(v)
    return None


def geometry(cmds, extent, tx, ty, z):
    rings, cur, x, y, i = [], [], 0, 0, 0
    n = 2 ** z

    def ll(px, py):
        gx, gy = (tx + px / extent) / n, (ty + py / extent) / n
        return [round(gx * 360 - 180, 6), round(math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * gy)))), 6)]
    while i < len(cmds):
        c = cmds[i]
        i += 1
        cid, cnt = c & 7, c >> 3
        if cid in (1, 2):
            for _ in range(cnt):
                dx, dy = cmds[i], cmds[i + 1]
                i += 2
                x += (dx >> 1) ^ -(dx & 1)
                y += (dy >> 1) ^ -(dy & 1)
                if cid == 1 and cur:
                    rings.append(cur)
                    cur = []
                cur.append(ll(x, y))
        elif cid == 7:
            if cur:
                cur.append(cur[0])
                rings.append(cur)
                cur = []
    if cur:
        rings.append(cur)
    return rings


def point_in_ring(lon, lat, ring):
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:]):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def edge_bearings(ring):
    """Orientation (0-180 deg) and length of real footprint edges; drops tile-clip edges (exactly N-S / E-W)."""
    out = []
    for a, b in zip(ring, ring[1:]):
        kx = 111.32 * math.cos(math.radians((a[1] + b[1]) / 2))
        dx, dy = (b[0] - a[0]) * kx, (b[1] - a[1]) * 110.574
        km, brg = math.hypot(dx, dy), math.degrees(math.atan2(dx, dy)) % 180
        if km > 2 and min(abs(brg - 0), abs(brg - 90), abs(brg - 180)) > 0.5:
            out.append([round(brg, 1), round(km, 1)])
    return out


def main():
    tx, ty = tile_xy(LAT, LON, Z)
    url = f"https://api.carbonmapper.org/api/v1/layers/scenes/public/{Z}/{tx}/{ty}.mvt"
    req = urllib.request.Request(url, headers={"Origin": ORIGIN, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        hdrs = {k: v for k, v in r.headers.items() if k.lower().startswith("access-control")
                or k.lower() in ("content-type", "content-length", "content-encoding")}
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    feats, layers = [], {}
    for f, w, layer in fields(raw):
        if f != 3:
            continue
        name, keys, vals, extent, lf = None, [], [], 4096, []
        for g, _, v in fields(layer):
            if g == 1:
                name = v.decode()
            elif g == 2:
                lf.append(v)
            elif g == 3:
                keys.append(v.decode())
            elif g == 4:
                vals.append(value(v))
            elif g == 5:
                extent = v
        layers[name] = len(lf)
        for fb in lf:
            tags, gtype, cmds = [], 0, []
            for g, _, v in fields(fb):
                if g == 2:
                    tags = packed(v)
                elif g == 3:
                    gtype = v
                elif g == 4:
                    cmds = packed(v)
            props = {keys[tags[k]]: vals[tags[k + 1]] for k in range(0, len(tags), 2)}
            props["@layer"] = name
            rings = geometry(cmds, extent, tx, ty, Z)
            if gtype == 3 and rings:
                geom = {"type": "Polygon", "coordinates": rings}
                contains = any(point_in_ring(LON, LAT, rg) for rg in rings)
            elif gtype == 2:
                geom = {"type": "MultiLineString", "coordinates": rings}
                contains = False
            else:
                geom = {"type": "MultiPoint", "coordinates": [p for rg in rings for p in rg]}
                contains = False
            props["@contains_facility"] = contains
            feats.append({"type": "Feature", "properties": props, "geometry": geom})
    keep = [f for f in feats if f["properties"]["@contains_facility"]]
    for f in keep:
        f["properties"]["@edge_bearings_deg_km"] = edge_bearings(f["geometry"]["coordinates"][0])
    doc = {"type": "FeatureCollection", "attribution": "Data by Carbon Mapper®",
           "source": url, "response_headers": hdrs, "layers": layers, "n_features_in_tile": len(feats),
           "note": ("Polygons are clipped to the z10 tile (plus buffer); only footprints containing the facility kept. "
                    "@edge_bearings_deg_km = orientation of real (non-clip) footprint edges, a clue to pass direction: "
                    "ISS at this latitude flies 53.5 deg (ascending) or 126.5 deg (descending); Tanager-1 ~9-12 deg "
                    "(descending, 189 deg) when scanning along track."),
           "features": keep}
    with open(os.path.join(HERE, "cm_scene_footprints_tkm.geojson"), "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))
    print(url, hdrs, layers, len(feats), "containing facility:", len(keep))
    if feats:
        print("property keys:", sorted(feats[0]["properties"].keys()))
    for f in keep:
        p = f["properties"]
        print({k: p[k] for k in p if k in ("name", "timestamp", "instrument", "platform", "id")}, len(f["geometry"]["coordinates"][0]), "verts")


if __name__ == "__main__":
    main()
