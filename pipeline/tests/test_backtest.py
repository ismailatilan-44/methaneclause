"""Prospective backtest (spec-v2 §7): the clause walk on tiny synthetic facilities, then properties of the committed
output (label, policy units, no currency headline, variants labelled)."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from backtest import episodes, walk_claims
from common import DERIVED

D0 = date(2025, 1, 1)


def d(n: int) -> str:
    return (D0 + timedelta(days=n)).isoformat()


def p(n, detected=False, rate=None, inst=("tan",)):
    return {"date": d(n), "instruments": set(inst), "detected": detected, "rate": rate}


def test_start_is_exclusive_and_below_min_rate_is_no_episode():
    passes = [p(0, True, 2000.0), p(10, True, 50.0), p(20, True, None), p(40, True, 1600.0)]
    eps, stats = episodes(passes, mars_dates=[], start=d(0))
    assert [e["date"] for e in eps] == [d(40)]  # day 0 is the start (not after it); 50 kg/h and hidden rates: none
    assert stats["detections_below_min_or_unquantified"] == 2 and eps[0]["tier"] == 2


def test_link_window_30_days():
    passes = [p(1, True, 600.0), p(31, True, 6000.0), p(32, True, 600.0)]
    eps, stats = episodes(passes, mars_dates=[], start=d(0))
    assert [e["date"] for e in eps] == [d(1), d(32)] and stats["linked"] == 1  # day 31 = 30 days later: linked


def test_corroboration_badges():
    passes = [p(1, True, 600.0, ("tan",)), p(5, True, None, ("emi",))]
    eps, _ = episodes(passes, mars_dates=[d(3)], start=d(0))
    assert eps[0]["L1"] is True and eps[0]["L2"] is True


def _claims(passes, mars=(), end=2000, scene_ok=None, variant="upper_bound"):
    eps, _ = episodes(passes, mars_dates=list(mars), start=d(0))
    return walk_claims(eps, passes, mars_dates=list(mars), end=d(end), scene_ok=scene_ok, variant=variant)


def test_installments_schedule_release_and_shares():
    # episode day 1, 900 kg/h (tier 3 = 100 units; above the EMIT floor); since = day 31; ten clean passes from day 40
    passes = [p(1, True, 900.0)] + [p(40 + 10 * i, inst=("tan",) if i % 2 else ("emi",)) for i in range(10)]
    (c,) = _claims(passes)
    assert c["cap"] == 1.0 and c["counted"] == 10 and c["instruments"] == ["emi", "tan"]  # policy units
    inst = c["installments"]
    assert [i["share"] for i in inst] == [3000, 3000, 4000] and [i["amount"] for i in inst] == [0.3, 0.3, 0.4]
    assert all(i["state"] == "released" for i in inst)
    final = inst[2]
    assert final["claimableAt"] == d(31 + 540 + 63)  # cure term + grace dominates


def test_relapse_cancels_pending_but_released_stay():
    passes = [p(1, True, 600.0)] + [p(40 + 5 * i) for i in range(6)] + [p(200, True, 900.0)]
    c = _claims(passes)[0]
    states = [i["state"] for i in c["installments"]]
    # 3rd pass (day 50) counts day 111 -> claimable day 175 < relapse day 200: released;
    # 6th pass (day 65) counts day 126 -> claimable day 190 < 200: released too
    assert states == ["released", "released"] and c["relapse"] == d(200)
    passes2 = [p(1, True, 600.0)] + [p(40 + 5 * i) for i in range(6)] + [p(180, True, 900.0)]
    c2 = _claims(passes2)[0]
    assert [i["state"] for i in c2["installments"]] == ["released", "cancelled"]


def test_counting_rules_floor_mars_and_since():
    passes = ([p(1, True, 600.0), p(20)]  # inside since: not counted
              + [p(40, inst=("GAO",))]  # airborne: no floor in the policy
              + [p(50), p(60), p(70)])  # MARS plume at day 55 refuses 50 and 60 (+/- 7 days)
    c = _claims(passes, mars=[d(55)])[0]
    assert c["counted"] == 1 and c["refused"] == {"before_since": 1, "no_floor": 1, "mars_nearby": 2}


def test_emit_floor_above_rate_is_not_counted():
    passes = [p(1, True, 500.0)] + [p(40 + i, inst=("emi",)) for i in range(3)]  # EMIT floor 700 > 500
    c = _claims(passes)[0]
    assert c["counted"] == 0 and c["refused"]["floor_above_rate"] == 3


def test_final_needs_two_instruments_and_mars_corroboration():
    passes = [p(1, True, 600.0)] + [p(40 + 10 * i) for i in range(10)]  # Tanager only
    c = _claims(passes)[0]
    assert len(c["installments"]) == 2 and c["finalBlocked"] == "one instrument"
    passes = [p(1, True, 900.0)] + [p(40 + 10 * i, inst=("tan",) if i % 2 else ("emi",)) for i in range(10)]
    c = _claims(passes, mars=[d(32)])[0]  # MARS plume inside [since, triggering pass], not within 7 d of a pass
    assert c["finalBlocked"] == "MARS plume in the corroboration window"


def test_qualified_variant_needs_scene_data_for_that_claim():
    passes = [p(1, True, 600.0)] + [p(40 + 10 * i) for i in range(3)]
    assert _claims(passes, variant="qualified", scene_ok=None)[0]["counted"] == 0
    ok = {d(1): {d(40): True, d(50): False, d(60): True}}  # keyed by the claim's episode date
    assert _claims(passes, variant="qualified", scene_ok=ok)[0]["counted"] == 2


def test_second_claim_does_not_reuse_the_first_claims_scene_checks():
    passes = [p(1, True, 900.0)] + [p(40 + 10 * i) for i in range(3)] + [p(200, True, 900.0), p(260), p(270)]
    ok = {d(1): {d(40): True, d(50): True, d(60): True, d(260): True, d(270): True}}
    first, second = _claims(passes, variant="qualified", scene_ok=ok)
    assert first["counted"] == 3 and second["counted"] == 0
    assert second["refused"] == {"no_scene_data_for_this_claim": 2}


def _ts(n: int, hour: int = 10) -> int:
    from datetime import datetime, timezone

    return int(datetime(D0.year, D0.month, D0.day, hour, tzinfo=timezone.utc).timestamp()) + n * 86_400


def _scene_doc(scenes, published=(0,)):
    """A scenes_v1 file with one origin point (published on day `published[0]`) and the given scenes."""
    base = {"sceneName": "x", "instrument": "tan", "cloudCoverPct": 10, "cloudCoverPctAssessed": 10, "notCloudy": True,
            "publishedPlumeCount": 0, "originPointsInside": [0], "perSourcePlumeIds": {}, "plumesNearFacility": [],
            "sceneRecordSha256": "00" * 32}
    out = []
    for i, s in enumerate(scenes):
        sc = {**base, "sceneProviderId": f"s{i}", **s}
        sc["sceneAt"], sc["publishedAt"] = _ts(sc.pop("day")), _ts(sc.pop("pub"), 12)
        out.append(sc)
    return {"schema": "methaneclause.scenes/1", "linkedCmSources": ["CH4_A"], "originPoints": [[0.0, 0.0]],
            "originPointPublishedAt": [_ts(n) for n in published], "evaluatedAt": _ts(2000), "scenes": out}


def test_claim_scene_checks_count_at_the_attestable_time():
    from backtest import claim_scene_checks

    ep = {"date": d(1), "rateKgH": 900.0}
    doc = _scene_doc([{"day": 40, "pub": 50},     # published early: counts at sceneAt + 60 d lag + 1 d clean window
                      {"day": 45, "pub": 120}])   # published late: counts at its publication + 1 d
    chk = claim_scene_checks(doc, ep, mars_ts=[])
    assert chk[d(40)] == {"ok": True, "countAt": d(101), "refusal": None, "scene": "s0", "scenes": ["s0"]}
    assert chk[d(45)]["countAt"] == d(121)  # the UTC date of that instant


def test_claim_scene_checks_refuse_with_the_rules():
    from backtest import claim_scene_checks

    ep = {"date": d(1), "rateKgH": 500.0}
    doc = _scene_doc([{"day": 20, "pub": 30},                                           # before since (day 31)
                      {"day": 40, "pub": 50, "notCloudy": False, "cloudCoverPct": 60, "cloudCoverPctAssessed": 60},
                      {"day": 41, "pub": 50, "perSourcePlumeIds": {"CH4_A": ["p1"]}},
                      {"day": 42, "pub": 50, "plumesNearFacility": ["p2"]},
                      {"day": 43, "pub": 50, "instrument": "emi"},                       # EMIT floor 700 > R 500
                      {"day": 44, "pub": 50, "originPointsInside": []},
                      {"day": 46, "pub": 50}])                                          # MARS plume on day 50
    chk = claim_scene_checks(doc, ep, mars_ts=[_ts(50)])
    assert {k: v["refusal"] for k, v in chk.items()} == {
        d(20): "SCENE_BEFORE_SINCE", d(40): "CLOUD_FLAG", d(41): "PLUME_ON_SCENE", d(42): "PLUME_ON_SCENE",
        d(43): "FLOOR_MISMATCH", d(44): "NO_ORIGIN_INTERSECT", d(46): "MARS_PLUME_NEARBY"}
    assert not any(v["ok"] for v in chk.values())


def test_claim_scene_checks_have_no_look_ahead():
    """Rule 2: the footprint must contain an origin published by the pass's attestation time, not today."""
    from backtest import claim_scene_checks

    ep = {"date": d(1), "rateKgH": 900.0}
    late = _scene_doc([{"day": 40, "pub": 50}], published=(150,))  # origin first published on day 150 > 40 + 60
    assert claim_scene_checks(late, ep, mars_ts=[])[d(40)]["refusal"] == "NO_ORIGIN_INTERSECT"
    early = _scene_doc([{"day": 40, "pub": 50}], published=(90,))
    assert claim_scene_checks(early, ep, mars_ts=[])[d(40)]["ok"] is True


