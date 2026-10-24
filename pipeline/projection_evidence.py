"""Two empirical checks on "the share of an answered source's emissions that stops" (pipeline/projection.py).

Both use committed files only and both are observational: neither is a measured effect of the clause.

Line 1 — MARS outcome by response. MARS documents a per-source `emission_status` (Ongoing / Potentially ceased / ...),
but neither the committed snapshot nor the current public file carries the field (recorded in
projection_sources.json), so the outcome is the labelled fallback: `persistency_category == "absent"`, i.e. no MARS
detection over the last six months among the high-quality observations — a non-detection label, never a confirmed
stop. Among notified oil and gas sources with a determined category: the share "absent" for feedback = Yes vs No,
Wilson intervals, the Newcombe interval of the difference, and a Mantel-Haenszel difference over plume-count strata
(responders have more plumes, so the crude comparison mixes size and response).

Line 2 — event study around notification. MARS publishes no notification date; the tile date of a source's first
notified plume (data/derived/mars_notified.json) is the proxy (the first plume date is a variant). For each notified
oil and gas source with feedback Yes or No whose Carbon Mapper sources (nearest notified MARS source within 1.5 km)
all have a committed timeline: Carbon Mapper per-pass detection rate in [T0 - window, T0) and (T0 + gap,
T0 + gap + window]; difference-in-differences responders minus non-responders; bootstrap interval over sources.
Every variant run is reported, not only the primary.

Two coverage sets: the committed join-neighbourhood timelines (cm_timelines.json: the neighbourhood of the sources
MARS labels absent today, a label decided after the window) and the same plus cm_timelines_notified.json (every
notified source with feedback Yes or No; fetch_cm_notified.py), which removes that conditioning.

Selection, named in the output: feedback is tracked only for sources whose latest plume is from November 2024 or
later (MARS definitions); Carbon Mapper passes are partly tasked toward expected plumes; T0 is a proxy; responders
start from a higher detection rate, so regression to the mean pushes the difference toward a drop.
"""
from __future__ import annotations

import random
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import NormalDist

import numpy as np

GROUPS = ("Yes", "No")


def _z(conf: float) -> float:
    return NormalDist().inv_cdf((1 + conf) / 2)


def wilson(k: int, n: int, conf: float) -> tuple[float, float]:
    z = _z(conf)
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** (1 / 2)) / den
    return mid - half, mid + half


def newcombe(k1: int, n1: int, k2: int, n2: int, conf: float) -> tuple[float, float]:
    """Newcombe's hybrid score interval for p1 - p2 (method 10)."""
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1, conf)
    l2, u2 = wilson(k2, n2, conf)
    d = p1 - p2
    return (d - ((p1 - l1) ** 2 + (u2 - p2) ** 2) ** (1 / 2), d + ((u1 - p1) ** 2 + (p2 - l2) ** 2) ** (1 / 2))


def _stratum(n_plumes: int, edges: list[int]) -> str:
    lo = max(e for e in edges if e <= n_plumes)
    nxt = [e for e in edges if e > lo]
    return f"{lo}+" if not nxt else (f"{lo}" if nxt[0] - lo == 1 else f"{lo}-{nxt[0] - 1}")


def _mh(table: dict[str, dict[str, tuple[int, int]]]) -> float:
    """Mantel-Haenszel risk difference over strata; table[stratum][group] = (k, n)."""
    num = den = 0.0
    for t in table.values():
        (k1, n1), (k0, n0) = t["Yes"], t["No"]
        if n1 and n0:
            w = n1 * n0 / (n1 + n0)
            num += w * (k1 / n1 - k0 / n0)
            den += w
    return num / den


