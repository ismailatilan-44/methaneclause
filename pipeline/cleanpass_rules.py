"""Qualification of one candidate clean pass (spec-v2 §2 rules 1-6). Pure: no network, no clock.

Every check is evaluated and recorded (`checks`, `refusals`); `refusal` is the first failing code in REFUSAL_ORDER
(time and identity first, then instrument, cloud, footprint, plume, second provider). Shared by `clean_passes.py`
(B1, case files) and `observer/cleanpass.py` (B2, the observer that signs).

scene: sceneProviderId, sceneAt (unix s), publishedAt (unix s | None), instrument, cloudCoverPct,
       cloudCoverPctAssessed, notCloudy, originIntersect, perSourceQueries [{cmSource, plumeCount}],
       plumesNearFacility (scene plumes with origin within the facility radius), marsRows [{t: unix s}]
ctx:   since, now, publicationLag (s), covenantEnd (unix s | None = no covenant term), floors {instrument: kg/h},
       rateKgH (the episode's attested rate R), marsWindowDays, maxCloudBp, seen (scene ids already used)
"""
from __future__ import annotations

DAY = 86_400
REFUSAL_ORDER = [
    "FUTURE", "SCENE_BEFORE_SINCE", "SCENE_NOT_PUBLISHED", "BEFORE_PUBLICATION_LAG", "AFTER_TERM",
    "DUPLICATE_SCENE", "UNKNOWN_INSTRUMENT", "FLOOR_MISMATCH", "CLOUD_FLAG_MISMATCH", "CLOUD_FLAG",
    "NO_ORIGIN_INTERSECT", "PLUME_ON_SCENE", "MARS_PLUME_NEARBY",
]


def attestable_from(scene_at: int, published_at: int | None, publication_lag: int) -> int:
    """First time a pass can be attested: the publication lag has run and the scene record is published.
    One formula for the case files (no-look-ahead origins) and the observer's bundle."""
    return max(scene_at + publication_lag, published_at if published_at is not None else scene_at)


def cloud_bp(pct, assessed) -> int | None:
    vals = [v for v in (pct, assessed) if v is not None]
    return 100 * max(vals) if vals else None


def qualify(scene: dict, ctx: dict) -> dict:
    t, pub, now, since = scene["sceneAt"], scene.get("publishedAt"), ctx["now"], ctx["since"]
    inst = scene.get("instrument")
    floor = ctx["floors"].get(inst)
    cbp = cloud_bp(scene.get("cloudCoverPct"), scene.get("cloudCoverPctAssessed"))
    flag = scene.get("notCloudy")
    within = cbp is not None and cbp <= ctx["maxCloudBp"]
    win = ctx["marsWindowDays"] * DAY
    mars_near = [r for r in scene.get("marsRows", []) if abs(r["t"] - t) <= win]
    counts = [q["plumeCount"] for q in scene.get("perSourceQueries", [])]
    per_source_hits = sum(abs(c) for c in counts)
    near = scene.get("plumesNearFacility") or 0
    end = ctx.get("covenantEnd")

    checks = {
        "future": {"ok": t <= now, "code": "FUTURE", "rule": "sceneAt <= now"},
        "since": {"ok": t > since, "code": "SCENE_BEFORE_SINCE", "rule": "since < sceneAt", "since": since},
        "published": {"ok": pub is not None and t <= pub <= now, "code": "SCENE_NOT_PUBLISHED",
                      "rule": "sceneAt <= publishedAt <= now"},
        "publicationLag": {"ok": now >= t + ctx["publicationLag"], "code": "BEFORE_PUBLICATION_LAG",
                           "rule": "now >= sceneAt + publicationLag", "attestableFrom": t + ctx["publicationLag"]},
        "term": {"ok": end is None or t < end, "code": "AFTER_TERM", "rule": "sceneAt < covenant.end",
                 "covenantEnd": end},
        "duplicate": {"ok": scene["sceneProviderId"] not in ctx["seen"], "code": "DUPLICATE_SCENE",
                      "rule": "scene not already used for this claim"},
        "instrument": {"ok": floor is not None, "code": "UNKNOWN_INSTRUMENT", "rule": "instrument in floor table"},
        "floor": {"ok": floor is not None and floor <= ctx["rateKgH"], "code": "FLOOR_MISMATCH",
                  "rule": "instrument floor <= episode rate R", "floorKgH": floor, "rateKgH": ctx["rateKgH"]},
        "cloudConsistent": {"ok": flag is not None and cbp is not None and bool(flag) == within,
                            "code": "CLOUD_FLAG_MISMATCH", "rule": "not_cloudy == (cloudBp <= maxCloudBp)",
                            "cloudBp": cbp},
        "cloud": {"ok": flag is True, "code": "CLOUD_FLAG", "rule": "Carbon Mapper not_cloudy == true [D3]"},
        "originIntersect": {"ok": bool(scene.get("originIntersect")), "code": "NO_ORIGIN_INTERSECT",
                            "rule": "scene footprint contains >= 1 plume-origin point of the linked CM sources"},
        "noPlume": {"ok": all(c == 0 for c in counts) and near == 0, "code": "PLUME_ON_SCENE",
                    "rule": "no plume of a linked CM source on this scene, and no scene plume with origin inside "
                            "the facility radius", "perSourcePlumes": per_source_hits, "plumesNearFacility": near},
        "mars": {"ok": not mars_near, "code": "MARS_PLUME_NEARBY",
                 "rule": f"no UNEP MARS plume at the facility within +/- {ctx['marsWindowDays']} days",
                 "count": len(mars_near)},
    }
    failing = {c["code"] for c in checks.values() if not c["ok"]}
    refusals = [code for code in REFUSAL_ORDER if code in failing]
    return {"checks": checks, "refusals": refusals, "refusal": refusals[0] if refusals else None,
            "qualified": not refusals, "floorKgH": floor, "cloudBp": cbp, "marsNearbyRows": mars_near}


def origin_intersect_known(inside: list[int] | None, point_published: list[int], attest_t: int) -> bool | None:
    """Rule 2 without look-ahead: does the footprint contain an origin point whose plume was already published when
    the pass could first be attested (`attest_t`)? `inside` = indexes of origin points inside the footprint (per-point
    queries) or None if only the MultiPoint query was made; then the answer is known only if every origin was
    already published (the MultiPoint result then applies unchanged), else None (undetermined)."""
    if inside is None:
        return True if all(p <= attest_t for p in point_published) else None
    return any(point_published[i] <= attest_t for i in inside)