def test_one_qualifying_scene_on_a_date_is_enough_and_count_uses_it():
    from backtest import claim_scene_checks

    ep = {"date": d(1), "rateKgH": 900.0}
    doc = _scene_doc([{"day": 40, "pub": 50, "notCloudy": False, "cloudCoverPct": 60, "cloudCoverPctAssessed": 60},
                      {"day": 40, "pub": 55}])
    assert claim_scene_checks(doc, ep, mars_ts=[])[d(40)] == {"ok": True, "countAt": d(101), "refusal": None,
                                                             "scene": "s1", "scenes": ["s1"]}


def test_qualified_walk_counts_at_the_scene_count_date():
    passes = [p(1, True, 600.0)] + [p(40), p(50)]
    ok = {d(1): {d(40): {"ok": True, "countAt": d(101), "refusal": None},
                 d(50): {"ok": True, "countAt": d(140), "refusal": None}}}
    c = _claims(passes, variant="qualified", scene_ok=ok, end=120)[0]
    assert c["counted"] == 1 and c["refused"] == {"counts_after_catalogue_end": 1}
    bad = {d(1): {d(40): {"ok": False, "countAt": None, "refusal": "CLOUD_FLAG"}}}
    c = _claims(passes, variant="qualified", scene_ok=bad)[0]
    assert c["counted"] == 0 and c["refused"] == {"scene_refused:CLOUD_FLAG": 1, "no_scene_record": 1}