def line1(mars: list[dict], ev: dict) -> dict:
    conf, edges = ev["confidence"], ev["plumeStrata"]
    cat = ev["statusProxy"]["absentCategory"]
    og = [r for r in mars if r["sector"] == "Oil and Gas" and r["notified"] == "Yes" and r["feedback"] in GROUPS]
    det = [r for r in og if r["persistency_category"] != "Undetermined"]
    groups = {}
    for g in GROUPS:
        rows = [r for r in det if r["feedback"] == g]
        k = sum(r["persistency_category"] == cat for r in rows)
        lo, hi = wilson(k, len(rows), conf)
        total = sum(r["feedback"] == g for r in og)
        groups[g] = {"sources": total, "determined": len(rows), "undeterminedLeftOut": total - len(rows), "absent": k,
                     "share": round(k / len(rows), 4), "ci": [round(lo, 4), round(hi, 4)]}
    y, n = groups["Yes"], groups["No"]
    d_lo, d_hi = newcombe(y["absent"], y["determined"], n["absent"], n["determined"], conf)
    table: dict[str, dict[str, tuple[int, int]]] = defaultdict(dict)
    rows_by: dict[tuple[str, str], list[int]] = defaultdict(list)
    for r in det:
        s = _stratum(int(r["n_plumes_detected"]), edges)
        rows_by[(s, r["feedback"])].append(int(r["persistency_category"] == cat))
    strata = sorted({s for s, _ in rows_by}, key=lambda s: int(s.split("-")[0].rstrip("+")))
    for s in strata:
        for g in GROUPS:
            v = rows_by.get((s, g), [])
            table[s][g] = (sum(v), len(v))
    mh = _mh(table)
    rng = random.Random(ev["seed"])
    draws = []
    for _ in range(ev["bootstrapDraws"]):
        bt = {s: {g: (lambda v: (sum(v), len(v)))([rng.choice(rows_by[(s, g)]) for _ in rows_by[(s, g)]])
                  if rows_by.get((s, g)) else (0, 0) for g in GROUPS} for s in strata}
        draws.append(_mh(bt))
    lo_q, hi_q = np.quantile(draws, [(1 - conf) / 2, (1 + conf) / 2])
    return {
        "outcome": f"persistency_category == '{cat}' (no MARS detection over the last six months; a non-detection "
                   "label, not a confirmed stop); emission_status is documented by MARS but absent from the data",
        "population": "notified oil and gas sources with feedback Yes (operator or government) or No and a determined "
                      "persistency category ('Undetermined' left out, counted per group)",
        "byFeedback": groups,
        "difference": {"yesMinusNo": round(y["share"] - n["share"], 4), "ci": [round(d_lo, 4), round(d_hi, 4)]},
        "byPlumeStratum": {s: {g: {"absent": table[s][g][0], "determined": table[s][g][1]} for g in GROUPS}
                           for s in strata},
        "mantelHaenszelDifference": {"yesMinusNo": round(mh, 4), "ci": [round(float(lo_q), 4), round(float(hi_q), 4)],
                                     "ciMethod": "bootstrap over sources within group and stratum"},
        "confidence": conf,
    }


def facilities_for_event_study(mars: list[dict], cm_sources: list[dict], timelines: dict, radius_km: float) -> dict:
    """{mars source: {"feedback", "cm": [names], "complete": all owned CM sources have a timeline}} for notified oil and
    gas sources with feedback Yes or No; ownership = nearest notified oil and gas MARS source within radius_km."""
    import pandas as pd
    from join.join import nearest

    nt = pd.DataFrame([r for r in mars if r["sector"] == "Oil and Gas" and r["notified"] == "Yes"])
    nt = nt.assign(lat=nt["lat"].astype(float), lon=nt["lon"].astype(float)).sort_values("source_name")
    nt = nt.reset_index(drop=True)
    lat = np.array([float(c["source_latitude"]) for c in cm_sources])
    lon = np.array([float(c["source_longitude"]) for c in cm_sources])
    idx, dist = nearest(nt, lat, lon)
    owned: dict[int, list[str]] = defaultdict(list)
    for c, i, d in zip(cm_sources, idx, dist):
        if d <= radius_km:
            owned[int(i)].append(c["source_name"])
    out = {}
    for i, r in nt.iterrows():
        if r["feedback"] in GROUPS:
            names = sorted(owned.get(i, []))
            out[r["source_name"]] = {"feedback": r["feedback"], "cm": names,
                                     "complete": bool(names) and all(n in timelines for n in names)}
    return out


