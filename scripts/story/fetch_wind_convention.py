"""Decide Carbon Mapper's wind_direction_avg_auto convention (FROM or TO) from the plume rasters themselves.

A plume extends DOWNWIND of its origin. For each plume: download its plume.png (the map overlay; georeferenced by
plume_bounds, north-up) and, for the lead plume, plume.tif (GeoTIFF, to check the PNG's georeferencing). Take the
plume pixels (alpha > 0), find the bearing from the origin to the plume-pixel centroid and to the far tail (pixels
in the outer 30 % of distance), and compare with the reported wind direction W:
    downwind ~ W        -> W is the direction the wind blows TO
    downwind ~ W + 180  -> W is the direction the wind blows FROM (meteorological)
Plumes: six fixed ones (lead plume + strong plumes on other passes) plus the EXTRA strongest-wind plumes in a 3 km
box (strong wind = best-defined plume direction).
Requests: 1 (plumes/annotated bbox) + 1 PNG per plume not yet in cm_plume_assets/ + 1 TIF (only if missing).
The small rasters are kept in cm_plume_assets/ as evidence (Carbon Mapper terms: non-commercial, attribution
"Data by Carbon Mapper®").
Writes wind_convention.json.  Run: /opt/homebrew/bin/python3.12 -I fetch_wind_convention.py
"""
import json
import math
import os
import struct
import urllib.parse
import urllib.request
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(ROOT, "data", "derived", "story")   # samples the story page is built from (scripts/build_story_data.py)
ASSETS = os.path.join(ROOT, "out", "story_plume_assets")   # plume rasters are evidence for the wind check; never committed
API = "https://api.carbonmapper.org/api/v1"
UA = "MethaneClause-story/1.0"
PLUMES = [  # lead plume first; others = strong plumes on other passes (different winds) as controls
    "tan20260611t083132c23s4001-AB", "tan20260611t083132c23s4001-CK", "tan20260620t083638c93s4001-AD",
    "tan20250803t075028c55s4001-I", "tan20260410t082753c95s4001-H", "tan20250729t080259c55s4001-T",
]
EXTRA = 8
LAT, LON, BOX_KM = 39.48457, 53.72005, 3.0
ERA5_FROM_0611 = 157  # weather_tkm.json, 2026-06-11 09:00 UTC (08:00 was 144)


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


