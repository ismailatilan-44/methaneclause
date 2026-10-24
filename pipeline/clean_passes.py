"""Every candidate clean pass at the three case facilities, with every check of spec-v2 §2 rules 1-6 and its refusal.

    cd pipeline && .venv/bin/python clean_passes.py     # writes data/derived/cases/<case>.clean_passes.json

Per case: the episode the claim would rest on (rule in CASES), `since = detectedAt + linkWindow` (30 d), the covenant
term, then every Carbon Mapper scene whose footprint contains a plume-origin point of the facility's linked sources
(server-side `intersects` with the MultiPoint of origins), from the episode date to the snapshot end. Each scene is run
through `cleanpass_rules.qualify` at `evaluatedAt` = the scene query's fetch time. Raw responses stay in data/raw/
(see scenes.py); only derived fields and response hashes are written. Network: cached, three facilities only.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import policy as pol
import scenes
from cleanpass_rules import attestable_from, origin_intersect_known, qualify
from cleanruns import u_bp
from common import (CM_LICENCE, DERIVED, MARS_LICENCE, RADIUS_KM, ROOT, haversine_km, sha256_file, strip_signed_urls,
                    write_json)

SNAPSHOT_END = "2026-10-16T00:00:00Z"
PER_POINT = {"DZA_S_012"}  # per-origin-point scene queries (answers spec-v2 §13 Q1 point by point)
CASES = {
    "DZA_S_012": {"covenant": "DZA replay covenant, EU importer [SIM] (Anvil)", "policy": "POLICY_V2_REPLAY",
                  "episodeRule": "first Carbon Mapper plume >= minRateKgH within the facility radius at or after the "
                                 "replay covenant start"},
    "TKM_S_276": {"covenant": "main covenant, importer [SIM] (Sepolia; core unchanged)", "policy": "POLICY_V2",
                  "episodeRule": "the main covenant's demo episode (pipeline/observer/demo.py PLUME)"},
    "USA_S_077": {"covenant": None, "policy": "POLICY_V2",
                  "episodeRule": "hypothetical claim [SIM]: no covenant holds an episode at this facility; the latest "
                                 "Carbon Mapper plume >= minRateKgH within the facility radius; no covenant term"},
}


def ts(s: str) -> int:
    s = s if (s.endswith("Z") or "+" in s[10:]) else s + "Z"
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def iso(t: int | None) -> str | None:
    return None if t is None else datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def record_sha256(item: dict) -> str:
    """SHA-256 of the RFC 8785 form of one provider record without expiring signed URLs (spec-v2 §4 sceneRecordSha256)."""
    import hashlib

    import rfc8785

    return hashlib.sha256(rfc8785.dumps(strip_signed_urls(item))).hexdigest()


def keccak_text(s: str) -> str:
    from eth_utils import keccak

    return "0x" + keccak(text=s).hex()


def _episode(case_name: str, case: dict, policy: dict) -> dict:
    min_rate = policy["core"]["minRateKgH"]
    rows = sorted((p for p in case["cm_plumes_within_radius"] if (p["emission_auto"] or 0) >= min_rate),
                  key=lambda p: (p["scene_timestamp"], p["plume_id"]))
    if case_name == "DZA_S_012":
        start = policy["core"]["start"]
        p = next(p for p in rows if ts(p["scene_timestamp"]) >= start)
    elif case_name == "TKM_S_276":
        from observer.demo import PLUME

        p = next(p for p in rows if p["plume_id"] == PLUME)
    else:
        p = rows[-1]
    return {"plumeId": p["plume_id"], "detectedAt": ts(p["scene_timestamp"]), "detectedAtIso": p["scene_timestamp"],
            "rateKgH": int(p["emission_auto"]), "instrument": p["instrument"], "publishedAt": p["published_at"],
            "rule": CASES[case_name]["episodeRule"]}


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def build(case_name: str) -> dict:
    cfg = CASES[case_name]
    policy = getattr(pol, cfg["policy"])
    core, pool = policy["core"], policy["pool"]
    case_path = DERIVED / "cases" / f"{case_name}.json"
    case = json.loads(case_path.read_text())
    fac = case["mars_source"]
    ep = _episode(case_name, case, policy)
    since = ep["detectedAt"] + core["linkWindow"]
    cov_end = core["start"] + core["term"] if cfg["covenant"] else None
    linked = sorted(case["cm_source_ids"])
    origins = {p["plume_id"]: (p["lon"], p["lat"]) for p in case["cm_plumes_within_radius"]}
    geom = scenes.multipoint(list(origins.values()))
    dt_from = ep["detectedAtIso"][:10] + "T00:00:00Z"
    items, qmeta = scenes.scenes_intersecting(case_name, geom, dt_from, SNAPSHOT_END)
    pts_sorted = [tuple(x) for x in json.loads(geom)["coordinates"]]
    pub_by_pt: dict[tuple, int] = {}
    for p in case["cm_plumes_within_radius"]:
        k = (round(p["lon"], 6), round(p["lat"], 6))
        pub_by_pt[k] = min(pub_by_pt.get(k, 1 << 62), ts(p["published_at"]))
    point_published = [pub_by_pt[k] for k in pts_sorted]
    ep_plume = next(p for p in case["cm_plumes_within_radius"] if p["plume_id"] == ep["plumeId"])
    ep_point = pts_sorted.index((round(ep_plume["lon"], 6), round(ep_plume["lat"], 6)))
    now = ts(qmeta["fetchedAt"])

    inside: dict[str, list[int]] | None = None
    point_queries = []
    if case_name in PER_POINT:
        inside = {}
        pts = json.loads(geom)["coordinates"]
        for i, (lon, lat) in enumerate(pts):
            g = json.dumps({"type": "Point", "coordinates": [lon, lat]}, separators=(",", ":"))
            its, m = scenes.scenes_intersecting(f"{case_name}_pt{i:02d}", g, dt_from, SNAPSHOT_END)
            point_queries.append({"point": [lon, lat], "contentSha256": m["contentSha256"],
                                  "scenes": len(its)})
            for it in its:
                inside.setdefault(it["id"], []).append(i)

    src = {}
    for n in linked:
        d, m = scenes.source(n)
        src[n] = {"plumes": d.get("plumes") or [], "url": m["url"], "contentSha256": m["contentSha256"],
                  "fetchedAt": m["fetchedAt"]}
    mars = [{"t": ts(r["tile_date"]), "id_plume": r["id_plume"], "tile_date": r["tile_date"],
             "satellite": r["satellite"], "ch4_fluxrate": r["ch4_fluxrate"]} for r in case["mars_plumes"]]

    ctx = {"since": since, "now": now, "publicationLag": pool["publicationLag"], "covenantEnd": cov_end,
           "floors": pool["instrumentFloorKgH"], "rateKgH": ep["rateKgH"], "marsWindowDays": pool["marsWindowDays"],
           "maxCloudBp": pool["maxCloudBp"], "seen": set()}
    out_scenes = []
    for it in sorted(items, key=lambda x: (x["timestamp"], x["id"])):
        t = ts(it["timestamp"])
        pub = ts(it["published_at"]) if it.get("published_at") else None
        psq = []
        for n in linked:
            hits = sorted((p for p in src[n]["plumes"]
                           if p.get("scene_id") == it["id"]
                           or (str(p.get("scene_timestamp", ""))[:10] == it["timestamp"][:10]
                               and p.get("instrument") == it["instrument"])), key=lambda p: p["plume_id"])
            psq.append({"cmSource": n, "url": src[n]["url"], "params": {}, "responseSha256": src[n]["contentSha256"],
                        "plumeCount": len(hits), "plumeIds": [p["plume_id"] for p in hits],
                        "plumes": [{"plumeId": p["plume_id"], "emissionKgH": p.get("emission_auto"),
                                    "uncertaintyKgH": p.get("emission_uncertainty_auto")} for p in hits]})
        if it.get("published_plume_count", 0) > 0:
            sp, sm = scenes.scene_plumes(it["id"])
            near = []
            for p in sp:
                lon, lat = (p.get("geometry_json") or {}).get("coordinates", [None, None])
                if lon is None:
                    continue
                dkm = float(haversine_km(fac["lat"], fac["lon"], lat, lon))
                if dkm <= RADIUS_KM:
                    near.append({"plumeId": p["plume_id"], "distKm": round(dkm, 3),
                                 "emissionKgH": p.get("emission_auto")})
            scene_q = {"url": sm["url"], "params": sm["params"], "responseSha256": sm["contentSha256"],
                       "plumes": len(sp)}
        else:
            near, scene_q = [], {"fastPath": "published_plume_count == 0"}
        scene = {"sceneProviderId": it["id"], "sceneAt": t, "publishedAt": pub, "instrument": it["instrument"],
                 "cloudCoverPct": it.get("cloud_cover_pct"), "cloudCoverPctAssessed": it.get("cloud_cover_pct_assessed"),
                 "notCloudy": it.get("not_cloudy"), "originIntersect": True,
                 "perSourceQueries": psq, "plumesNearFacility": len(near), "marsRows": mars}
        # Rule 2 without look-ahead is THE rule (spec-v2 §15 2026-10-16): only origins published by the time the pass
        # can first be attested count. Undetermined (no per-point query and some origin published later) fails closed.
        attest_t = attestable_from(t, pub, pool["publicationLag"])
        known = origin_intersect_known(None if inside is None else inside.get(it["id"], []), point_published, attest_t)
        q = qualify({**scene, "originIntersect": known is True}, ctx)
        q_today = qualify(scene, {**ctx, "seen": set(ctx["seen"])})  # every origin known today (not the rule)
        if q["qualified"]:
            ctx["seen"].add(it["id"])
        mars_rows = [{k: r[k] for k in ("id_plume", "tile_date", "satellite", "ch4_fluxrate")}
                     | {"daysFromScene": round((r["t"] - t) / 86400, 2)} for r in q["marsNearbyRows"]]
        out_scenes.append({
            "sceneProviderId": it["id"], "sceneId": keccak_text(it["id"]), "sceneName": it["name"],
            "sceneRecordSha256": record_sha256(it),
            "sceneAt": t, "sceneAtIso": iso(t), "publishedAt": pub, "publishedAtIso": iso(pub),
            "instrument": it["instrument"], "platform": it.get("platform"),
            "cloudCoverPct": it.get("cloud_cover_pct"), "cloudCoverPctAssessed": it.get("cloud_cover_pct_assessed"),
            "notCloudy": it.get("not_cloudy"), "cloudBp": q["cloudBp"], "bounds": it.get("bounds"),
            "originIntersect": known is True,
            "originIntersectBasis": ("origins published by attestation time" if known is not None else
                                     "undetermined offline (no per-point query; some origin published later) -> "
                                     "refused, fail closed"),
            "originPointsInside": None if inside is None else inside.get(it["id"], []),
            "episodeOriginInside": None if inside is None else ep_point in inside.get(it["id"], []),
            "originIntersectAnyOriginToday": True,
            "qualifiedAllOriginsToday": q_today["qualified"], "refusalAllOriginsToday": q_today["refusal"],
            "perSourceQueries": psq, "publishedPlumeCount": it.get("published_plume_count"),
            "scenePlumesQuery": scene_q, "plumesNearFacility": near,
            "plumeOnScene": not q["checks"]["noPlume"]["ok"],
            "marsNearby": {"windowDays": pool["marsWindowDays"], "count": len(mars_rows), "rows": mars_rows},
            "sensitivityMode": it.get("sensitivity_mode"), "solarZenithAngle": it.get("solar_zenith_angle"),
            "offNadir": it.get("off_nadir"), "gsd": it.get("gsd"),
            "floorKgH": q["floorKgH"], "rateFloorKgH": q["floorKgH"],
            "attestableFrom": iso(q["checks"]["publicationLag"]["attestableFrom"]),
            "qualified": q["qualified"], "refusal": q["refusal"], "refusals": q["refusals"],
            "checks": {k: {kk: vv for kk, vv in v.items()} for k, v in q["checks"].items()},
        })

    return {
        "schema": "methaneclause.clean_passes/1", "case": case_name, "sourceId": case["sourceId"],
        "facility": {"lat": fac["lat"], "lon": fac["lon"], "country": fac["country"], "radiusKm": RADIUS_KM},
        "linkedCmSources": linked, "originPoints": json.loads(geom)["coordinates"],
        "originPointPublishedAt": [iso(x) for x in point_published], "episodeOriginPoint": ep_point,
        "originRule": "rule 2 without look-ahead (spec-v2 §15 2026-10-16): the footprint must contain an origin "
                      "point whose earliest plume was published by the time the pass can first be attested "
                      "(max(sceneAt + publicationLag, publishedAt)); undetermined offline = refused. "
                      "`qualifiedAllOriginsToday` shows the same scene against every origin known today (not the rule)",
        "countIsLowerBound": True,
        "countIsLowerBoundReason": ("a lower bound on what an observer at each pass's time would have counted "
                                    "(they saw at most today's plumes); re-checking later can only lower it."),
        "episode": ep, "since": since, "sinceIso": iso(since),
        "covenant": {"label": cfg["covenant"] or "none — hypothetical claim [SIM]", "policy": cfg["policy"],
                     "policyHash": pol.policy_hash(policy), "start": core["start"] if cfg["covenant"] else None,
                     "end": cov_end, "endIso": iso(cov_end)},
        "rules": {"publicationLag": pool["publicationLag"], "linkWindow": core["linkWindow"],
                  "marsWindowDays": pool["marsWindowDays"], "maxCloudBp": pool["maxCloudBp"],
                  "cloudRule": pool["cloudRule"], "instrumentFloorKgH": pool["instrumentFloorKgH"],
                  "plumeRadiusKm": RADIUS_KM},
        "evaluatedAt": now, "evaluatedAtIso": iso(now),
        "sceneQuery": {"url": qmeta["url"], "params": qmeta["params"], "responseSha256": qmeta["contentSha256"],
                       "fetchedAt": qmeta["fetchedAt"], "count": len(items)},
        "perPointQueries": point_queries or None,
        "summary": summarise(out_scenes, case, since, pool["instrumentFloorKgH"]),
        "generatedFrom": [{"path": _rel(p), "sha256": sha256_file(p)} for p in
                          (case_path, ROOT / "pipeline" / "cleanpass_rules.py", Path(__file__).resolve(),
                           ROOT / "pipeline" / "policy.py")]
        + [{"query": f"scenes/{case_name}", "contentSha256": qmeta["contentSha256"]}]
        + [{"query": f"scenes/{case_name}_pt{i:02d}", "contentSha256": q["contentSha256"]}
           for i, q in enumerate(point_queries)]
        + [{"query": f"source/{n}", "contentSha256": src[n]["contentSha256"]} for n in linked]
        + [{"query": f"scene_plumes/{s_['sceneProviderId']}", "contentSha256": s_["scenePlumesQuery"]["responseSha256"]}
           for s_ in out_scenes if "responseSha256" in s_["scenePlumesQuery"]],
        "licence": [f"{CM_LICENCE} — Data by Carbon Mapper; derived scene fields only", MARS_LICENCE],
        "scenes": out_scenes,
    }


def summarise(sc: list[dict], case: dict, since: int, floors: dict) -> dict:
    after = [s for s in sc if s["sceneAt"] > since]
    relapse = next((s for s in after if s["plumeOnScene"]), None)
    before = [s for s in after if relapse is None or s["sceneAt"] < relapse["sceneAt"]]
    q_before = [s for s in before if s["qualified"]]
    q_all = [s for s in sc if s["qualified"]]
    timeline_dates = {r["date"] for r in case["cm_timeline_union"]}
    scene_dates = {s["sceneAtIso"][:10] for s in sc}
    first = sc[0]["sceneAtIso"][:10] if sc else "9999"
    return {
        "candidates": len(sc), "qualified": len(q_all),
        "qualifiedByInstrument": dict(Counter(s["instrument"] for s in q_all)),
        "firstRefusal": dict(Counter(s["refusal"] for s in sc if s["refusal"])),
        "firstPlumeAfterSince": relapse and {"sceneName": relapse["sceneName"], "sceneAtIso": relapse["sceneAtIso"]},
        "qualifiedBeforeFirstPlume": len(q_before),
        "qualifiedBeforeFirstPlumeByInstrument": dict(Counter(s["instrument"] for s in q_before)),
        "upperBoundBpAtThatCount": u_bp(len(q_before)) if q_before else None,
        "qualifiedScenes": [f"{s['sceneAtIso'][:10]} {s['instrument']}" for s in q_all],
        "statusLabel": (f"{len(q_before)} qualified clean pass{'' if len(q_before) == 1 else 'es'} (a lower bound)" if q_before
                        else "no attestable clean pass yet"),
        "maxCountedFloorKgH": max((floors[s["instrument"]] for s in q_before), default=None),
        "countedAtCloudBp2500": sum(1 for s in q_before if s["cloudBp"] == 2500),
        "countedAtCloudBp2500Note": "counted passes whose cloud cover sits exactly at 25 % (EMIT reports cloud in "
                                    "bins); the gate stays Carbon Mapper's not_cloudy flag [D3]",
        "qualifiedBeforeFirstPlumeAllOriginsToday": sum(1 for s in before if s["qualifiedAllOriginsToday"]),
        "originIntersectUndetermined": sum(1 for s in sc if s["originIntersectBasis"].startswith("undetermined")),
        "timelineDatesWithoutIntersectingScene": sorted(d for d in timeline_dates - scene_dates if d >= first),
    }


def main() -> None:
    from schemas_v2 import validate

    for name in CASES:
        doc = build(name)
        validate(doc)
        write_json(DERIVED / "cases" / f"{name}.clean_passes.json", doc)
        s = doc["summary"]
        print(f"{name}: since {doc['sinceIso']}, {s['candidates']} scenes, {s['qualified']} qualified "
              f"{s['qualifiedByInstrument']}; before first plume after since: {s['qualifiedBeforeFirstPlume']} "
              f"{s['qualifiedBeforeFirstPlumeByInstrument']}; refusals {s['firstRefusal']}")


if __name__ == "__main__":
    main()
