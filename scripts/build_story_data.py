"""Build app/data/story.json for the story page (app/story.html) from committed data only; no network.

Run from the repo root:  python3 -I scripts/build_story_data.py        (--check: exit 1 if the file is out of date)

Inputs
  app/data/app.json                       cases, ledger (DZA_S_012 refund side), replay, policy, capReasons
  data/derived/cases/TKM_S_276*.json      plumes, scenes, the facility-radius count (post_mars_last_plume)
  data/derived/cm_timelines.json          the nearest source's own observation list (timeline ticks)
  data/derived/stake_band.json            methane at stake (stake, not abatement)
  data/derived/testnet_run.json           the Sepolia run; block times from data/derived/sepolia_txs.json
  data/derived/verification_time.json     the manual check the "Verify yourself" view times
  pipeline/readme_testnet.py              the tamper case's edited rate (the label the README table uses)
  data/derived/story/*                    OpenStreetMap context, ERA5 weather, Carbon Mapper scene footprints,
                                          Carbon Mapper plume winds (direction convention checked against the plume
                                          masks), an orbit estimate per pass; re-fetch with scripts/story/fetch_*.py
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "app" / "data" / "story.json"
ST = ROOT / "data" / "derived" / "story"


def load(rel):
    return json.loads((ROOT / rel).read_text())


def build() -> dict:
    app = load("app/data/app.json")
    case = app["cases"]["TKM_S_276"]
    cp = load("data/derived/cases/TKM_S_276.clean_passes.json")
    cases_tkm = load("data/derived/cases/TKM_S_276.json")
    stake = load("data/derived/stake_band.json")
    timelines = load("data/derived/cm_timelines.json")
    tn = load("data/derived/testnet_run.json")
    vt = load("data/derived/verification_time.json")
    txs = {t["tx"]: t for t in load("data/derived/sepolia_txs.json")["transactions"]}

    near = min(cases_tkm["cm_sources"], key=lambda k: cases_tkm["cm_sources"][k]["dist_km"])
    m = re.search(r'"reject_tamper": "[^"]*?([\d,]+) → ([\d,]+) kg/h', (ROOT / "pipeline/readme_testnet.py").read_text())
    tamper = {"from": int(m.group(1).replace(",", "")), "to": int(m.group(2).replace(",", ""))}

    def with_time(st):
        h = st["tx"].split("/tx/")[-1]
        return {**st, **({"blockTime": txs[h]["timestamp"]} if h in txs else {})}

    L = app["ledger"]["DZA_S_012"]
    rep = app["replay"]["variants"]["A"]
    dza = {
        "source": L["source"], "country": L["country"], "sourceType": L["sourceType"], "mars": L["mars"],
        "leak": {k: L["leak"][k] for k in ("sceneAt", "rateKgH", "uncKgH", "instrument", "lat", "lon", "plumeId")},
        "relapse": {k: L["relapse"][k] for k in ("sceneAt", "rateKgH", "uncKgH", "instrument", "lat", "lon", "plumeId")},
        "clean": {**{k: L["clean"][k] for k in ("installments", "refusals", "n", "floorKgH", "maxCloudPct", "since",
                                                 "statusLabel", "lowerBound", "candidates", "covenantLabel")},
                  "scenes": [{"t": sc["sceneAt"], "inst": sc["instrument"], "cloud": sc["cloudCoverPct"], "qualified": sc["qualified"],
                              "refusal": sc["refusal"], "afterRelapse": sc["afterRelapse"]} for sc in L["clean"]["scenes"]]},
        "replayA": {"label": rep["label"], "outcome": rep["outcome"], "chain": "Anvil (local)", "checks": app["replay"]["checks"],
                    "sponsor": {k: rep["sponsor"][k] for k in ("openedByBuyerWei", "releasedWei", "forfeitedWei", "observerFeesWei")}},
        "plumes": [{"t": q["scene_timestamp"][:10], "rate": None if q["emission_auto"] is None else round(q["emission_auto"]),
                    "lat": q["lat"], "lon": q["lon"]} for q in app["cases"]["DZA_S_012"]["cmPlumes"]],
        "sandbox": [with_time(st) for st in app["sepolia"]["run"]["steps"] if st.get("source") == "judge cure arc"
                    and any(w in st["step"] for w in ("DZA", "relapse", "pool", "claim", "clean pass"))],
        "capSentence": app["capReasons"]["catalogue"]["sentence"],
    }

    scene = {
        "case": "TKM_S_276",
        "facility": {**cp["facility"], "sector": case["mars"]["sector"], "sourceType": case["mars"]["source_type"]},
        "mars": {"notified": case["mars"]["notified"], "operatorFeedback": case["mars"]["feedback_operator"],
                 "persistency": case["mars"]["persistency_category"], "lastPlume": case["mars"]["last_plume_date"][:10],
                 "plumes": [{"date": p["tile_date"][:10], "rate": p["ch4_fluxrate"], "sat": p["satellite"]} for p in case["marsPlumes"]]},
        "cmPlumes": sorted([{"id": p["plume_id"], "date": p["scene_timestamp"][:10],
                             "rate": None if p["emission_auto"] is None else round(p["emission_auto"]),
                             "unc": None if p["emission_uncertainty_auto"] is None else round(p["emission_uncertainty_auto"]),
                             "inst": p["instrument"], "lat": p["lat"], "lon": p["lon"], "distKm": p["origin_dist_km"]}
                            for p in case["cmPlumes"]], key=lambda p: p["date"]),
        "scenes": [{"date": s["sceneAtIso"][:10], "time": s["sceneAtIso"][11:16], "providerId": s["sceneProviderId"],
                    "cloudPct": s["cloudCoverPct"], "cloudAssessed": s.get("cloudCoverPctAssessed"), "gsd": round(s["gsd"], 1),
                    "offNadir": round(s["offNadir"], 1), "refusal": s["refusal"]} for s in cp["scenes"]],
        "episode": cp["episode"],
        "headline": cases_tkm["post_mars_last_plume"],
        "nearSource": {"name": near, "distKm": cases_tkm["cm_sources"][near]["dist_km"],
                       "postMars": cases_tkm["cm_sources"][near]["post_mars_last_plume"],
                       "contentSha256": cases_tkm["cm_sources"][near]["content_sha256"],
                       "obs": timelines[near]["obs"], "det": timelines[near]["det"]},
        "stake": {k: stake[k] for k in ("label", "kt_ch4_per_yr", "kt_co2e_per_yr_gwp100", "gwp100", "detected", "passes", "since")},
        "policy": app["policy"],
        "dza": dza,
        "tamper": tamper,
        "verify": {"schema": vt["schema"], "label": vt["label"], "question": vt["question"], "steps": vt["manual"]["steps"],
                   "manualRuns": len(vt["manual"]["runs"]), "toolMedianSeconds": vt["tool"]["medianSeconds"], "toolMode": vt["tool"]["mode"]},
        "sepolia": {"covenant": tn["methaneCovenant"], "createdAt": tn["createdAt"], "evidenceHash": tn["evidenceHash"],
                    "steps": [with_time(st) for st in tn["steps"]]},
    }

    # context layers, from data/derived/story/
    plume_dates = {p["date"] for p in scene["cmPlumes"]}

    def simplify(coords, n=24):
        step = max(1, len(coords) // n)
        out = coords[::step]
        return out if out[-1] == coords[-1] else out + [coords[-1]]

    fp = json.loads((ST / "cm_scene_footprints_tkm.geojson").read_text())
    passes = []
    for f in sorted(fp["features"], key=lambda f: f["properties"]["timestamp"]):
        p = f["properties"]
        ring = f["geometry"]["coordinates"][0] if f["geometry"]["type"] == "Polygon" else f["geometry"]["coordinates"][0][0]
        passes.append({"date": p["timestamp"][:10], "time": p["timestamp"][11:16], "inst": p["instrument"], "uuid": p.get("scene_uuid"),
                       "cloudPct": p.get("cloud_cover_pct"), "sunElev": None if p.get("sun_elevation") is None else round(p["sun_elevation"]),
                       "plumeInRing": p["timestamp"][:10] in plume_dates,
                       "footprint": [[round(x, 4), round(y, 4)] for x, y in simplify(ring)]})
    wx = json.loads((ST / "weather_tkm.json").read_text())
    osm = json.loads((ST / "osm_tkm.geojson").read_text())
    keep = []
    for f in osm["features"]:
        if f["properties"].get("@class") in ("pipeline", "road", "settlement", "industrial"):
            g = f["geometry"]
            if g["type"] == "LineString":
                g = {"type": "LineString", "coordinates": [[round(x, 5), round(y, 5)] for x, y in g["coordinates"]]}
            keep.append({"type": "Feature", "geometry": g, "properties": {k: v for k, v in f["properties"].items() if k in (
                "@class", "@dist_km", "name", "ref", "highway", "substance", "network", "location", "man_made", "place")}})
    wind = {}
    for pl in json.loads((ST / "wind_convention.json").read_text())["plumes"]:
        d = pl["scene_time"][:10]
        if pl.get("wind_dir_reported") is not None and (d not in wind or (pl.get("rate_kg_h") or 0) > (wind[d]["rate"] or 0)):
            wind[d] = {"dir": round(pl["wind_dir_reported"], 1), "speed": round(pl["wind_speed_reported"], 2), "rate": pl.get("rate_kg_h")}
    orb = json.loads((ST / "orbits_tkm.json").read_text())
    extras = {
        "cmWindByDate": wind,
        "satgeo": {e["time_utc"][:10]: {"distKm": e["A_ground_distance_facility_to_subsat_km"], "altKm": e["alt_km_assumed"]}
                   for e in orb["tanager_pass_estimates"]},
        "passes": passes,
        "weather": [{"t": s["scene_time_utc"], **s["nearest"]} for s in wx["scenes"]],
        "osm": {"type": "FeatureCollection", "features": keep},
    }
    return {"schema": "methaneclause.story/1", "scene": scene, "extras": extras}


def main(argv):
    text = json.dumps(build(), indent=1, sort_keys=True) + "\n"   # one value per line: hashes never share a line with a key-like word
    if "--check" in argv:
        ok = OUT.is_file() and OUT.read_text() == text
        print("story.json up to date" if ok else "story.json is out of date: run python3 -I scripts/build_story_data.py")
        sys.exit(0 if ok else 1)
    OUT.write_text(text)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text):,} bytes)")


if __name__ == "__main__":
    main(sys.argv[1:])