def test_conservative_variant_ends_the_claim_at_an_own_plume_with_a_hidden_rate():
    passes = [p(1, True, 600.0)] + [p(40 + 5 * i) for i in range(6)]
    base = _claims(passes)[0]
    eps, _ = episodes(passes, mars_dates=[], start=d(0))
    cons = walk_claims(eps, passes, mars_dates=[], end=d(2000), scene_ok=None, variant="upper_bound",
                       extra_relapses=[d(20), d(52)])  # day 20 is inside the link window: ignored
    assert base["relapse"] is None and base["counted"] == 6
    assert cons[0]["relapse"] == d(52) and cons[0]["relapseKind"] == "own plume, rate hidden"
    assert cons[0]["counted"] == 0 and cons[0]["refused"]["voided_by_relapse"] == 3  # days 40-50 count after day 52


def test_released_with_plume_seen_names_each_plume_and_reason():
    from backtest import released_with_plume_seen

    claim = {"episode": d(1), "since": d(31), "installments": [
        {"state": "released", "claimableAt": d(200), "amount": 0.3},
        {"state": "cancelled", "claimableAt": d(300), "amount": 0.3}]}
    near = [{"plumeId": "own-hidden", "sceneAt": _ts(50), "ownSource": "CH4_A", "rateHidden": True, "rateKgH": None},
            {"plumeId": "neighbour", "sceneAt": _ts(60), "ownSource": None, "rateHidden": False, "rateKgH": 900.0},
            {"plumeId": "own-small", "sceneAt": _ts(70), "ownSource": "CH4_A", "rateHidden": False, "rateKgH": 50.0},
            {"plumeId": "before-since", "sceneAt": _ts(20), "ownSource": None, "rateHidden": True, "rateKgH": None},
            {"plumeId": "after-release", "sceneAt": _ts(250), "ownSource": None, "rateHidden": True, "rateKgH": None}]
    (hit,) = released_with_plume_seen([claim], near)
    assert hit["releasedUnits"] == 0.3 and hit["lastRelease"] == d(200)
    assert [(x["plumeId"], x["reason"]) for x in hit["plumes"]] == [
        ("own-hidden", "rate hidden"), ("neighbour", "a neighbour's source"), ("own-small", "below 100 kg/h")]
    assert released_with_plume_seen([{**claim, "installments": claim["installments"][1:]}], near) == []


