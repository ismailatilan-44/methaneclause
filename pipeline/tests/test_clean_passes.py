"""Clean-pass qualification (spec-v2 §2 rules 1-6): each rule refuses the right synthetic scene, boundaries included."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from cleanpass_rules import REFUSAL_ORDER, qualify  # noqa: E402
from policy import POLICY_V2  # noqa: E402

FLOORS = POLICY_V2["pool"]["instrumentFloorKgH"]

DAY = 86_400
T0 = 1_730_000_000  # scene time used by the good scene


def good_scene(**kw):
    s = {"sceneProviderId": "uuid-1", "sceneAt": T0, "publishedAt": T0 + 20 * DAY, "instrument": "tan",
         "cloudCoverPct": 0, "cloudCoverPctAssessed": 0, "notCloudy": True, "originIntersect": True,
         "perSourceQueries": [{"cmSource": "S", "plumeCount": 0}], "plumesNearFacility": 0, "marsRows": []}
    s.update(kw)
    return s


def ctx(**kw):
    c = {"since": T0 - 10 * DAY, "now": T0 + 100 * DAY, "publicationLag": 60 * DAY, "covenantEnd": T0 + 400 * DAY,
         "floors": dict(FLOORS), "rateKgH": 2000, "marsWindowDays": 7, "maxCloudBp": 2500,
         "seen": set()}
    c.update(kw)
    return c


def test_good_scene_qualifies():
    r = qualify(good_scene(), ctx())
    assert r["qualified"] is True and r["refusal"] is None and r["refusals"] == []
    assert all(v["ok"] for v in r["checks"].values())
    assert r["floorKgH"] == FLOORS["tan"] and r["cloudBp"] == 0


@pytest.mark.parametrize("scene_kw, ctx_kw, code", [
    ({"notCloudy": False, "cloudCoverPct": 80, "cloudCoverPctAssessed": 75}, {}, "CLOUD_FLAG"),
    ({"notCloudy": True, "cloudCoverPct": 42, "cloudCoverPctAssessed": 0}, {}, "CLOUD_FLAG_MISMATCH"),
    ({"notCloudy": False, "cloudCoverPct": 10, "cloudCoverPctAssessed": 10}, {}, "CLOUD_FLAG_MISMATCH"),
    ({"notCloudy": True, "cloudCoverPct": None, "cloudCoverPctAssessed": None}, {}, "CLOUD_FLAG_MISMATCH"),
    ({"originIntersect": False}, {}, "NO_ORIGIN_INTERSECT"),
    ({"perSourceQueries": [{"cmSource": "S", "plumeCount": 1}]}, {}, "PLUME_ON_SCENE"),
    ({"plumesNearFacility": 2}, {}, "PLUME_ON_SCENE"),
    ({"marsRows": [{"t": T0 + 3 * DAY}]}, {}, "MARS_PLUME_NEARBY"),
    ({}, {"since": T0}, "SCENE_BEFORE_SINCE"),
    ({"publishedAt": None}, {}, "SCENE_NOT_PUBLISHED"),
    ({"publishedAt": T0 + 200 * DAY}, {}, "SCENE_NOT_PUBLISHED"),
    ({"publishedAt": T0 - 1}, {}, "SCENE_NOT_PUBLISHED"),
    ({}, {"now": T0 + 59 * DAY}, "BEFORE_PUBLICATION_LAG"),
    ({}, {"covenantEnd": T0}, "AFTER_TERM"),
    ({}, {"seen": {"uuid-1"}}, "DUPLICATE_SCENE"),
    ({"instrument": "ssc"}, {}, "UNKNOWN_INSTRUMENT"),
    ({"instrument": "emi"}, {"rateKgH": FLOORS["emi"] - 1}, "FLOOR_MISMATCH"),
    ({"publishedAt": T0 + 20 * DAY}, {"now": T0 - 1}, "FUTURE"),
])
def test_each_rule_refuses(scene_kw, ctx_kw, code):
    r = qualify(good_scene(**scene_kw), ctx(**ctx_kw))
    assert r["qualified"] is False
    assert code in r["refusals"]
    assert r["refusal"] == code or REFUSAL_ORDER.index(r["refusal"]) < REFUSAL_ORDER.index(code)


def test_boundaries():
    # exactly at since: refused (sceneAt <= since); one second later: fine
    assert qualify(good_scene(), ctx(since=T0))["refusal"] == "SCENE_BEFORE_SINCE"
    assert qualify(good_scene(), ctx(since=T0 - 1))["qualified"]
    # exactly sceneAt + lag: attestable
    assert qualify(good_scene(), ctx(now=T0 + 60 * DAY))["qualified"]
    # MARS plume exactly 7 days away refuses, 7 days + 1 s does not
    assert qualify(good_scene(marsRows=[{"t": T0 - 7 * DAY}]), ctx())["refusal"] == "MARS_PLUME_NEARBY"
    assert qualify(good_scene(marsRows=[{"t": T0 - 7 * DAY - 1}]), ctx())["qualified"]
    # cloud exactly 25 %: not cloudy by Carbon Mapper's flag and <= 2500 bp -> consistent
    assert qualify(good_scene(cloudCoverPct=25, cloudCoverPctAssessed=25), ctx())["qualified"]
    # floor equal to the rate passes
    assert qualify(good_scene(instrument="emi"), ctx(rateKgH=FLOORS["emi"]))["qualified"]
    # no covenant term (hypothetical claim): AFTER_TERM not applicable
    assert qualify(good_scene(), ctx(covenantEnd=None))["checks"]["term"]["ok"]


def test_every_failing_rule_is_listed_and_first_is_by_order():
    r = qualify(good_scene(notCloudy=False, cloudCoverPct=90, cloudCoverPctAssessed=90, originIntersect=False,
                           marsRows=[{"t": T0}]), ctx(since=T0))
    assert r["refusals"] == ["SCENE_BEFORE_SINCE", "CLOUD_FLAG", "NO_ORIGIN_INTERSECT", "MARS_PLUME_NEARBY"]
    assert r["refusal"] == "SCENE_BEFORE_SINCE"


def test_refusal_codes_are_the_spec_list():
    assert set(REFUSAL_ORDER) == {"CLOUD_FLAG", "CLOUD_FLAG_MISMATCH", "NO_ORIGIN_INTERSECT", "PLUME_ON_SCENE",
                                  "MARS_PLUME_NEARBY", "SCENE_BEFORE_SINCE", "SCENE_NOT_PUBLISHED",
                                  "BEFORE_PUBLICATION_LAG", "AFTER_TERM", "DUPLICATE_SCENE", "UNKNOWN_INSTRUMENT",
                                  "FLOOR_MISMATCH", "FUTURE"}


def test_origin_intersect_without_look_ahead():
    from cleanpass_rules import origin_intersect_known

    pub = [100, 100, 900]  # publication time of the earliest plume at each origin point
    # per-point result available: only point 2 inside, published after attestation -> no intersect
    assert origin_intersect_known([2], pub, attest_t=500) is False
    assert origin_intersect_known([0, 2], pub, attest_t=500) is True
    assert origin_intersect_known([2], pub, attest_t=900) is True
    # no per-point result: decidable only when every origin was already published
    assert origin_intersect_known(None, pub, attest_t=1000) is True
    assert origin_intersect_known(None, pub, attest_t=500) is None
