"""Re-fetch ERA5 hourly weather at TKM_S_276 for each scene time (Open-Meteo archive API).

Four GET requests (one per date span). Writes weather_tkm.json with, per scene,
the nearest-hour row plus the two bracketing hours, and the CORS headers returned
for a non-localhost Origin.
Weather data by Open-Meteo.com (CC BY 4.0), based on Copernicus ERA5 (C3S).
Run: python3.12 -I fetch_weather.py
"""
import datetime as dt
import json
import os
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HERE = os.path.join(ROOT, "data", "derived", "story")   # samples the story page is built from (scripts/build_story_data.py)
LAT, LON = 39.48457, 53.72005
ORIGIN = "http://127.0.0.1:4180"
VARS = ["wind_speed_10m", "wind_direction_10m", "temperature_2m", "relative_humidity_2m", "cloud_cover"]
SCENES = [
    ("2023-06-20T08:44:14Z", "emi20230620t084414", "EMIT (ISS)"),
    ("2025-06-13T10:01:54Z", "MARS 2025-06-13", "EMIT (ISS), MARS plume"),
    ("2026-06-11T08:31:32Z", "tan20260611t083132", "Tanager-1, lead plume AB"),
    ("2026-06-20T08:36:38Z", "tan20260620t083638", "Tanager-1"),
    ("2026-08-10T08:35:43Z", "tan20260810t083543", "Tanager-1"),
]
SPANS = [("2023-06-20", "2023-06-20"), ("2025-06-13", "2025-06-13"),
         ("2026-06-11", "2026-06-20"), ("2026-08-10", "2026-08-10")]


def get(start, end):
    q = urllib.parse.urlencode({
        "latitude": LAT, "longitude": LON, "start_date": start, "end_date": end,
        "hourly": ",".join(VARS), "models": "era5", "timezone": "GMT",
        "wind_speed_unit": "ms"})
    url = "https://archive-api.open-meteo.com/v1/archive?" + q
    req = urllib.request.Request(url, headers={"Origin": ORIGIN, "User-Agent": "MethaneClause-story/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        hdrs = {k: v for k, v in r.headers.items() if k.lower().startswith("access-control")}
        return url, hdrs, json.load(r)


def main():
    hourly = {}
    meta = {}
    for start, end in SPANS:
        url, hdrs, d = get(start, end)
        meta[f"{start}..{end}"] = {"url": url, "cors_headers": hdrs, "grid_lat": d.get("latitude"),
                                   "grid_lon": d.get("longitude"), "elevation_m": d.get("elevation"),
                                   "units": d.get("hourly_units")}
        h = d["hourly"]
        for i, t in enumerate(h["time"]):
            hourly[t] = {v: h[v][i] for v in VARS}
    out = []
    for iso, scene, what in SCENES:
        t = dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        lo = t.replace(minute=0, second=0)
        hi = lo + dt.timedelta(hours=1)
        near = lo if (t - lo) <= (hi - t) else hi
        key = lambda x: x.strftime("%Y-%m-%dT%H:%M")  # noqa: E731
        out.append({"scene_time_utc": iso, "scene": scene, "what": what,
                    "nearest_hour_utc": key(near), "nearest": hourly.get(key(near)),
                    "bracket": {key(lo): hourly.get(key(lo)), key(hi): hourly.get(key(hi))}})
    doc = {
        "source": "Open-Meteo Historical Weather API (archive-api.open-meteo.com), models=era5",
        "licence": "CC BY 4.0 — 'Weather data by Open-Meteo.com' (https://open-meteo.com/en/license); "
                   "ERA5: Copernicus Climate Change Service (C3S), Hersbach et al. 2018",
        "point": {"lat": LAT, "lon": LON},
        "note": "wind_direction_10m is the direction the wind blows FROM (meteorological convention). "
                "ERA5 grid is ~0.25 deg (~25 km); values are for the grid cell, not the facility.",
        "requests": meta,
        "scenes": out,
    }
    with open(os.path.join(HERE, "weather_tkm.json"), "w") as fh:
        json.dump(doc, fh, indent=1)
    print(json.dumps(doc, indent=1)[:5000])


if __name__ == "__main__":
    main()
