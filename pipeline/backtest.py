"""Prospective backtest of the two-sided clause over the MARS target set (spec §16).

Counterfactual: if these sources had been under the clause; no clause existed. Committed inputs only.

Definition (also written into the output as `definition`):
  targets     the 1,122 MARS oil & gas sources that are notified and labelled "absent" (join criterion rule 1)
  facility    a target with the Carbon Mapper sources it owns (nearest target within 1.5 km, join rule 3); passes are
              the union of their observation dates; a detection at the facility is a Carbon Mapper plume (de-duplicated
              by plume_id) whose origin lies within 1.5 km of the target, on that date
  start       the first MARS plume's tile date (a backdated start: MARS publishes a plume after its tile date, so a
              covenant could not have been written on that day); only passes strictly after it count; end = the last
              observation date in cm_timelines.json (catalogue end)
  episode     a detection with a quantified rate >= minRateKgH (100 kg/h); tier of the largest quantified plume that
              day; a detection within 30 days (linkWindow) of the episode that anchors it is LINKED (no new rebate)
  rebate      the tier's amount in policy units (POLICY_V2_VERDICT_ONLY tier amounts; a parameter, not a price)
  claim       each episode opens one; since = episode date + 30 days; cap = the episode's rebate; the next episode
              (a relapse) cancels it, effective on the relapse's detection date. Which way each choice errs:
                - effective on the detection date, earlier than on chain (publication + the core's windows): fewer
                  refunds than the contract would release;
                - only a quantified plume >= minRateKgH from an owned source is a relapse: a plume at the facility
                  with a hidden rate, below 100 kg/h, or from a neighbour's source (join rule 3) does not cancel:
                  more refunds. `releasedWithPlumeSeen` lists those claims; the `qualified_conservative` variant
                  also treats an own source's plume with a hidden rate as a relapse (JSON only)
  clean pass  a detection-free pass after since, before the relapse, counted 61 days after the scene (60-day
              publication lag + 1-day clean window) and only if that is by the catalogue end. Variants:
                qualified    a scene of that date passes spec-v2 §2 rules 1-6 (cleanpass_rules.qualify) for THIS claim
                             on the committed scene records (data/derived/scenes_v1/, fetched for the join candidates
                             and the three demo cases that open a claim): cloud flag, origin intersect without
                             look-ahead, per-source query and plumes within 1.5 km, MARS +/- 7 days, floor <= the
                             episode rate, published; counted at max(scene + 60 d, scene publication) + 1 d. Other
                             facilities are "unqualified — scene data not fetched" and count 0
                upper_bound  every detection-free pass whose instrument has a published floor <= the episode rate and
                             no MARS plume within +/- 7 days (cloud and footprint not checked: an upper bound)
  installments  3 / 6 / 10 counted passes -> 30 / 30 / 40 % of the cap; claimable 64 days after counting; the final
              needs two instruments, no MARS plume in [since, its triggering pass] (the corroboration), and is
              claimable no earlier than since + 540 + 63 days; it must fit the covenant (since + 603 <= end + 60).
              Released when claimable before the relapse and by the catalogue end; cancelled when the relapse comes
              first; pending when claimable after the catalogue end
  corroboration badges  L1 = another Carbon Mapper instrument detects at the facility within +/- 7 days; L2 = a MARS
              plume (other satellites) within +/- 7 days
  stake band  the v1 method per facility over the passes after start (stake.band_from: duty cycle with its 95 %
              Clopper-Pearson interval x P10 / median / P90 plume rate x 8,760 h), summed over quantified facilities
              (a sum of per-source bands, not a joint interval); CO2e with GWP-100 and GWP-20 (floors.py). A stake,
              not abatement: satellites see plumes above their floor on a fraction of days

    cd pipeline && .venv/bin/python backtest.py     # -> data/derived/backtest.json
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from cleanpass_rules import attestable_from, origin_intersect_known, qualify
from common import DERIVED, RADIUS_KM, ROOT, haversine_km, mars_target, sha256_file, write_json
from policy import POLICY, POLICY_V2_VERDICT_ONLY, policy_hash, tier_of

LABEL = "counterfactual: if these sources had been under the clause; no clause existed"
POOL = POLICY_V2_VERDICT_ONLY["pool"]
CORE = POLICY_V2_VERDICT_ONLY["core"]
DAY = 86_400
LINK = CORE["linkWindow"] // DAY
LAG = POOL["publicationLag"] // DAY
CLEAN = POOL["cleanWindow"] // DAY
HOLD = POOL["holdWindow"] // DAY
CURE = POOL["cureTerm"] // DAY
GRACE = (POOL["publicationLag"] + CORE["challengeWindow"] + CORE["contestWindow"]) // DAY
MARS_DAYS = POOL["marsWindowDays"]
FLOORS = POOL["instrumentFloorKgH"]
CASES = ("DZA_S_012", "TKM_S_276", "USA_S_077")
SCENES_V1 = DERIVED / "scenes_v1"
QUALIFIED_RULES = (f"qualified passes (cloud flag, origin intersect without look-ahead, per-source query and no plume "
                   f"within {RADIUS_KM:g} km, MARS ±{MARS_DAYS} d, {LAG}-day publication lag)")
SAME_SENSOR = {"EMIT - NASA", "Planet Tanager"}  # MARS rows from the sensors that open episodes: not a second provider
SELECTION = "the 1,122 sources MARS labels absent today — a label decided after the window"
EXPORTER = {"country": "Algeria", "importers": "Italy / Spain-type importers [SIM]"}


def _d(s: str) -> date:
    return date.fromisoformat(s[:10])


def _plus(s: str, days: int) -> str:
    return (_d(s) + timedelta(days=days)).isoformat()


def _unix(s: str) -> int:
    """MARS tile_date (UTC, with or without a zone suffix) -> unix seconds."""
    s = s.replace("Z", "+00:00")
    t = datetime.fromisoformat(s)
    return int((t if t.tzinfo else t.replace(tzinfo=timezone.utc)).timestamp())


def _near(day: str, dates, days: int) -> bool:
    t = _d(day)
    return any(abs((_d(x) - t).days) <= days for x in dates)


# ------------------------------------------------------------------------------------------------ the walk
def l2_dates(rows: list[tuple[str, str]]) -> list[str]:
    """MARS rows that count for L2: satellites other than EMIT and Tanager, without any row on the start date (the
    first MARS plume date; several satellites can see a source that day)."""
    rows = sorted(rows)
    start = rows[0][0][:10] if rows else None
    return [d_[:10] for d_, sat in rows if d_[:10] != start and sat not in SAME_SENSOR]


def episodes(passes: list[dict], *, mars_dates: list[str], start: str,
             l2: list[str] | None = None) -> tuple[list[dict], dict]:
    """Episodes from the detection stream after `start` (exclusive), with the 30-day link window. L2 uses `l2`
    (other-satellite MARS rows, start row excluded) when given, else `mars_dates`."""
    eps, anchor = [], None
    stats = Counter()
    after = sorted((p for p in passes if p["date"] > start[:10]), key=lambda p: p["date"])
    dets = [p for p in after if p["detected"]]
    for p in dets:
        stats["detections"] += 1
        t = tier_of(p["rate"]) if p["rate"] is not None else None
        if t is None:
            stats["detections_below_min_or_unquantified"] += 1
            continue
        if anchor is not None and (_d(p["date"]) - _d(anchor)).days <= LINK:
            stats["linked"] += 1
            continue
        anchor = p["date"]
        others = [q for q in dets if q is not p and abs((_d(q["date"]) - _d(p["date"])).days) <= MARS_DAYS
                  and q["instruments"] - p["instruments"]]
        eps.append({"date": p["date"], "tier": t, "rateKgH": round(float(p["rate"]), 1),
                    "instruments": sorted(p["instruments"]),
                    "rebatePolicyUnits": POOL["unitTierAmounts"][t - 1] / 100,
                    "L1": bool(others), "L2": _near(p["date"], mars_dates if l2 is None else l2, MARS_DAYS)})
    stats.setdefault("linked", 0)
    stats.setdefault("detections_below_min_or_unquantified", 0)
    return eps, dict(stats)


def walk_claims(eps: list[dict], passes: list[dict], *, mars_dates: list[str], end: str,
                scene_ok: dict[str, dict[str, bool]] | None, variant: str,
                extra_relapses: list[str] = ()) -> list[dict]:
    """One claim per episode; clean passes counted per `variant` ("qualified" or "upper_bound"). `scene_ok` is keyed
    by the claim's episode date: a claim uses only the scene checks made for it (any MARS row refuses a pass in the
    upper bound; the corroboration also uses any MARS row). `extra_relapses`: dates that also end a claim when they
    fall after its `since` (the conservative variant: an own source's plume with a hidden rate)."""
    clean = sorted((p for p in passes if not p["detected"]), key=lambda p: p["date"])
    out = []
    for k, e in enumerate(eps):
        since = _plus(e["date"], LINK)
        relapse = eps[k + 1]["date"] if k + 1 < len(eps) and eps[k + 1]["date"] <= end else None
        relapse_kind = "episode" if relapse else None
        extra = min((x for x in extra_relapses if since < x <= end), default=None)
        if extra and (relapse is None or extra < relapse):
            relapse, relapse_kind = extra, "own plume, rate hidden"
        stop = relapse or _plus(end, 1)
        counted, inst_seen, refused, installments, final_blocked = 0, set(), Counter(), [], None
        counted_detail = []
        thresholds = {r[0]: r for r in POOL["installments"]}
        claim_scenes = None if scene_ok is None else scene_ok.get(e["date"])
        for p in clean:
            if not (e["date"] < p["date"] < stop):
                continue
            if p["date"] <= since:
                refused["before_since"] += 1
                continue
            floored = {i: FLOORS[i] for i in p["instruments"] if i in FLOORS}
            ok_inst = {i for i, f in floored.items() if f <= e["rateKgH"]}
            if not floored:
                refused["no_floor"] += 1
                continue
            if not ok_inst:
                refused["floor_above_rate"] += 1
                continue
            c = _plus(p["date"], LAG + CLEAN)
            if variant == "qualified":
                if scene_ok is None:
                    refused["no_scene_data"] += 1
                    continue
                if claim_scenes is None:
                    refused["no_scene_data_for_this_claim"] += 1
                    continue
                if p["date"] not in claim_scenes:
                    refused["no_scene_record"] += 1
                    continue
                chk = claim_scenes[p["date"]]
                if isinstance(chk, dict):  # scenes_v1: rules 1-6 per claim; counted at the scene's attestable time
                    if not chk["ok"]:
                        refused[f"scene_refused:{chk['refusal']}"] += 1
                        continue
                    c = chk["countAt"]
                elif not chk:
                    refused["scene_refused"] += 1
                    continue
            elif _near(p["date"], mars_dates, MARS_DAYS):
                refused["mars_nearby"] += 1
                continue
            if c > end:
                refused["counts_after_catalogue_end"] += 1
                continue
            if relapse and c >= relapse:
                refused["voided_by_relapse"] += 1
                continue
            counted += 1
            inst_seen |= ok_inst
            counted_detail.append({"date": p["date"], "countAt": c,
                                   "scenes": chk.get("scenes", []) if variant == "qualified" and isinstance(chk, dict)
                                   else []})
            rule = thresholds.get(counted)
            if not rule:
                continue
            after_passes, share, two, final = rule
            if final:
                if two and len(inst_seen) < 2:
                    final_blocked = "one instrument"
                    continue
                if any(since <= m[:10] <= p["date"] for m in mars_dates):
                    final_blocked = "MARS plume in the corroboration window"
                    continue
                if _plus(since, CURE + GRACE) > _plus(end, LAG):
                    final_blocked = "final out of term"
                    continue
                claimable = max(_plus(c, HOLD), _plus(since, CURE + GRACE))
            else:
                claimable = _plus(c, HOLD)
            state = ("cancelled" if relapse and claimable >= relapse else
                     "released" if claimable <= end else "pending")
            installments.append({"index": len(installments) + 1, "afterPasses": after_passes, "share": share,
                                 "amount": round(e["rebatePolicyUnits"] * share / 10000, 2), "scheduledAt": c,
                                 "claimableAt": claimable, "state": state})
        out.append({"episode": e["date"], "since": since, "cap": e["rebatePolicyUnits"], "counted": counted,
                    "instruments": sorted(inst_seen), "refused": {k2: v for k2, v in sorted(refused.items()) if v},
                    "installments": installments, "relapse": relapse, "relapseKind": relapse_kind,
                    "finalBlocked": final_blocked, "countedPasses": counted_detail})
    return out


# ------------------------------------------------------------------------------------------------ the data
def facilities() -> tuple[list[dict], str, dict]:
    from join.join import nearest

    mars_all = pd.read_csv(DERIVED / "mars_sources.csv")
    targets = mars_target(mars_all).sort_values("source_name").reset_index(drop=True)
    mp = pd.read_csv(DERIVED / "mars_plumes_og.csv")
    mars_rows = {k: sorted(zip(g["tile_date"], g["satellite"])) for k, g in mp.groupby("source_name")}
    mars_dates = {k: [d_[:10] for d_, _ in v] for k, v in mars_rows.items()}
    cm_src = pd.read_csv(DERIVED / "cm_sources.csv")
    tl = json.loads((DERIVED / "cm_timelines.json").read_text())
    end = max(o[0] for t in tl.values() for o in t["obs"])
    idx, dist = nearest(targets, cm_src["source_latitude"].to_numpy(), cm_src["source_longitude"].to_numpy())
    owned: dict[int, list[str]] = defaultdict(list)
    for name, i, dkm in zip(cm_src["source_name"], idx, dist):
        if dkm <= RADIUS_KM and name in tl:
            owned[int(i)].append(name)
    pl = pd.read_csv(DERIVED / "cm_plumes.csv").drop_duplicates("plume_id")
    pl = pl[pl["source_name"].isin({n for v in owned.values() for n in v})]
    by_src = {k: g for k, g in pl.groupby("source_name")}
    facs = []
    for i, t in targets.iterrows():
        names = sorted(owned.get(i, []))
        obs: dict[str, set] = defaultdict(set)
        for n in names:
            for day, inst in tl[n]["obs"]:
                obs[day] |= set(inst.split("+"))
        rates: dict[str, list] = defaultdict(list)
        det: set[str] = set()
        all_rates = []
        for n in names:
            g = by_src.get(n)
            if g is None:
                continue
            dkm = haversine_km(t["lat"], t["lon"], g["lat"].to_numpy(), g["lon"].to_numpy())
            for (_, r), dk in zip(g.iterrows(), dkm):
                if dk > RADIUS_KM or not isinstance(r["scene_timestamp"], str):
                    continue
                day = r["scene_timestamp"][:10]
                det.add(day)
                obs.setdefault(day, {r["instrument"]})
                if r["emission_auto"] == r["emission_auto"]:
                    rates[day].append(float(r["emission_auto"]))
                    all_rates.append((day, float(r["emission_auto"])))
        passes = [{"date": day, "instruments": inst, "detected": day in det,
                   "rate": max(rates[day]) if rates.get(day) else None} for day, inst in sorted(obs.items())]
        md = mars_dates.get(t["source_name"], [])
        facs.append({"name": t["source_name"], "country": t["country"], "lat": float(t["lat"]), "lon": float(t["lon"]),
                     "cm_sources": names, "passes": passes,
                     "mars_dates": md, "l2_dates": l2_dates(mars_rows.get(t["source_name"], [])),
                     "mars_ts": [_unix(d_) for d_, _ in mars_rows.get(t["source_name"], [])],
                     "start": md[0] if md else None, "rates": all_rates})
    inputs = [DERIVED / p for p in ("mars_sources.csv", "mars_plumes_og.csv", "cm_sources.csv", "cm_timelines.json",
                                    "cm_plumes.csv")]
    return facs, end, {str(p.relative_to(ROOT)): sha256_file(p) for p in inputs}


def scene_data() -> dict[str, dict]:
    """The committed scene files (`scene_records.py` -> data/derived/scenes_v1/<facility>.json), by facility. Each file
    must still match the INDEX hash written when it was derived (fail closed on an edited file)."""
    index = json.loads((SCENES_V1 / "INDEX.json").read_text())
    out = {}
    for name, h in index["files"].items():
        p = SCENES_V1 / f"{name}.json"
        if sha256_file(p) != h:
            raise ValueError(f"{p}: SHA-256 differs from scenes_v1/INDEX.json")
        out[name] = json.loads(p.read_text())
    return out


def _iso_day(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).date().isoformat()


def claim_scene_checks(doc: dict, ep: dict, *, mars_ts: list[int]) -> dict[str, dict]:
    """Scene checks for ONE claim (episode `ep`), keyed by scene date: every scene of the facility's file through
    `cleanpass_rules.qualify` with this claim's context — since = the end of the day `linkWindow` after the episode, R =
    the episode's rate, the facility's MARS rows (any satellite, ±7 d), the scene's own publication, and rule 2 without
    look-ahead (an origin point counts only if published by the pass's attestation time). A date qualifies if one of its
    scenes does; it counts on the UTC date of attestable time + cleanWindow of the earliest qualifying scene. Today's
    catalogue and MARS snapshot can only refuse more than an observer at the time would have."""
    since = int(datetime.fromisoformat(_plus(ep["date"], LINK) + "T00:00:00+00:00").timestamp()) + DAY - 1
    ctx = {"since": since, "now": doc["evaluatedAt"], "publicationLag": POOL["publicationLag"], "covenantEnd": None,
           "floors": FLOORS, "rateKgH": ep["rateKgH"], "marsWindowDays": MARS_DAYS, "maxCloudBp": POOL["maxCloudBp"],
           "seen": set()}
    published = [x if x is not None else 1 << 62 for x in doc["originPointPublishedAt"]]
    mars = [{"t": t} for t in mars_ts]
    out: dict[str, dict] = {}
    for s in doc["scenes"]:
        attest = attestable_from(s["sceneAt"], s["publishedAt"], POOL["publicationLag"])
        scene = {**s, "originIntersect": origin_intersect_known(s["originPointsInside"], published, attest) is True,
                 "perSourceQueries": [{"cmSource": n, "plumeCount": len(s["perSourcePlumeIds"].get(n, []))}
                                      for n in doc["linkedCmSources"]],
                 "plumesNearFacility": len(s["plumesNearFacility"]), "marsRows": mars}
        q = qualify(scene, ctx)
        day = _iso_day(s["sceneAt"])
        prev = out.get(day)
        if q["qualified"]:
            ctx["seen"].add(s["sceneProviderId"])
            count_at = _iso_day(attest + POOL["cleanWindow"])
            both = (prev["scenes"] if prev and prev["ok"] else []) + [s["sceneProviderId"]]
            if prev is None or not prev["ok"] or count_at < prev["countAt"]:
                out[day] = {"ok": True, "countAt": count_at, "refusal": None, "scene": s["sceneProviderId"]}
            out[day]["scenes"] = both  # every scene that qualifies for this claim on that date
        elif prev is None:
            out[day] = {"ok": False, "countAt": None, "refusal": q["refusal"], "scene": None, "scenes": []}
    return out


def _sum_inst(claims: list[dict]) -> dict:
    states = Counter(i["state"] for c in claims for i in c["installments"])
    units = Counter()
    for c in claims:
        for i in c["installments"]:
            units[i["state"]] = round(units[i["state"]] + i["amount"], 2)
    return {"claims": len(claims), "claimsWithCountedPass": sum(1 for c in claims if c["counted"]),
            "countedPasses": sum(c["counted"] for c in claims),
            "installments": {s: states.get(s, 0) for s in ("released", "cancelled", "pending")},
            "refundPolicyUnits": {s: units.get(s, 0) for s in ("released", "cancelled", "pending")},
            "finalBlocked": dict(Counter(c["finalBlocked"] for c in claims if c["finalBlocked"])),
            "refused": dict(sum((Counter(c["refused"]) for c in claims), Counter()))}


def run() -> dict:
    from floors import GWP
    from stake import band_from

    facs, end, inputs = facilities()
    scenes = scene_data()
    fetch_failed = json.loads((SCENES_V1 / "INDEX.json").read_text())["failed"]
    cands = {c["mars_source"] for c in json.loads((DERIVED / "join_v1.json").read_text())["candidates"]}
    gwp = {"gwp100": GWP["gwp100_fossil_ch4"]["value"], "gwp20": GWP["gwp20_fossil_ch4"]["value"]}
    rows = []
    for f in facs:
        if not f["passes"] or not f["start"]:
            rows.append({"name": f["name"], "country": f["country"], "cmSources": len(f["cm_sources"]),
                         "evaluated": False,
                         "why": ("no Carbon Mapper source owned (join rule 3: each source counts for its nearest "
                                 "target only)") if not f["passes"] else "no MARS plume row"})
            continue
        after = [p for p in f["passes"] if p["date"] > f["start"]]
        eps, stats = episodes(f["passes"], mars_dates=f["mars_dates"], start=f["start"], l2=f["l2_dates"])
        doc, scene_ok = scenes.get(f["name"]), None
        in_scope = f["name"] in cands or f["name"] in CASES
        if doc is not None:
            if doc["claims"] != [e["date"] for e in eps] or not in_scope:
                raise ValueError(f"scenes_v1/{f['name']}.json was derived for claims {doc['claims']}; the backtest "
                                 f"opens {[e['date'] for e in eps]} (in scope: {in_scope}): re-run scene_records.py")
            scene_ok = {e["date"]: claim_scene_checks(doc, e, mars_ts=f["mars_ts"]) for e in eps}
        elif in_scope and eps and f["name"] not in fetch_failed:
            raise ValueError(f"{f['name']}: in the scene-fetch scope with {len(eps)} claims but no scenes_v1 file")
        claims = {v: walk_claims(eps, f["passes"], mars_dates=f["mars_dates"], end=end,
                                 scene_ok=scene_ok, variant=v) for v in ("qualified", "upper_bound")}
        near = [p for p in (doc or {}).get("nearPlumes", []) if p["gas"] == "CH4" and p["sceneAt"]]
        hidden_own = sorted({_iso_day(p["sceneAt"]) for p in near if p["ownSource"] and p["rateHidden"]}
                            | {p["date"] for p in f["passes"] if p["detected"] and p["rate"] is None})
        claims["qualified_conservative"] = walk_claims(eps, f["passes"], mars_dates=f["mars_dates"], end=end,
                                                       scene_ok=scene_ok, variant="qualified",
                                                       extra_relapses=hidden_own)
        seen_plume = released_with_plume_seen(claims["qualified"], near)
        stake = band_from(len(after), sum(p["detected"] for p in after),
                          [r for d_, r in f["rates"] if d_ > f["start"]], gwp)
        rows.append({"name": f["name"], "country": f["country"], "cmSources": len(f["cm_sources"]),
                     "evaluated": True, "start": f["start"], "passesAfterStart": len(after),
                     "detectionsAfterStart": stats.get("detections", 0), "linked": stats["linked"],
                     "detectionsBelowMinOrUnquantified": stats["detections_below_min_or_unquantified"],
                     "episodes": eps, "rebatePolicyUnits": round(sum(e["rebatePolicyUnits"] for e in eps), 2),
                     "sceneData": doc is not None, "sceneFetchScope": in_scope,
                     "qualifiedSceneDatesOffTimeline": sorted(
                         {d_ for chk in (scene_ok or {}).values() for d_, v in chk.items() if v["ok"]}
                         - {p["date"] for p in f["passes"]}),
                     "claims": {v: _sum_inst(c) for v, c in claims.items()},
                     "releasedWithPlumeSeen": seen_plume,
                     "countedScenes": sorted({s for c in claims["qualified"] for x in c["countedPasses"]
                                             for s in x["scenes"]}),
                     "releasedByClaim": [{"episode": c["episode"], "units": round(sum(
                         i["amount"] for i in c["installments"] if i["state"] == "released"), 2)}
                         for c in claims["qualified"] if any(i["state"] == "released" for i in c["installments"])],
                     "lagFailingDates": sorted({d_ for chk in (scene_ok or {}).values() for d_, v in chk.items()
                                                if v["refusal"] == "BEFORE_PUBLICATION_LAG"}),
                     "claimsDetail": claims if f["name"] in CASES else None, "stake": stake})
    return {"rows": rows, "end": end, "inputs": inputs, "gwp": gwp}


def released_with_plume_seen(claims: list[dict], near: list[dict]) -> list[dict]:
    """Claims that released an installment although a CH4 plume with origin within 1.5 km of the facility was seen
    after `since` and by the last release: the clause does not count it as a relapse (only a quantified plume >=
    minRateKgH from an owned source is one). Each plume with its reason."""
    out = []
    for c in claims:
        rel = [i for i in c["installments"] if i["state"] == "released"]
        if not rel:
            continue
        last = max(i["claimableAt"] for i in rel)
        hits = []
        for p in near:
            day = _iso_day(p["sceneAt"])
            if not (c["since"] < day <= last):
                continue
            reason = ("a neighbour's source" if not p["ownSource"] else "rate hidden" if p["rateHidden"] else
                      f"below {CORE['minRateKgH']} kg/h" if p["rateKgH"] < CORE["minRateKgH"] else
                      "quantified, not in the backtest's plume snapshot")
            hits.append({"plumeId": p["plumeId"], "date": day, "ownSource": p["ownSource"], "rateKgH": p["rateKgH"],
                         "reason": reason})
        if hits:
            out.append({"episode": c["episode"], "since": c["since"], "lastRelease": last,
                        "releasedUnits": round(sum(i["amount"] for i in rel), 2), "plumes": hits})
    return out


def aggregate(rows: list[dict]) -> dict:
    ev = [r for r in rows if r["evaluated"]]
    eps = [e for r in ev for e in r["episodes"]]
    rates = sorted(e["rateKgH"] for e in eps)

    def q(p):
        return round(rates[min(len(rates) - 1, int(p * len(rates)))], 1) if rates else None

    out = {"facilitiesEvaluated": len(ev), "facilitiesWithEpisode": sum(1 for r in ev if r["episodes"]),
           "episodes": len(eps), "tierMix": dict(sorted(Counter(f"tier {e['tier']}" for e in eps).items())),
           "linkedDetections": sum(r["linked"] for r in ev),
           "detectionsBelowMinOrUnquantified": sum(r["detectionsBelowMinOrUnquantified"] for r in ev),
           "attestedKgH": {"p10": q(0.1), "median": q(0.5), "p90": q(0.9), "max": rates[-1] if rates else None},
           "rebatePolicyUnits": round(sum(r["rebatePolicyUnits"] for r in ev), 2),
           "L1Share": round(sum(e["L1"] for e in eps) / len(eps), 4) if eps else None,
           "L2Share": round(sum(e["L2"] for e in eps) / len(eps), 4) if eps else None,
           "L1Episodes": sum(e["L1"] for e in eps), "L2Episodes": sum(e["L2"] for e in eps)}
    for v in ("qualified", "upper_bound", "qualified_conservative"):
        s = [r["claims"][v] for r in ev]
        out[v] = {"claims": sum(x["claims"] for x in s), "claimsWithCountedPass": sum(x["claimsWithCountedPass"] for x in s),
                  "countedPasses": sum(x["countedPasses"] for x in s),
                  "installments": {k: sum(x["installments"][k] for x in s) for k in ("released", "cancelled", "pending")},
                  "refundPolicyUnits": {k: round(sum(x["refundPolicyUnits"][k] for x in s), 2)
                                        for k in ("released", "cancelled", "pending")},
                  "finalBlocked": dict(sum((Counter(x["finalBlocked"]) for x in s), Counter())),
                  "passesRefused": dict(sorted(sum((Counter(x["refused"]) for x in s), Counter()).items()))}
    out["upper_bound"]["label"] = "cloud and footprint unchecked; not an estimate; JSON only, never quoted"
    out["qualified_conservative"]["label"] = (
        "the qualified variant, plus: a CH4 plume with a hidden rate from one of the facility's own sources within "
        "1.5 km (today's catalogue, or a detection without a rate in the plume snapshot) after since is a relapse; "
        "JSON only, not on the card")
    with_scenes = [r for r in ev if r["sceneData"]]
    no_scene = Counter("in the fetch scope, fetch failed" if r["sceneFetchScope"] else
                       "outside the fetch scope (not a join candidate or demo case): scene data not fetched"
                       for r in ev if not r["sceneData"] for _ in r["episodes"])
    out["qualified"] |= {
        "label": f"{QUALIFIED_RULES}; counted only at facilities with committed scene data (data/derived/scenes_v1/: "
                 "the join candidates and the three demo cases that open a claim); elsewhere not fetched, not counted",
        "facilitiesWithSceneData": len(with_scenes),
        "claimsWithSceneData": sum(len(r["episodes"]) for r in with_scenes),
        "claimsWithoutSceneData": dict(sorted(no_scene.items())),
    }
    out["L1Label"] = "cadence badge (another Carbon Mapper instrument within +/- 7 days, any rate), not corroboration"
    out["L2Label"] = ("a MARS plume from a satellite other than EMIT or Tanager within 7 days; MARS rows on the start "
                      "date excluded")
    return out


def stake_total(rows: list[dict], gwp: dict) -> dict:
    from floors import GWP

    q = [r["stake"] for r in rows if r["evaluated"] and r["stake"]["status"] == "quantified"]
    refused = Counter(r["stake"]["reason"] for r in rows if r["evaluated"] and r["stake"]["status"] != "quantified")
    kt = {k: round(sum(s["kt_raw"][k] for s in q), 2) for k in ("low", "mid", "high")}
    full = [s for s in q if s["duty_cycle"] == 1.0]
    return {"label": "stake, not abatement: a sum of per-source v1 bands (not a joint interval); only the low bound "
                     "is quoted",
            "midHighLabel": "mid and high: sum of per-facility bounds, not a quantile; not quoted",
            "facilitiesQuantified": len(q), "facilitiesNotQuantified": dict(refused),
            "dutyCycleConcentration": {"facilitiesWithDutyOne": len(full),
                                       "shareOfMid": round(sum(s["kt_raw"]["mid"] for s in full)
                                                           / sum(s["kt_raw"]["mid"] for s in q), 4) if q else None},
            "taskingBias": "passes are partly tasked toward expected plumes, so the duty cycle seen on passes is "
                           "biased upward; the low bound uses the duty cycle's 2.5 % lower limit and the P10 rate",
            "kt_ch4_per_yr": kt,
            "co2e": {k: {"factor": g, "kt_co2e_per_yr": {b: round(v * g, 1) for b, v in kt.items()}}
                     for k, g in gwp.items()},
            "gwpSources": "pipeline/floors.py (" + "; ".join(f"{n} {GWP[k]['status']}" for n, k in (
                ("GWP-100", "gwp100_fossil_ch4"), ("GWP-20", "gwp20_fossil_ch4"))) + ")",
            "method": "per facility: duty cycle (Clopper-Pearson 95 %) x P10 / median / P90 plume rate x 8,760 h over "
                      "the passes after its first MARS plume date"}


def scene_summary(rows: list[dict]) -> dict:
    """Where the qualified variant has scene data, and the same totals over the three demo cases alone (the scope the
    qualified figure had before the join-candidate fetch)."""
    index = json.loads((SCENES_V1 / "INDEX.json").read_text())
    return {"source": "data/derived/scenes_v1/ (pipeline/scene_records.py; raw responses in data/raw/, untracked)",
            "scope": index["scope"], "facilities": index["written"], "fetchFailed": index["failed"],
            "claims": index["claims"], "fetch": index["fetch"],
            "elsewhere": "unqualified — scene data not fetched",
            "qualifiedSceneDatesOffTimeline": {
                "count": sum(len(r.get("qualifiedSceneDatesOffTimeline", [])) for r in rows),
                "note": "dates with a scene that qualifies for some claim but no pass in the facility's observation "
                        "timeline (cm_timelines.json); the walk iterates the timeline, so they are not counted"},
            "demoCasesOnly": {k: v for k, v in aggregate([r for r in rows if r["name"] in CASES])["qualified"].items()
                              if k in ("claims", "claimsWithCountedPass", "countedPasses", "installments",
                                       "refundPolicyUnits")},
            **scene_stats(rows)}


def scene_stats(rows: list[dict]) -> dict:
    """Released-while-a-plume-was-seen, concentration, the publication-lag counts and the country mix of the
    qualified variant (spec-v2 §15 B7 review), all from the walk."""
    sc = [r for r in rows if r.get("sceneData")]
    seen = [{"facility": r["name"], **c} for r in sc for c in r["releasedWithPlumeSeen"]]
    reasons = Counter(p["reason"] for c in seen for p in c["plumes"])
    rel = sorted(({"facility": r["name"], **c} for r in sc for c in r["releasedByClaim"]),
                 key=lambda x: (-x["units"], x["facility"], x["episode"]))
    total = round(sum(x["units"] for x in rel), 2)
    top = rel[:3]
    fac_by_scene: dict[str, set] = defaultdict(set)
    for r in sc:
        for s in r["countedScenes"]:
            fac_by_scene[s].add(r["name"])
    lag_refusals = sum(r["claims"]["qualified"]["refused"].get("scene_refused:BEFORE_PUBLICATION_LAG", 0) for r in sc)
    cands = json.loads((DERIVED / "join_v1.json").read_text())["candidates"]
    by_country: dict[str, dict] = {}
    for r in sc:
        q = r["claims"]["qualified"]
        c = by_country.setdefault(r["country"], {"facilitiesWithSceneData": 0, "claims": 0, "claimsWithCountedPass": 0,
                                                 "countedPasses": 0, "releasedPolicyUnits": 0.0,
                                                 "joinCandidates": sum(1 for x in cands if x["country"] == r["country"])})
        c["facilitiesWithSceneData"] += 1
        c["claims"] += q["claims"]
        c["claimsWithCountedPass"] += q["claimsWithCountedPass"]
        c["countedPasses"] += q["countedPasses"]
        c["releasedPolicyUnits"] = round(c["releasedPolicyUnits"] + q["refundPolicyUnits"]["released"], 2)
    lead = max(by_country, key=lambda k: (by_country[k]["countedPasses"], k))
    return {
        "releasedWithPlumeSeen": {
            "claims": len(seen), "releasedPolicyUnits": round(sum(c["releasedUnits"] for c in seen), 2),
            "byReason": dict(sorted(reasons.items())),
            "note": "released although a CH4 plume with origin within 1.5 km was seen after since and by the last "
                    "release; the clause does not count it as a relapse (only a quantified plume >= 100 kg/h from an "
                    "owned source is one). See qualified_conservative for the totals when an own source's plume with "
                    "a hidden rate is a relapse",
            "list": seen},
        "concentration": {
            "releasedPolicyUnits": total,
            "topClaims": top, "topClaimsShareOfReleased": round(sum(x["units"] for x in top) / total, 4) if total else None,
            "countedScenesDistinct": len(fac_by_scene),
            "scenesCountedAtTwoOrMoreFacilities": sum(1 for v in fac_by_scene.values() if len(v) >= 2),
            "note": "one wide EMIT or Tanager scene can cover two facilities; each facility counts it for its own claim"},
        "publicationLag": {
            "claimByPassRefusals": lag_refusals,
            "facilityDateChecksFailingLag": sum(len(r["lagFailingDates"]) for r in sc),
            "note": "claim-by-pass refusals = timeline passes the walk refused BEFORE_PUBLICATION_LAG for a claim; "
                    "(facility, date) checks = scene dates whose first refusal is the 60-day lag (not yet run at the "
                    "fetch time) for at least one claim"},
        "byCountry": dict(sorted(by_country.items())),
        "byCountryNote": (f"the country mix follows the join selection: {by_country[lead]['joinCandidates']} of "
                          f"{len(cands)} join candidates are in {lead}, the country with the most counted passes"),
    }


def economic_sentence() -> str:
    """The IEA's abatement-cost scale (IEA's term), generated from data/derived/projection_sources.json (`costScale`
    and its [V] source; that file belongs to the projection and is only read here). Fails closed if the source is not
    read at the URL ([V]) or the value is not in the quoted text. Tier amounts stay policy units: not converted."""
    ps = json.loads((DERIVED / "projection_sources.json").read_text())
    cs = ps["parameters"]["costScale"]
    (sid,) = cs["sources"]
    src = ps["sources"][sid]
    quote = next(q for q in src["quotes"] if f"USD {cs['usdPerTco2e']} per" in q)
    if src["tag"] != "[V]" or cs["status"] != "read":
        raise ValueError(f"projection_sources.json {sid}: not a read primary source ({src['tag']}, {cs['status']})")
    return (f"Context, not a comparison: the IEA writes that '{quote.rstrip('.')}' ({src['title']}, {src['url']}, "
            f"read {src['readOn']}). The clause's tier amounts are policy units and are not converted to a currency.")


WORDS = {1: "one", 2: "two", 3: "three"}
REASON_ORDER = ("rate hidden", f"below {CORE['minRateKgH']} kg/h", "a neighbour's source",
                "quantified, not in the backtest's plume snapshot")


def refunds_sentence(out: dict) -> str:
    """The refunds card sentence (B7 review wording), every number and the join facts generated: the second selection
    (the join candidates: a Tanager plume within the radius inside the join window) is named beside the first."""
    q, sd = out["totals"]["qualified"], out["scene_data"]
    j = json.loads((DERIVED / "join_v1.json").read_text())["summary"]
    if j["candidates_with_tanager"] != j["n_candidates"]:
        raise ValueError("join_v1: not every candidate has a Tanager plume; reword the selection")
    w0, w1 = (date.fromisoformat(x) for x in j["window"])
    window = f"{w0:%b}–{w1:%b %Y}" if w0.year == w1.year else f"{w0:%b %Y}–{w1:%b %Y}"
    demo_extra = sum(1 for c in CASES if c not in {x["mars_source"] for x in
                                                   json.loads((DERIVED / "join_v1.json").read_text())["candidates"]})
    seen = sd["releasedWithPlumeSeen"]
    reasons = ", or ".join(r for r in REASON_ORDER if r in seen["byReason"])
    return (
        f"Counterfactual (no clause existed): refunds count only {QUALIFIED_RULES}. Scene data covers "
        f"{q['facilitiesWithSceneData']:,} facilities (the join candidates, which "
        f"had a Tanager plume within {j['radius_km']:g} km in {window} and open a claim, plus "
        f"{WORDS.get(demo_extra, demo_extra)} demo cases, among {SELECTION}). Of their {q['claimsWithSceneData']:,} "
        f"claims, {q['claimsWithCountedPass']:,} had a counted pass ({q['countedPasses']:,} passes on "
        f"{sd['concentration']['countedScenesDistinct']:,} scenes): installments {q['installments']['released']:,} "
        f"released, {q['installments']['cancelled']:,} cancelled, {q['installments']['pending']:,} pending "
        f"({q['refundPolicyUnits']['released']:,.2f} / {q['refundPolicyUnits']['cancelled']:,.2f} / "
        f"{q['refundPolicyUnits']['pending']:,.2f} policy units); {seen['claims']:,} released claims "
        f"({seen['releasedPolicyUnits']:,.2f} units) had a plume at the facility that the clause does not count as a "
        f"relapse ({reasons}). The other {q['claims'] - q['claimsWithSceneData']:,} claims were not fetched and count "
        "nothing.")


def refunds_short_sentence(out: dict) -> str:
    """The README's form of `refundsSentence` (README.md has a 25 KB budget); the full sentence stays in this file, the
    Devpost draft and the PDF. Same numbers, same two selections, generated."""
    q, sd = out["totals"]["qualified"], out["scene_data"]
    seen = sd["releasedWithPlumeSeen"]
    return (f"Counterfactual, qualified passes only, at the {q['facilitiesWithSceneData']:,} join-candidate and demo "
            f"facilities, among {SELECTION}: {q['claimsWithCountedPass']:,} of {q['claimsWithSceneData']:,} claims "
            f"had a counted pass; {q['installments']['released']:,} installments released "
            f"({q['refundPolicyUnits']['released']:,.2f} policy units), {q['installments']['cancelled']:,} cancelled; "
            f"{seen['claims']:,} released claims saw a plume not counted as a relapse; "
            f"{q['claims'] - q['claimsWithSceneData']:,} claims not fetched.")


def card(out: dict) -> dict:
    """The sentences that may be quoted (after the adversarial review of the backtest), generated from the numbers;
    selection named in each."""
    t, sb, tg = out["totals"], out["stake_band"], out["targets"]
    tiers = ", ".join(f"{k} {v:,}" for k, v in t["tierMix"].items())
    q = t["qualified"]
    low_mt = sb["kt_ch4_per_yr"]["low"] / 1000
    ev = [r for r in out["facilities"] if r["evaluated"]]
    quant = {r["name"] for r in ev if r["stake"]["status"] == "quantified"}
    with_ep = {r["name"] for r in ev if r["episodes"]}
    if not with_ep <= quant or len(quant) != sb["facilitiesQuantified"] or len(with_ep) != t["facilitiesWithEpisode"]:
        raise ValueError("stake facilities are not the episode facilities plus sub-minimum ones; reword stakeSentence")
    below = quant - with_ep  # a quantified rate, but every detection under the episode minimum
    if any(r["stake"]["rate_kgh"]["max"] >= CORE["minRateKgH"] for r in ev if r["name"] in below):
        raise ValueError("a stake facility without an episode has a plume at or above the minimum; reword stakeSentence")
    return {
        "episodesSentence": (
            f"Counterfactual walk over the {tg['evaluated']:,} sources that own a Carbon Mapper source (of {SELECTION}), "
            f"from the first MARS plume's tile date (a backdated start) to the catalogue end ({out['catalogueEnd']}), "
            "policy frozen: "
            f"{t['episodes']:,} episodes at {t['facilitiesWithEpisode']:,} facilities ({tiers}); "
            f"{t['detectionsBelowMinOrUnquantified']:,} detections refused (below 100 kg/h or no rate) and "
            f"{t['linkedDetections']:,} linked; rebates {t['rebatePolicyUnits']:,.2f} policy units (a parameter, not "
            "a price)."),
        "refundsSentence": refunds_sentence(out),
        "refundsShortSentence": refunds_short_sentence(out),
        "stakeSentence": (
            f"Stake, not abatement: at least ~{low_mt:.1f} Mt CH₄/yr across {sb['facilitiesQuantified']:,} "
            f"facilities (of {SELECTION}), the {len(with_ep):,} with an episode plus {len(below):,} whose plumes all "
            f"fell below {CORE['minRateKgH']} kg/h, assuming what satellites saw on their passes holds between them; "
            "passes are partly tasked toward expected plumes, which biases the seen duty cycle upward."),
        "l2Sentence": (
            f"{round(t['L2Share'] * 100)} % of episodes had a MARS plume from a satellite other than EMIT or Tanager "
            f"within 7 days ({t['L2Episodes']:,} of {t['episodes']:,}; over {SELECTION}; MARS rows on the start date "
            "excluded)."),
        "l1Sentence": (
            f"L1 is a cadence badge, not corroboration quality: {t['L1Share']:.1%} of episodes had another Carbon "
            f"Mapper instrument detect within ±7 days, over {SELECTION}."),
    }


def main() -> None:
    from schemas_v2 import validate

    r = run()
    rows, end = r["rows"], r["end"]
    by_country = {}
    for c in sorted({x["country"] for x in rows}):
        by_country[c] = aggregate([x for x in rows if x["country"] == c])
    exp = [x for x in rows if x["country"] == EXPORTER["country"]]
    sb = stake_total(rows, r["gwp"])
    for x in rows:
        if x.get("stake"):
            x["stake"].pop("kt_raw", None)
    out = {
        "schema": "methaneclause.backtest/1",
        "label": LABEL + "; amounts in policy units, never a currency",
        "definition": __doc__.split("Definition (also written into the output as `definition`):")[1]
        .split("    cd pipeline")[0].strip(),
        "policy": "POLICY_V2_VERDICT_ONLY", "policyHash": policy_hash(POLICY_V2_VERDICT_ONLY),
        "generatedFrom": [{"path": p, "sha256": h} for p, h in r["inputs"].items()]
        + [{"path": str(q.relative_to(ROOT)), "sha256": sha256_file(q)}
           for q in (SCENES_V1 / "INDEX.json", DERIVED / "projection_sources.json",
                     ROOT / "pipeline" / "cleanpass_rules.py", Path(__file__).resolve())],
        "catalogueEnd": end,
        "amounts": {"unit": "policy units",
                    "tierAmountsPolicyUnits": [x / 100 for x in POOL["unitTierAmounts"]],
                    "note": "tier amounts are a parameter of the supply contract, not a price"},
        "targets": {"mars_targets": len(rows), "with_cm_source": sum(1 for x in rows if x["cmSources"]),
                    "evaluated": sum(1 for x in rows if x["evaluated"])},
        "selection": SELECTION,
        "variants": {"qualified": "spec-v2 §2 rules 1-6 (cleanpass_rules.qualify) on the committed scene records "
                                  "(data/derived/scenes_v1/), keyed by claim, rule 2 without look-ahead, counted at the "
                                  "scene's attestable time + cleanWindow; facilities outside the fetch scope are "
                                  "unqualified — scene data not fetched",
                     "upper_bound": "every detection-free pass with a floored instrument and no MARS plume (any MARS "
                                    "row) within +/- 7 days; cloud and footprint unchecked; not an estimate"},
        "totals": aggregate(rows),
        "by_country": by_country,
        "exporter_table": {**EXPORTER, "label": "importer assignment [SIM]: Algerian pipeline gas reaches Italy and "
                                                "Spain; parties are simulated",
                           "totals": aggregate(exp),
                           "rows": [{k: x.get(k) for k in ("name", "evaluated", "start", "passesAfterStart",
                                                           "rebatePolicyUnits", "claims", "why")}
                                    | {"episodes": len(x.get("episodes", []))} for x in exp]},
        "stake_band": sb,
        "scene_data": scene_summary(rows),
        "economicSentence": economic_sentence(),
        "facilities": rows,
    }
    out["card"] = card(out)
    for x in rows:
        x.pop("countedScenes", None)  # used for the concentration figures above; not written per facility
    validate(out)
    write_json(DERIVED / "backtest.json", out)
    for k, v in out["card"].items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