def _rates(cm: list[str], timelines: dict, t0: date, window: int, gap: int) -> tuple[list[bool], list[bool]]:
    obs, det = set(), set()
    for n in cm:
        obs |= {o[0] for o in timelines[n]["obs"]}
        det |= set(timelines[n]["det"])
    pre = [d in det for d in sorted(obs) if t0 - timedelta(window) <= date.fromisoformat(d) < t0]
    post = [d in det for d in sorted(obs) if t0 + timedelta(gap) < date.fromisoformat(d) <= t0 + timedelta(gap + window)]
    return pre, post


def did_run(facs: dict, t0s: dict[str, str], timelines: dict, spec: dict, ev: dict, *, same_t0: set[str],
            catalogue_end: str) -> dict:
    """One specification. Besides the estimate, per group: how many used sources have T0 before MARS's feedback
    tracking began (`t0BeforeFeedbackTracking`), whose post window runs past the catalogue end
    (`postWindowTruncated`), and whose two proxy dates agree (`proxyDatesAgree`)."""
    funnel: Counter = Counter()
    pairs: dict[str, list[tuple[float, float]]] = {g: [] for g in GROUPS}
    flags: dict[str, Counter] = {g: Counter() for g in GROUPS}
    tracking, end = date.fromisoformat(ev["feedbackTrackingStart"]), date.fromisoformat(catalogue_end)
    for name, f in sorted(facs.items()):
        g = f["feedback"]
        if not f["cm"]:
            funnel[f"{g}: no Carbon Mapper source within the radius"] += 1
            continue
        if not f["complete"]:
            funnel[f"{g}: an owned Carbon Mapper source has no committed timeline"] += 1
            continue
        t0 = date.fromisoformat(t0s[name])
        pre, post = _rates(f["cm"], timelines, t0, spec["windowDays"], spec["gapDays"])
        if min(len(pre), len(post)) < spec["minPassesEachSide"]:
            funnel[f"{g}: fewer passes than the minimum on a side"] += 1
            continue
        funnel[f"{g}: used"] += 1
        pairs[g].append((sum(pre) / len(pre), sum(post) / len(post)))
        flags[g]["t0BeforeFeedbackTracking"] += t0 < tracking
        flags[g]["postWindowTruncated"] += t0 + timedelta(spec["gapDays"] + spec["windowDays"]) > end
        flags[g]["proxyDatesAgree"] += name in same_t0
    out = {"spec": spec, "funnel": dict(sorted(funnel.items())),
           "groups": {g: {"sources": len(v),
                          "preRate": round(statistics.mean(a for a, _ in v), 4) if v else None,
                          "postRate": round(statistics.mean(b for _, b in v), 4) if v else None,
                          **{k: flags[g][k] for k in ("t0BeforeFeedbackTracking", "postWindowTruncated",
                                                      "proxyDatesAgree")}}
                      for g, v in pairs.items()}}
    if min(len(v) for v in pairs.values()) < ev["minSourcesPerGroup"]:
        out["did"] = None
        out["note"] = "too few sources in a group for an estimate"
        return out

    def did(y, n):
        return statistics.mean(b - a for a, b in y) - statistics.mean(b - a for a, b in n)

    rng = random.Random(ev["seed"])
    y, n = pairs["Yes"], pairs["No"]
    draws = [did([rng.choice(y) for _ in y], [rng.choice(n) for _ in n]) for _ in range(ev["bootstrapDraws"])]
    lo, hi = np.quantile(draws, [(1 - ev["confidence"]) / 2, (1 + ev["confidence"]) / 2])
    se = statistics.stdev(draws)
    mde = (_z(ev["confidence"]) + NormalDist().inv_cdf(ev["power"])) * se
    out["did"] = {"yesMinusNo": round(did(y, n), 4), "ci": [round(float(lo), 4), round(float(hi), 4)],
                  "bootstrapSe": round(se, 4), "minimumDetectableEffect": round(mde, 4),
                  "minimumDetectableShareOfPreRate": round(mde / out["groups"]["Yes"]["preRate"], 4)}
    return out