# ---------- PNG (all 8-bit colour types + low-bit palettes) ----------
def png_alpha(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, idat, pal, trns = 8, b"", None, None
    while i < len(data):
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ, body = data[i + 4:i + 8], data[i + 8:i + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype, _, _, inter = struct.unpack(">IIBBBBB", body)
        elif typ == b"PLTE":
            pal = body
        elif typ == b"tRNS":
            trns = body
        elif typ == b"IDAT":
            idat += body
        i += 12 + ln
    assert inter == 0, "interlaced PNG"
    ch = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[ctype]
    bpp = max(1, ch * depth // 8)
    stride = (w * ch * depth + 7) // 8
    raw, prev, rows = zlib.decompress(idat), bytearray(stride), []
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0
            b, c = prev[x], (prev[x - bpp] if x >= bpp else 0)
            if f == 1:
                line[x] = (line[x] + a) & 255
            elif f == 2:
                line[x] = (line[x] + b) & 255
            elif f == 3:
                line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(line)
        prev = line
    alpha = []
    for line in rows:
        if ctype == 3:
            idx = []
            per = 8 // depth
            for x in range(w):
                byte = line[x // per]
                shift = 8 - depth * (x % per + 1)
                idx.append((byte >> shift) & ((1 << depth) - 1))
            alpha.append([(trns[k] if trns and k < len(trns) else 255) for k in idx])
        elif ctype == 6:
            alpha.append([line[x * 4 + 3] for x in range(w)])
        elif ctype == 4:
            alpha.append([line[x * 2 + 1] for x in range(w)])
        else:  # no alpha channel: treat non-zero as plume
            alpha.append([255 if any(line[x * ch:(x + 1) * ch]) else 0 for x in range(w)])
    return w, h, {"ctype": ctype, "depth": depth}, alpha


# ---------- GeoTIFF tags only (georeferencing check) ----------
def tiff_georef(data):
    end = "<" if data[:2] == b"II" else ">"
    off = struct.unpack(end + "I", data[4:8])[0]
    n = struct.unpack(end + "H", data[off:off + 2])[0]
    sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 7: 1, 11: 4, 12: 8, 16: 8}
    fmt = {1: "B", 2: "c", 3: "H", 4: "I", 5: "II", 7: "B", 11: "f", 12: "d", 16: "Q"}
    tags = {}
    for k in range(n):
        e = off + 2 + 12 * k
        tag, typ, cnt = struct.unpack(end + "HHI", data[e:e + 8])
        sz = sizes.get(typ, 1) * cnt
        vo = e + 8 if sz <= 4 else struct.unpack(end + "I", data[e + 8:e + 12])[0]
        raw = data[vo:vo + sz]
        if typ == 2:
            val = raw.rstrip(b"\x00").decode(errors="replace")
        elif typ in fmt and typ != 5:
            val = list(struct.unpack(end + fmt[typ] * cnt, raw))
        else:
            val = raw.hex()[:64]
        tags[tag] = val
    geokeys = {}
    if 34735 in tags:
        gk = tags[34735]
        for j in range(gk[3]):
            kid, loc, c, v = gk[4 + 4 * j:8 + 4 * j]
            if loc == 0:
                geokeys[kid] = v
    return {"width": tags.get(256, [None])[0], "height": tags.get(257, [None])[0],
            "bits": tags.get(258), "compression": tags.get(259, [None])[0], "sample_format": tags.get(339),
            "pixel_scale": tags.get(33550), "tiepoint": tags.get(33922), "nodata": tags.get(42113),
            "geokeys": {"GTModelType": geokeys.get(1024), "GeographicType(4326=WGS84)": geokeys.get(2048),
                        "ProjectedCSType": geokeys.get(3072)}}


def bearing(lat1, lon1, lat2, lon2):
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    return math.degrees(math.atan2(math.sin(dl) * math.cos(p2),
                                   math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl))) % 360


def angdiff(a, b):
    return abs((a - b + 180) % 360 - 180)


def analyse(rec, png):
    w, h, info, alpha = png_alpha(png)
    x0, y0, x1, y1 = rec["plume_bounds"]  # lon_min, lat_min, lon_max, lat_max
    olon, olat = rec["geometry_json"]["coordinates"][:2]
    kx, ky = 111.320 * math.cos(math.radians(olat)), 110.574
    pts = []
    for j in range(h):
        for i in range(w):
            if alpha[j][i] > 0:
                lon = x0 + (i + 0.5) / w * (x1 - x0)
                lat = y1 - (j + 0.5) / h * (y1 - y0)
                pts.append((lon, lat, alpha[j][i]))
    if not pts:
        return {"pixels": 0}
    clon = sum(p[0] for p in pts) / len(pts)
    clat = sum(p[1] for p in pts) / len(pts)
    dists = sorted(math.hypot((p[0] - olon) * kx, (p[1] - olat) * ky) for p in pts)
    cut = dists[int(0.7 * (len(dists) - 1))]
    tail = [p for p in pts if math.hypot((p[0] - olon) * kx, (p[1] - olat) * ky) >= cut]
    tlon, tlat = sum(p[0] for p in tail) / len(tail), sum(p[1] for p in tail) / len(tail)
    b_c, b_t = bearing(olat, olon, clat, clon), bearing(olat, olon, tlat, tlon)
    W = rec["wind_direction_avg_auto"]
    mask = {(round(p[0], 4), round(p[1], 4)) for p in pts}
    return {"_mask": mask, "png": info | {"w": w, "h": h}, "pixels": len(pts),
            "pixel_m": [round((x1 - x0) / w * kx * 1000, 1), round((y1 - y0) / h * ky * 1000, 1)],
            "origin_to_centroid_km": round(math.hypot((clon - olon) * kx, (clat - olat) * ky), 2),
            "bearing_origin_to_centroid": round(b_c, 1), "bearing_origin_to_tail": round(b_t, 1),
            "max_extent_km": round(dists[-1], 2),
            "wind_dir_reported": round(W, 1), "wind_speed_reported": round(rec["wind_speed_avg_auto"], 2),
            "diff_tail_vs_W": round(angdiff(b_t, W), 1), "diff_tail_vs_W_plus_180": round(angdiff(b_t, W + 180), 1),
            "verdict": "FROM" if angdiff(b_t, W + 180) < angdiff(b_t, W) else "TO"}


def cached(pid, kind, url):
    path = os.path.join(ASSETS, f"{pid}_plume.{kind}")
    if not os.path.exists(path):
        with open(path, "wb") as fh:
            fh.write(get(url))
    with open(path, "rb") as fh:
        return fh.read()


def main():
    os.makedirs(ASSETS, exist_ok=True)
    dlat, dlon = BOX_KM / 110.574, BOX_KM / (111.320 * math.cos(math.radians(LAT)))
    bbox = [round(LON - dlon, 6), round(LAT - dlat, 6), round(LON + dlon, 6), round(LAT + dlat, 6)]
    url = f"{API}/catalog/plumes/annotated?" + urllib.parse.urlencode({"bbox": bbox, "limit": 1000}, doseq=True)
    items = json.loads(get(url))["items"]
    recs = {r["plume_id"]: r for r in items}
    windy = sorted((r for r in items if r["plume_id"] not in PLUMES and r.get("gas") == "CH4" and r.get("plume_png")
                    and r.get("wind_speed_avg_auto") is not None and r.get("wind_direction_avg_auto") is not None),
                   key=lambda r: -r["wind_speed_avg_auto"])[:EXTRA]
    chosen = PLUMES + [r["plume_id"] for r in windy]
    out = {"query": url, "plumes": [], "method": __doc__.split("\n\n")[0]}
    for pid in chosen:
        r = recs.get(pid)
        if not r or not r.get("plume_png"):
            out["plumes"].append({"plume_id": pid, "error": "not returned"})
            continue
        png = cached(pid, "png", r["plume_png"])
        row = {"plume_id": pid, "scene_time": r["scene_timestamp"], "rate_kg_h": round(r["emission_auto"] or 0),
               "origin": r["geometry_json"]["coordinates"][:2], "plume_bounds": r["plume_bounds"]} | analyse(r, png)
        if pid == PLUMES[0]:
            tif = cached(pid, "tif", r["plume_tif"])
            g = tiff_georef(tif)
            row["tif"] = g
            if g["pixel_scale"] and g["tiepoint"]:
                lon0, lat0 = g["tiepoint"][3], g["tiepoint"][4]
                row["tif_bounds_vs_plume_bounds"] = {
                    "tif": [lon0, lat0 - g["pixel_scale"][1] * g["height"], lon0 + g["pixel_scale"][0] * g["width"], lat0],
                    "plume_bounds": r["plume_bounds"]}
        out["plumes"].append(row)
    # Several plumes of one scene can share ONE mask (a merged enhancement with several origins inside it); then the
    # origin->tail test says nothing about wind for that plume. Group masks with Jaccard > 0.9; keep singletons.
    ps = [p for p in out["plumes"] if p.get("_mask")]
    group = {p["plume_id"]: {p["plume_id"]} for p in ps}
    for i, a in enumerate(ps):
        for b in ps[i + 1:]:
            A, B = a["_mask"], b["_mask"]
            if len(A & B) / len(A | B) > 0.9:
                g = group[a["plume_id"]] | group[b["plume_id"]]
                for k in g:
                    group[k] = g
    for p in ps:
        g = sorted(group[p["plume_id"]])
        p["shared_mask_with"] = [k for k in g if k != p["plume_id"]]
        del p["_mask"]
    indep = [p for p in ps if not p["shared_mask_with"]]
    out["summary_independent_masks"] = {
        "plumes": [p["plume_id"] for p in indep], "verdicts": [p["verdict"] for p in indep],
        "n_from": sum(p["verdict"] == "FROM" for p in indep), "n_to": sum(p["verdict"] == "TO" for p in indep),
        "shared_mask_groups": sorted({tuple(sorted(group[p["plume_id"]])) for p in ps if p["shared_mask_with"]})}
    v = [p.get("verdict") for p in out["plumes"] if p.get("verdict")]
    out["summary"] = {"verdicts": v, "n_from": v.count("FROM"), "n_to": v.count("TO")}
    for lo, hi in ((0, 3), (3, 6), (6, 99)):
        sub = [p for p in out["plumes"] if p.get("verdict") and lo <= p["wind_speed_reported"] < hi]
        out["summary"][f"wind_{lo}_{hi}_ms"] = {"n": len(sub), "from": sum(p["verdict"] == "FROM" for p in sub),
                                                "median_diff_vs_W_plus_180": sorted(p["diff_tail_vs_W_plus_180"] for p in sub)[len(sub) // 2] if sub else None,
                                                "median_diff_vs_W": sorted(p["diff_tail_vs_W"] for p in sub)[len(sub) // 2] if sub else None}
    lead = out["plumes"][0]
    if lead.get("bearing_origin_to_tail") is not None:
        out["lead_vs_era5"] = {"era5_from_deg": ERA5_FROM_0611, "era5_blows_to_deg": (ERA5_FROM_0611 + 180) % 360,
                               "plume_tail_bearing": lead["bearing_origin_to_tail"],
                               "diff_era5_to_vs_tail": round(angdiff((ERA5_FROM_0611 + 180) % 360, lead["bearing_origin_to_tail"]), 1),
                               "diff_cm_vs_era5_from": round(angdiff(lead["wind_dir_reported"], ERA5_FROM_0611), 1)}
    out["attribution"] = "Data by Carbon Mapper®"
    with open(os.path.join(HERE, "wind_convention.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
