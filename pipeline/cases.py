"""Rebuild the demo case files LIVE from Carbon Mapper + the MARS snapshot (spec §4 "Real demo cases").

Writes data/derived/cases/{MARS_SOURCE}.json and data/derived/cases/LEAD.json.
The lead case (TKM_S_276) must still show detections after MARS's last plume, including 2026-06-11 at ~2,548 kg/h.
If it does not reproduce, the first backup lead (TKM_S_342, TKM_S_213, TKM_S_395) that is a join_v1 candidate
with a post-'absent' detection becomes the lead, and LEAD.json says so.

Headline count (`post_mars_last_plume`), the facility-radius basis: passes and detections after MARS's last plume,
counted only from linked Carbon Mapper sources whose centre is within RADIUS_KM of the MARS point, and only on dates
in those sources' own observation lists. A neighbouring source further out can see a plume the facility's own
sources did not observe (TKM_S_276 on 2026-04-17: the only detection came from a source 1.774 km away, plumes
2.8-3.2 km out). The union over every linked source stays in the file as `post_mars_last_plume_all_linked`, labelled
secondary, so nothing is hidden. Each source's post-label date lists are kept, so the count is recomputable from the
committed file (tests/test_cases_radius.py).

OFFLINE=1 reads the cached response (data/raw/cm/live/ first: the bytes of the last live fetch, then the older
data/raw/cm/source/ cache); when the cached bytes are the ones a committed case file recorded, that record's
fetchedAt is kept, because it is still when those bytes were fetched.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd

from canon import evidence_hash
from common import (CASE_CM_SOURCES, CM_LICENCE, DERIVED, RADIUS_KM, RAW, haversine_km, http_get, load_manifest,
                    now_utc, save_manifest, sha256_bytes, write_json, content_sha256)
from fetch_cm import PLUME_FIELDS, SRC_URL
from merkle import hx, source_id

CASES = DERIVED / "cases"
LIVE = RAW / "cm" / "live"
LEAD, LEAD_DATE, LEAD_RATE = "TKM_S_276", "2026-06-11", 2548.0
BACKUPS = ["TKM_S_342", "TKM_S_213", "TKM_S_395"]


ALL_LINKED_LABEL = ("secondary, not the headline: union over every linked Carbon Mapper source, including centres "
                    f"beyond {RADIUS_KM:g} km of the MARS point")
BASIS = (f"facility radius: linked Carbon Mapper sources whose centre is within {RADIUS_KM:g} km of the MARS point; a "
         "pass or detection counts only on a date in those sources' own observation lists")


def _recorded_fetch(name: str, raw_sha256: str) -> str | None:
    """fetchedAt of a committed case file that recorded exactly these bytes for this source, else None."""
    for f in sorted(CASES.glob("*_S_*.json")):
        if f.name.endswith(".clean_passes.json"):
            continue
        rec = json.loads(f.read_text()).get("cm_sources", {}).get(name)
        if rec and rec.get("raw_sha256") == raw_sha256:
            return rec.get("fetchedAt")
    return None


def fetch_live(name: str) -> tuple[dict, tuple[str, str], str]:
    if os.environ.get("OFFLINE") == "1":  # reproduce from cache without network
        live = RAW / "cm" / "live" / f"{name}.json"
        b = (live if live.exists() else RAW / "cm" / "source" / f"{name}.json").read_bytes()
        t = _recorded_fetch(name, sha256_bytes(b)) or "offline-cache"
    else:
        b = http_get(SRC_URL + name)
        LIVE.mkdir(parents=True, exist_ok=True)
        (LIVE / f"{name}.json").write_bytes(b)
        t = now_utc()
    return json.loads(b), (sha256_bytes(b), content_sha256(b)), t


def facility_post(sources: dict[str, dict], last: str, radius_km: float = RADIUS_KM) -> dict:
    """Passes and detections after `last` over the sources whose centre is within `radius_km` of the facility.
    `sources` maps a Carbon Mapper source name to {"dist_km", "obs": [dates], "det": [dates]}. A date counts as a pass
    if it is on a near source's own observation list, and as a detection only if that same source both observed it
    and lists it as a detection date."""
    near = sorted(n for n, v in sources.items() if v["dist_km"] <= radius_km)
    passes, det = set(), set()
    for n in near:
        obs = {o for o in sources[n]["obs"] if o > last}
        passes |= obs
        det |= obs & set(sources[n]["det"])
    return {"since": last, "basis": BASIS, "radius_km": radius_km, "cm_sources": near,
            "n_cm_passes": len(passes), "n_cm_detected": len(det), "detections": sorted(det)}


def build_case(mars_name: str, mars: pd.DataFrame, mars_plumes: pd.DataFrame, join: dict, man: dict) -> dict:
    m = mars[mars["source_name"] == mars_name].iloc[0].to_dict()
    last = str(m["last_plume_date"])[:10]
    cand = next((c for c in join["candidates"] if c["mars_source"] == mars_name), None)
    # spec-named Carbon Mapper sources + the ones join_v1 attributed to this MARS source
    names = sorted(set(CASE_CM_SOURCES.get(mars_name, [])) | set(cand["cm_sources"] if cand else []))

    cm_out, plumes, obs, det, per = {}, {}, {}, set(), {}
    for n in names:
        raw, h, t = fetch_live(n)
        man.setdefault("cm_case_live_responses", {"url_prefix": SRC_URL, "licence": CM_LICENCE, "sha256": {}})
        man["cm_case_live_responses"]["sha256"][n] = h[0]
        man["cm_case_live_responses"].setdefault("content_sha256", {})[n] = h[1]
        man["cm_case_live_responses"]["fetchedAt"] = t
        inst = {}
        for sc in raw.get("scenes") or []:
            inst.setdefault(sc["timestamp"][:10], set()).add(sc.get("instrument") or "unk")
        lon, lat = raw["point"]["coordinates"]
        o_all, d_all = raw.get("observation_dates") or [], raw.get("detection_dates") or []
        dist = round(float(haversine_km(m["lat"], m["lon"], lat, lon)), 3)
        cm_out[n] = {"lat": lat, "lon": lon, "dist_km": dist, "within_radius": dist <= RADIUS_KM,
                     "raw_sha256": h[0], "content_sha256": h[1], "fetchedAt": t,
                     "n_obs": len(o_all), "n_det": len(d_all),
                     "post_mars_last_plume": {"n_passes": sum(o > last for o in o_all),
                                              "n_detected": sum(o > last for o in d_all),
                                              "passes": sorted(o for o in o_all if o > last),
                                              "detections": sorted(o for o in d_all if o > last)}}
        per[n] = {"dist_km": dist, "obs": o_all, "det": d_all}
        for o in raw.get("observation_dates") or []:
            obs.setdefault(o, set()).update(inst.get(o, {"unk"}))
        det |= set(raw.get("detection_dates") or [])
        for p in raw.get("plumes") or []:
            c = p["geometry_json"]["coordinates"]
            row = {k: p.get(k) for k in PLUME_FIELDS}
            row.update(source_name=n, lon=c[0], lat=c[1],
                       origin_dist_km=round(float(haversine_km(m["lat"], m["lon"], c[1], c[0])), 3))
            plumes.setdefault(p["plume_id"], row)  # twin dedup by plume_id
    pl = sorted(plumes.values(), key=lambda r: (r["scene_timestamp"], r["plume_id"]))
    pl = [r for r in pl if r["origin_dist_km"] <= RADIUS_KM]
    timeline = [{"date": o, "instruments": "+".join(sorted(i)), "detected": o in det} for o, i in sorted(obs.items())]
    post = [x for x in timeline if x["date"] > last]
    mp = mars_plumes[mars_plumes["source_name"] == mars_name]
    case = {
        "mars_source": {k: (None if pd.isna(v) else v) for k, v in m.items()},
        "mars_plumes": [{k: (None if pd.isna(v) else v) for k, v in r.items()}
                        for r in mp[["id_plume", "satellite", "tile_date", "ch4_fluxrate", "ch4_fluxrate_std",
                                     "detection_institution"]].to_dict("records")],
        "cm_sources": cm_out,
        "cm_timeline_union": timeline,
        "cm_plumes_within_radius": pl,
        "post_mars_last_plume": facility_post(per, last),
        "post_mars_last_plume_all_linked": {"label": ALL_LINKED_LABEL, "since": last, "cm_sources": names,
                                            "n_cm_passes": len(post),
                                            "n_cm_detected": sum(x["detected"] for x in post),
                                            "detections": [x["date"] for x in post if x["detected"]]},
        "join_v1_candidate": cand is not None,
        "sourceId": hx(source_id(mars_name)),  # keccak256(source_name), as the contracts expect
        "cm_source_ids": {n: hx(source_id(n)) for n in names},
        "mars_snapshot_sha256": man["files"]["mars_zip"]["sha256"],
        "licence": [CM_LICENCE + " — Data by Carbon Mapper", "UNEP IMEO MARS, CC BY-NC-SA 4.0"],
    }
    hashed = json.loads(json.dumps(case, default=str))
    for v in hashed["cm_sources"].values():
        v.pop("fetchedAt")  # hash covers content, not when we fetched it or the
        v.pop("raw_sha256")  # raw bytes (those embed expiring signed URLs)
    case["case_evidence_hash"] = evidence_hash(hashed)
    return case


def lead_ok(case: dict) -> tuple[bool, float | None]:
    """The lead reproduces if a post-'absent' plume on LEAD_DATE has a rate within 5% of LEAD_RATE."""
    rates = [p["emission_auto"] for p in case["cm_plumes_within_radius"]
             if p["scene_timestamp"][:10] == LEAD_DATE and p["emission_auto"] is not None]
    match = min(rates, key=lambda r: abs(r - LEAD_RATE)) if rates else None
    ok = (case["post_mars_last_plume"]["n_cm_detected"] > 0 and match is not None
          and abs(match - LEAD_RATE) / LEAD_RATE < 0.05)
    return ok, match


def main() -> None:
    man = load_manifest()
    mars = pd.read_csv(DERIVED / "mars_sources.csv")
    mars_plumes = pd.read_csv(DERIVED / "mars_plumes_og.csv")
    join = json.loads((DERIVED / "join_v1.json").read_text())

    out = {}
    for name in CASE_CM_SOURCES:
        out[name] = build_case(name, mars, mars_plumes, join, man)
    ok, peak = lead_ok(out[LEAD])
    lead = {"lead": LEAD, "reproduced": ok, "check": f"detection on {LEAD_DATE} at ~{LEAD_RATE:.0f} kg/h after MARS last plume",
            "matched_rate_kgh_on_date": None if peak is None else round(peak, 1), "checkedAt": now_utc()}
    if not ok:
        for b in BACKUPS:
            if b in {c["mars_source"] for c in join["candidates"]}:
                out[b] = build_case(b, mars, mars_plumes, join, man)
                if out[b]["post_mars_last_plume"]["n_cm_detected"] > 0:
                    lead.update(lead=b, fallback_from=LEAD)
                    break
    save_manifest(man)
    for name, case in out.items():
        write_json(CASES / f"{name}.json", case)
        pm = case["post_mars_last_plume"]
        al = case["post_mars_last_plume_all_linked"]
        print(f"{name}: MARS last plume {pm['since']}, CM after (sources <= {RADIUS_KM} km): {pm['n_cm_detected']}/"
              f"{pm['n_cm_passes']} passes detected [all linked, secondary: {al['n_cm_detected']}/{al['n_cm_passes']}], "
              f"{len(case['cm_plumes_within_radius'])} plumes <= {RADIUS_KM} km, candidate={case['join_v1_candidate']}")
    write_json(CASES / "LEAD.json", lead)
    print("LEAD:", lead)


if __name__ == "__main__":
    main()