THREATS = [
    "T0 is a proxy (the first notified plume), not the notification or the response date; for the sources used, "
    "`proxyDatesAgree` counts where the first plume is the first notified one",
    "feedback is tracked only for sources whose latest plume is from November 2024 or later (MARS definitions); "
    "`t0BeforeFeedbackTracking` counts used sources whose T0 is earlier",
    "post windows that run past the catalogue end are shorter (`postWindowTruncated`), and more so for one group",
    "Carbon Mapper passes are partly tasked toward expected plumes, more so after Tanager's launch: both groups' rates "
    "rise after T0",
    "responders start from a higher detection rate: regression to the mean pushes the difference toward a drop",
    "non-responders may repair without replying, and a reply may come after the window: both push the difference "
    "toward zero",
    "detection is binary above an instrument floor: a partial cut in emitted mass is invisible to it, so the design "
    "cannot bound a mass share",
]


def line2(mars: list[dict], notified: dict, mars_plumes: list[dict], cm_sources: list[dict],
          coverage: dict[str, tuple[str, dict]], ev: dict, radius_km: float) -> dict:
    """`coverage` = {name: (description, timelines)}; every variant runs on every coverage set."""
    es = ev["eventStudy"]
    first: dict[str, str] = {}
    for p in mars_plumes:
        d = p["tile_date"].split("T")[0]
        first[p["source_name"]] = min(first.get(p["source_name"], d), d)
    proxies = {"firstNotifiedPlume": {k: v["firstNotified"] for k, v in notified.items()}, "firstPlume": first}
    same = {k for k, v in proxies["firstNotifiedPlume"].items() if proxies["firstPlume"].get(k) == v}
    sets = {}
    for name, (desc, tl) in coverage.items():
        facs = facilities_for_event_study(mars, cm_sources, tl, radius_km)
        undated = sorted(set(facs) - set(proxies["firstNotifiedPlume"]))
        if undated:
            raise ValueError(f"{len(undated)} notified sources without a first notified plume (first: {undated[0]}): "
                             "run make mars-notified")
        end = max(o[0] for t in tl.values() for o in t["obs"])
        runs = [did_run(facs, proxies[s["proxy"]], tl, s, ev, same_t0=same, catalogue_end=end)
                for s in [es["primary"], *es["variants"]]]
        ests = [r["did"]["yesMinusNo"] for r in runs if r["did"]]
        sets[name] = {"coverage": desc, "catalogueEnd": end, "primary": runs[0], "variants": runs[1:],
                      "rangeOfEstimates": [min(ests), max(ests)] if ests else None,
                      "allCiIncludeZero": all(r["did"]["ci"][0] <= 0 <= r["did"]["ci"][1] for r in runs if r["did"])}
    return {
        "outcome": "Carbon Mapper per-pass detection rate (a pass with any plume of the owned sources counts as a "
                   "detection), mean over sources, before vs after the proxy notification date",
        "population": "notified oil and gas MARS sources with feedback Yes (operator or government) or No; Carbon "
                      "Mapper sources owned by the nearest notified source within the join radius",
        "coverageSets": sets,
        "mdeNote": "minimum detectable effect = (z at the confidence level + z at the power) x the bootstrap standard "
                   "error of the difference; it is a detection-rate difference and says nothing about a partial cut "
                   "in emitted mass",
        "threats": THREATS,
    }