def test_l2_counts_other_satellites_only_and_not_the_start_row():
    from backtest import l2_dates

    rows = [("2025-01-01T10:00:00", "Sentinel-2 - ESA"), ("2025-01-01T10:05:00", "Sentinel-2 - ESA"),
            ("2025-01-03", "EMIT - NASA"), ("2025-01-04", "Planet Tanager"), ("2025-01-05", "Landsat - NASA/USGS")]
    assert l2_dates(rows) == ["2025-01-05"]  # every row on the start date and same-sensor rows excluded


def test_counted_only_by_catalogue_end():
    passes = [p(1, True, 600.0)] + [p(40), p(50)]
    c = _claims(passes, end=105)[0]  # day 40 counts day 101 (<= 105), day 50 counts day 111 (> 105)
    assert c["counted"] == 1


def test_committed_output_labels():
    bt = json.loads((DERIVED / "backtest.json").read_text())
    assert bt["label"].startswith("counterfactual: if these sources had been under the clause; no clause existed")
    econ = bt["economicSentence"]  # the IEA's figure, quoted and attributed; checked on its own below
    ps = json.loads((DERIVED / "projection_sources.json").read_text())
    cs = ps["parameters"]["costScale"]
    src = ps["sources"][cs["sources"][0]]
    quote = next(x for x in src["quotes"] if f"USD {cs['usdPerTco2e']} per tonne" in x)
    assert econ.startswith(f"Context, not a comparison: the IEA writes that '{quote.rstrip('.')}' (")
    assert src["url"] in econ and src["tag"] == "[V]"
    assert econ.endswith("The clause's tier amounts are policy units and are not converted to a currency.")
    assert "[pending citation]" not in econ
    text = json.dumps({k: v for k, v in bt.items() if k != "economicSentence"}, ensure_ascii=False).lower()
    for allowed in ("not abatement", "abatement-cost figure (iea's term)"):
        text = text.replace(allowed, "")
    leftovers = [w for w in ("€", "eur ", "usd", "abate" + "ment") if w in text]  # a word that must not appear
    assert leftovers == []
    assert bt["targets"]["mars_targets"] == 1122
    assert set(bt["variants"]) == {"qualified", "upper_bound"}
    assert bt["amounts"]["unit"] == "policy units"
    selection = "the 1,122 sources MARS labels absent today — a label decided after the window"
    card = bt["card"]
    assert all(selection in v for k, v in card.items() if k.endswith("Sentence"))
    t = bt["totals"]
    assert f"{t['episodes']:,} episodes" in card["episodesSentence"]
    assert f"{t['rebatePolicyUnits']:,.2f} policy units" in card["episodesSentence"]
    low = bt["stake_band"]["kt_ch4_per_yr"]["low"] / 1000
    assert card["stakeSentence"].startswith(f"Stake, not abatement: ~{low:.1f} Mt CH₄/yr, the low bound of the band, ")
    assert "at least" not in card["stakeSentence"], "the figure is the band's low bound, not a floor"
    assert card["stakeSentence"].endswith("which biases the seen duty cycle upward (roadmap: a duty cycle measured on "
                                          "un-targeted EMIT passes).") and "tasked" in card["stakeSentence"]
    n_below = bt["stake_band"]["facilitiesQuantified"] - t["facilitiesWithEpisode"]  # why 428 is not 425
    assert (f"across {bt['stake_band']['facilitiesQuantified']:,} facilities" in card["stakeSentence"]
            and f"the {t['facilitiesWithEpisode']:,} with an episode plus {n_below:,} whose plumes all fell below"
            in card["stakeSentence"])
    assert "cadence badge" in card["l1Sentence"]
    assert card["l2Sentence"].startswith(f"{round(t['L2Share'] * 100)} % of episodes had a MARS plume from a "
                                         "satellite other than EMIT or Tanager within 7 days")
    assert t["L2Episodes"] == round(t["L2Share"] * t["episodes"])
    assert "not quoted" in bt["stake_band"]["midHighLabel"]
    assert "not an estimate" in t["upper_bound"]["label"]
    q = t["qualified"]  # B7: the refunds sentence is generated from the qualified totals over the fetched scene data
    rs = card["refundsSentence"]
    sd = bt["scene_data"]
    seen = sd["releasedWithPlumeSeen"]
    assert rs.startswith("Counterfactual (no clause existed): refunds count only qualified passes (cloud flag, origin "
                         "intersect without look-ahead, per-source query and no plume within 1.5 km, MARS ±7 d, 60-day "
                         "publication lag).")
    assert (f"Scene data covers {q['facilitiesWithSceneData']:,} facilities (the join candidates, which had a Tanager "
            f"plume within 1.5 km in Mar–Sep 2026 and open a claim, plus two demo cases, among {selection}).") in rs
    assert (f"Of their {q['claimsWithSceneData']:,} claims, {q['claimsWithCountedPass']:,} had a counted pass "
            f"({q['countedPasses']:,} passes on {sd['concentration']['countedScenesDistinct']:,} scenes): installments "
            f"{q['installments']['released']:,} released, {q['installments']['cancelled']:,} cancelled, "
            f"{q['installments']['pending']:,} pending") in rs
    assert (f"{seen['claims']:,} released claims ({seen['releasedPolicyUnits']:,.2f} units) had a plume at the facility "
            "that the clause does not count as a relapse") in rs
    assert seen["claims"] == len(seen["list"]) and all(c["plumes"] for c in seen["list"])
    assert seen["releasedPolicyUnits"] == round(sum(c["releasedUnits"] for c in seen["list"]), 2)
    assert rs.endswith(f"The other {q['claims'] - q['claimsWithSceneData']:,} claims were not fetched and count nothing.")
    cons = t["qualified_conservative"]  # JSON only: an own source's plume with a hidden rate is a relapse
    assert cons["refundPolicyUnits"]["released"] <= q["refundPolicyUnits"]["released"] and "JSON only" in cons["label"]
    assert "backdated start" in card["episodesSentence"]
    j = json.loads((DERIVED / "join_v1.json").read_text())
    assert f"{sd['byCountryNote'].split(' join candidates')[0].split(': ')[1]}" == (
        f"{sum(1 for c in j['candidates'] if c['country'] == 'Turkmenistan')} of {len(j['candidates'])}")
    assert q["claims"] - q["claimsWithSceneData"] == sum(q["claimsWithoutSceneData"].values())
    assert not any("upper" in v.lower() for v in card.values())  # the upper-bound variant stays in the JSON only
    assert bt["scene_data"]["facilities"] == q["facilitiesWithSceneData"]
    assert "read" in bt["stake_band"]["gwpSources"] and "secondary" not in bt["stake_band"]["gwpSources"]
    whole = json.dumps(bt)
    assert "x100" not in whole and "×100" not in whole and "229200" not in json.dumps(card)
    assert bt["exporter_table"]["importers"].endswith("[SIM]")
    sb = bt["stake_band"]
    assert {"gwp100", "gwp20"} <= set(sb["co2e"]) and sb["label"].startswith("stake, not abatement")


def test_stake_reasoned_refusal():
    from stake import band_from

    r = band_from(passes=0, detected=0, rates=[])
    assert r["status"] == "not_quantified" and "no satellite pass" in r["reason"]
    r = band_from(passes=5, detected=2, rates=[])
    assert r["status"] == "not_quantified" and "no quantified plume" in r["reason"]
    r = band_from(passes=5, detected=2, rates=[1000.0, 2000.0])
    assert r["status"] == "quantified" and r["kt_ch4_per_yr"]["low"] <= r["kt_ch4_per_yr"]["high"]
