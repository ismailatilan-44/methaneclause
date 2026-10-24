"""The projection's time factor (boards T4c and T4d; pre-registered in data/derived/projection_sources.json ->
model.t4c and model.t4d). T4c (held by its review) is kept for the reviewer's report-only rows; T4d is at the end of
this file: removed time in absolute days against the stake's own emitting days, every event censored at the facility's
last pass, a fix credited only until a new fault would start (the re-onset hazard measured on the stake's own passes),
and the action share conditioned on CARB plume count. Nothing of either is published while
model.timeFactor.headlineKind is 'range'.

T4c, as reviewed:

    imported by pipeline/projection.py (offline, committed inputs only)

Of a facility's emitting time over its window, T is the share a fix would remove. Events follow Cusworth et al. 2026,
applied to the stake's own passes (the passes after the facility's start that its duty cycle counts): a detection after
a non-detection (or at the first pass) starts an event, a non-detection after a detection ends it. Each event has three
duration conventions (shortest, central = midpoints, longest). A fixable event is cut at its first detection + Carbon
Mapper's publication lag of that detection + the seller's response delay; the response delays are every CARB oil and gas
Type A row's notification -> mitigation days (the whole distribution, negatives counted as 0):

    T = sum_e mean_j max(0, end_e - (f_e + p_e + d_j)) / sum_e (end_e - start_e)

This file types no parameter value: the minimum event duration is read from Cusworth's quote, the delays from the
committed CARB aggregates (carb_summary.json), the lags from cm_plumes.csv (tests/test_projection.py checks the literals).
"""
from __future__ import annotations

import csv
import re
import statistics
from collections import defaultdict
from datetime import date, datetime

from common import DERIVED, RADIUS_KM, haversine_km

CONVENTIONS = ("shortest", "central", "longest")
MIN_PER_DAY = 1440
SEC_PER_DAY = 86400
T_DIGITS = 4
UNIT_CONSTANTS = {0, 1, 2, 10, T_DIGITS, MIN_PER_DAY, SEC_PER_DAY}  # midpoint halves, deciles, digits, units


def _day(s: str) -> date:
    return date.fromisoformat(s[:10])


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def min_event_days(prm: dict) -> float:
    """Cusworth's minimum duration of a single-detection event, read from its quote (minutes -> days)."""
    ref = prm["model"]["timeFactor"]["minEventMinutes"]
    q = prm["sources"][ref["source"]]["quotes"][ref["quote"]]
    m = re.search(ref["regex"], q)
    if not m:
        raise ValueError(f"{ref['source']} quote {ref['quote']} no longer states the minimum event duration")
    return float(m.group(1)) / MIN_PER_DAY


def _acted_delays(cs: dict, prm: dict, key: str) -> list[int]:
    """One of the acted outcome's day-difference histograms {days: incidences} in the CARB aggregates
    (data/derived/carb_summary.json), expanded to one value per incidence in ascending order."""
    acted = prm["parameters"]["responseScenarios"]["rule"]["acted"]
    if acted not in cs["aggregates"]["delays"]:
        raise ValueError(f"carb_summary.json has no delays for {acted}: the time factor cannot be computed")
    hist = cs["aggregates"]["delays"][acted][key]
    return [int(d) for d in sorted(hist, key=int) for _ in range(hist[d])]


def response_delays(cs: dict, prm: dict) -> list[float]:
    """Every CARB Type A row with a notification and a mitigation date: mitigated - notified, days, negatives as 0."""
    out = [max(0, d) for d in _acted_delays(cs, prm, "notifiedToMitigatedDays")]
    if not out:
        raise ValueError("carb_summary.json has no Type A row with both dates: the time factor cannot be computed")
    return out


def carb_first_plume_delays(cs: dict, prm: dict) -> list[float]:
    """Variant t_carb: CARB's first plume -> mitigation (its own notification step in place of the publication lag)."""
    return [max(0, d) for d in _acted_delays(cs, prm, "earliestPlumeToMitigatedDays")]


def events(passes: list[dict], start: str, end: str) -> list[dict]:
    """Cusworth's events on one facility's passes after its start (date order): f/l = first/last detected pass,
    prev/next = the non-detected pass before f / after l (the start date / the catalogue end if none)."""
    after = sorted((p for p in passes if p["date"] > start), key=lambda p: p["date"])
    out, cur, prev = [], None, start
    for p in after:
        if p["detected"]:
            if cur is None:
                cur = {"f": p["date"], "l": p["date"], "prev": prev, "n": 1}
            else:
                cur["l"] = p["date"]
                cur["n"] += 1
        else:
            if cur is not None:
                cur["next"] = p["date"]
                out.append(cur)
                cur = None
            prev = p["date"]
    if cur is not None:
        cur["next"] = end
        out.append(cur)
    return out


def bounds(e: dict, convention: str, min_days: float) -> tuple[float, float]:
    """(start, end) of an event in days since 1970-01-01 under a duration convention."""
    f, l_, pv, nx = (_day(e[k]).toordinal() for k in ("f", "l", "prev", "next"))
    if convention == "shortest":
        return f, max(l_, f + min_days)
    if convention == "longest":
        return pv, nx
    if convention == "central":
        return (pv + f) / 2, (l_ + nx) / 2
    raise ValueError(f"unknown convention {convention!r}")


def removed_and_length(evs: list[dict], lags: dict[str, float], delays: list[float], convention: str,
                       min_days: float, use_lag: bool = True) -> tuple[float, float]:
    """(sum of W_e, sum of L_e) in days for one facility's events."""
    w = length = 0.0
    for e in evs:
        s, t = bounds(e, convention, min_days)
        cut = _day(e["f"]).toordinal() + (lags[e["f"]] if use_lag else 0)
        w += statistics.fmean(max(0.0, t - (cut + d)) for d in delays)
        length += t - s
    return w, length


def publication_lags(fac: dict) -> dict[str, float]:
    """Per detection day at the facility: the earliest published_at - scene_timestamp, in days (floored at 0), over the
    facility's own Carbon Mapper plumes within the join radius (the plumes that make its detections)."""
    out: dict[str, float] = {}
    for r in _plumes_by_source().get_many(fac["cm_sources"]):
        if not r["scene_timestamp"] or not r["published_at"]:
            continue
        if haversine_km(fac["lat"], fac["lon"], float(r["lat"]), float(r["lon"])) > RADIUS_KM:
            continue
        day = r["scene_timestamp"][:10]
        lag = max(0.0, (_ts(r["published_at"]) - _ts(r["scene_timestamp"])).total_seconds() / SEC_PER_DAY)
        out[day] = min(lag, out.get(day, lag))
    return out


class _Plumes:
    def __init__(self) -> None:
        self.by: dict[str, list[dict]] = defaultdict(list)
        seen = set()
        with open(DERIVED / "cm_plumes.csv", newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["plume_id"] in seen:
                    continue
                seen.add(r["plume_id"])
                self.by[r["source_name"]].append(r)

    def get_many(self, names: list[str]) -> list[dict]:
        return [r for n in names for r in self.by.get(n, [])]


_CACHE: dict[str, _Plumes] = {}


def _plumes_by_source() -> _Plumes:
    if "p" not in _CACHE:
        _CACHE["p"] = _Plumes()
    return _CACHE["p"]


def facility_time(fac: dict, end: str, delays: list[float], min_days: float, use_lag: bool = True) -> dict:
    """T and its parts per convention for one facility, plus the events and lags it used."""
    evs = events(fac["passes"], fac["start"], end)
    lags = publication_lags(fac)
    missing = [e["f"] for e in evs if e["f"] not in lags]
    if missing:
        raise ValueError(f"{fac['name']}: detections without a published plume on {missing[:3]}")
    out = {"events": len(evs), "lagsDays": [round(lags[e["f"]], 2) for e in evs],
           "single": sum(e["f"] == e["l"] for e in evs),
           "unboundedOnly": len(evs) == 1 and evs[0]["prev"] == fac["start"] and evs[0]["next"] == end}
    for c in CONVENTIONS:
        w, length = removed_and_length(evs, lags, delays, c, min_days, use_lag)
        out[c] = {"removedDays": round(w, 4), "emittingDays": round(length, 4),
                  "T": round(w / length, T_DIGITS) if length > 0 else 0.0}
    return out


def not_mars_notified_time(prm: dict, delays: list[float], min_days: float, end: str) -> dict:
    """Variant t_nn: T pooled over Carbon Mapper sources in cm_timelines.json farther than the join radius from every
    MARS oil and gas source that was notified (natural stops only). Each source's window runs from its first
    observation; publication lags from its own plumes."""
    import json

    tl = json.loads((DERIVED / "cm_timelines.json").read_text(encoding="utf-8"))
    with open(DERIVED / "mars_sources.csv", newline="", encoding="utf-8") as fh:
        notified = [(float(r["lat"]), float(r["lon"])) for r in csv.DictReader(fh)
                    if r["sector"] == "Oil and Gas" and r["notified"] == "Yes"]
    import numpy as np

    nlat, nlon = np.array([a for a, _ in notified]), np.array([b for _, b in notified])
    w = length = 0.0
    used = 0
    for name, t in tl.items():
        if not t["det"] or haversine_km(t["lat"], t["lon"], nlat, nlon).min() <= RADIUS_KM:
            continue
        first = min(o[0] for o in t["obs"])
        det = set(t["det"])
        passes = [{"date": d, "detected": d in det} for d in sorted({o[0] for o in t["obs"]} | det)]
        fac = {"name": name, "passes": passes, "start": date.fromordinal(_day(first).toordinal() - 1).isoformat(),
               "cm_sources": [name], "lat": t["lat"], "lon": t["lon"]}
        try:
            ft = facility_time(fac, end, delays, min_days)
        except ValueError:
            continue  # a detection without a published plume: the source is left out, not guessed
        if ft["events"]:
            used += 1
            w += ft["central"]["removedDays"]
            length += ft["central"]["emittingDays"]
    minimum = prm["parameters"]["evidence"]["minSourcesPerGroup"]
    if used < minimum:
        return {"computable": False, "sources": used, "reason": f"fewer than {minimum} not MARS-notified sources with an event"}
    return {"computable": True, "sources": used, "T": round(w / length, T_DIGITS)}


def quantiles(xs: list[float]) -> dict:
    """Median and the first and last decile edges (needs two values at least)."""
    qs = statistics.quantiles(xs, n=10)
    return {"n": len(xs), "median": round(statistics.median(xs), 2), "p10": round(qs[0], 2), "p90": round(qs[-1], 2)}

# ===================================================================================== board T4d (model.t4d)
# Removed time in absolute days against the stake's own emitting days (duty cycle x days from the start to the last
# pass); every event censored at the facility's last pass; a fix credited only until a new fault would start (the
# re-onset hazard measured on the stake's own passes); the action share conditioned on persistence (CARB plume count).

def last_pass(fac: dict) -> str:
    after = [p["date"] for p in fac["passes"] if p["date"] > fac["start"]]
    if not after:
        raise ValueError(f"{fac['name']}: no pass after the start")
    return max(after)


def censored_events(fac: dict) -> list[dict]:
    """Cusworth's events on the passes after the start, each censored at the facility's last pass: an event whose last
    detected pass is the last pass is 'censored' (its end is the last pass under every convention)."""
    lp = last_pass(fac)
    evs = events(fac["passes"], fac["start"], lp)
    for e in evs:
        e["censored"] = e["l"] == lp
    return evs


def event_end(e: dict, convention: str) -> float:
    """The event's end, in ordinal days, under a convention; a censored event ends at the last pass (= its last
    detected pass) under every convention."""
    l_, nx = _day(e["l"]).toordinal(), _day(e["next"]).toordinal()
    if e["censored"] or convention == "shortest":
        return l_
    if convention == "longest":
        return nx
    if convention == "central":
        return (l_ + nx) / 2
    raise ValueError(f"unknown convention {convention!r}")


def event_start_central(e: dict) -> float:
    return (_day(e["prev"]).toordinal() + _day(e["f"]).toordinal()) / 2


def survival_days(x_days: float, hazard_per_year: float, days_per_year: float) -> float:
    """Time from a fix to x_days later, weighted by the survival of no new fault: (1 - exp(-h x)) / h (x if h = 0)."""
    import math

    if x_days <= 0:
        return 0.0
    if hazard_per_year == 0:
        return x_days
    h = hazard_per_year / days_per_year
    return (1 - math.exp(-h * x_days)) / h


def reonset_hazard(evs_by_fac: list[list[dict]], last_passes: list[str], days_per_year: float) -> dict:
    """Restarts / event-free years over a scope's facilities (central convention): an event-free period runs from the
    end of an event with an observed end to the central start of the next event, or to the facility's last pass if
    none follows; a restart is a period that ends in an event."""
    restarts, off = 0, 0.0
    for evs, lp in zip(evs_by_fac, last_passes):
        for i, e in enumerate(evs):
            if e["censored"]:
                continue
            end = event_end(e, "central")
            if i + 1 < len(evs):
                restarts += 1
                off += max(0.0, event_start_central(evs[i + 1]) - end)
            else:
                off += max(0.0, _day(lp).toordinal() - end)
    years = off / days_per_year
    return {"restarts": restarts, "eventFreeYears": round(years, 2),
            "perYear": round(restarts / years, T_DIGITS) if years > 0 else None}


def r_by_plume_class(cs: dict, prm: dict) -> dict:
    """Type A share among the CARB oil and gas incidences with per-incidence rows, by plume-count class (1, 2, 3+),
    from the committed counts by plume count and outcome (data/derived/carb_summary.json)."""
    acted = prm["parameters"]["responseScenarios"]["rule"]["acted"]
    top = len(PLUME_CLASSES)
    out = {}
    for k, label in enumerate(PLUME_CLASSES, start=1):
        counts = [c for n, c in cs["aggregates"]["byPlumesOutcome"].items() if min(int(n), top) == k]
        n_a, n = sum(c[acted] for c in counts), sum(sum(c.values()) for c in counts)
        out[label] = {"typeA": n_a, "n": n, "share": round(n_a / n, T_DIGITS)}
    return out


def plume_class(n_detected: int) -> str:
    return PLUME_CLASSES[min(n_detected, len(PLUME_CLASSES)) - 1]


def facility_credit(fac: dict, evs: list[dict], lags: dict[str, float], delays: list[float], r_of: dict,
                    hazard_per_year: float, convention: str, days_per_year: float, lag_add: float = 0,
                    use_lag: bool = True, r_censored_minus: float = 0, censored_stop_hazard: float = 0,
                    fixed_lag: float | None = None, mark_dates: set | None = None) -> dict:
    """Sum over events of r_e x C_e (credited days) and its censored part; r_of maps a plume class to r. A censored
    event's credit is also weighted by the survival of no natural stop (option e: censored_stop_hazard = h_nat)."""
    total = cens = marked = 0.0
    for e in evs:
        end = event_end(e, convention)
        lag = fixed_lag if fixed_lag is not None else (lags[e["f"]] if use_lag else 0)
        fix0 = _day(e["f"]).toordinal() + lag + lag_add
        hz = hazard_per_year + (censored_stop_hazard if e["censored"] else 0)
        c = statistics.fmean(survival_days(end - (fix0 + d), hz, days_per_year) for d in delays)
        r = r_of[plume_class(e["n"])] - (r_censored_minus if e["censored"] else 0)
        total += r * c
        cens += r * c if e["censored"] else 0
        marked += r * c if mark_dates and any(e["f"] <= x <= e["l"] for x in mark_dates) else 0
    return {"credit": total, "censoredCredit": cens, "markedCredit": marked}


def merge_single_misses(evs: list[dict]) -> list[dict]:
    """Events merged across exactly one pass without a detection (the pass that ends one event is the pass before the
    next): a single missed detection of a persistent source no longer counts as a restart (post-result variant)."""
    out: list[dict] = []
    for e in evs:
        if out and not out[-1]["censored"] and out[-1]["next"] == e["prev"]:
            m = out[-1]
            out[-1] = {**m, "l": e["l"], "n": m["n"] + e["n"], "next": e["next"], "censored": e["censored"]}
        else:
            out.append(dict(e))
    return out


def t4c_variant(evs: list[dict], lags: dict[str, float], delays: list[float], min_days: float,
                hazard_per_year: float, cap_days: float | None, days_per_year: float) -> tuple[float, float]:
    """The reviewer's report-only rows on T4c's ratio (central convention): (removed, length) with each event's
    removed time weighted by survival and / or capped."""
    w = length = 0.0
    for e in evs:
        s, t = bounds(e, "central", min_days)
        cut = _day(e["f"]).toordinal() + lags[e["f"]]
        vals = [survival_days(t - (cut + d), hazard_per_year, days_per_year) for d in delays]
        w += statistics.fmean(min(v, cap_days) if cap_days is not None else v for v in vals)
        length += t - s
    return w, length


PLUME_CLASSES = ("1", "2", "3+")


# ---------------------------------------------------------- board T4d amendment: the not MARS-notified natural-stop sample
def sample_passes(tl: dict) -> list[dict]:
    """A sampled source's observation dates as passes (a detection date counts as an observation)."""
    det = set(tl["det"])
    return [{"date": d, "detected": d in det} for d in sorted({o[0] for o in tl["obs"]} | det)]


def sample_start(passes: list[dict]) -> str:
    """The day before a sampled source's first observation: its window starts with it."""
    return date.fromordinal(_day(passes[0]["date"]).toordinal() - 1).isoformat()


def natural_stop_parts(passes: list[dict], start: str) -> tuple[int, float]:
    """(observed event ends, emitting days) on one series after start, central convention, censored at its last pass;
    an event's start is the midpoint of the pass before it without a detection and its first detection, or the first
    detection if there is none (model.t4d.notMarsNotifiedSample.naturalStopHazard)."""
    after = [p for p in passes if p["date"] > start]
    if not after:
        return 0, 0.0
    lp = max(p["date"] for p in after)
    evs = events(passes, start, lp)
    ends, emitting = 0, 0.0
    for e in evs:
        e["censored"] = e["l"] == lp
        s = _day(e["f"]).toordinal() if e["prev"] == start else event_start_central(e)
        ends += not e["censored"]
        emitting += max(0.0, event_end(e, "central") - s)
    return ends, emitting


def natural_stop_hazard(series: list[tuple[list[dict], str]], days_per_year: float) -> dict:
    """Observed event ends / emitting years over a set of series (each passes + start)."""
    ends, emitting, used = 0, 0.0, 0
    for passes, start in series:
        n, em = natural_stop_parts(passes, start)
        ends += n
        emitting += em
        used += em > 0 or n > 0
    years = emitting / days_per_year
    return {"observedEnds": ends, "emittingYears": round(years, 2), "sourcesWithEvents": used,
            "perYear": round(ends / years, T_DIGITS) if years > 0 else None}


# ===================================================================== board B1: the forward hold-out (pre-registered)
# PRE-REGISTERED 2026-10-24 (submission sprint task P1), committed on its own before any forward value was computed:
# the commit order is the evidence. Known when it was written: T4d option d on portfolio_v3 (central ~30 kt CH4/yr,
# up to ~62 kt with no recurrence; 65.52 % of its credit on events containing the plume that selected the member) and
# the shape of the committed files; no member list, stake, event or tonnage of the forward scope had been computed.
# Every choice is fixed here; the implementation reads these values and types none of its own.
FORWARD = {
    "id": "portfolio_forward",
    "status": "pre-registered 2026-10-24, before any forward value was computed",
    "question": ("If members are chosen only with what was known on a date D and credited only for events that start "
                 "after D, what would the clause stop? No credit can fall on the plume that selected a member."),
    "D": "2025-09-12",
    "dWhy": "one year before the MARS snapshot's as-of date (join_v1.json summary.mars_as_of = 2026-09-12)",
    "creditEnd": "2026-09-13",
    "creditEndWhy": "the Carbon Mapper catalogue end of the committed timelines (backtest.json catalogueEnd)",
    "universe": ("the backtest's evaluated facilities (backtest.json): today's MARS oil and gas targets (notified, "
                 "labelled absent today), each with the Carbon Mapper sources it owns; the only sources whose Carbon "
                 "Mapper plume rows, rates and publication times are committed"),
    "members": {
        "absentAtD": ("MARS knew the source at D and saw no plume in the six calendar months before it: at least one "
                      "mars_plumes_og.csv row (tile_date, UTC date) before D minus WINDOW_MONTHS (common.py, the join's "
                      "own absent window) and none in [D minus WINDOW_MONTHS, D], both ends inclusive"),
        "plumeBeforeD": ("at least one Carbon Mapper plume at the facility, counted as the backtest counts a detection "
                         "(an owned source, origin within RADIUS_KM of the MARS source, de-duplicated by plume_id), "
                         "whose published_at UTC date is before D"),
    },
    "stake": ("S_i = stake.band_from over the facility's passes after its start (the first MARS plume) up to and "
              "including D and the rates of its plumes dated in that window: the low bound, quantified members only; "
              "duty D_i = that window's detected passes / passes. The stake is known at D (no pass after D is read)"),
    "credit": ("T4d option d otherwise unchanged (central convention, r by CARB plume-count class scaled by each action "
               "scenario, every CARB Type A notification-to-mitigation delay, each detection's own publication lag, "
               "survival weighting): Cusworth events on the facility's passes up to creditEnd, each censored at the "
               "last pass on or before creditEnd; only events whose first detection is after D are credited"),
    "denominator": ("D_i x W_i with W_i = days from D to the facility's last pass on or before creditEnd; a member with "
                    "no pass after D is credited 0 and counted"),
    "hazard": ("lambda measured on the members' own passes up to and including D (reonset_hazard, central "
               "convention, events censored at the last pass on or before D); refuse to run if it is undefined"),
    "band": ("low = with that lambda, high = lambda 0 (no recurrence), at each action scenario; the headline figure "
             "is the low end at the central action share"),
    "selectingShare": ("the share of credit on events containing a member's selecting plume (the scene dates of the "
                       "plumes that meet plumeBeforeD) is 0 by construction, as those plumes are dated before D; it is "
                       "computed and checked"),
    "sensitivity": {
        "fwd_stake_backtest": ("S_i and D_i from backtest.json (passes from the start to the catalogue end, as "
                               "portfolio_v3): reads passes after D"),
        "fwd_join_rule": ("plumeBeforeD replaced by the join criterion run at D: a plume with scene date in "
                          "[D minus WINDOW_MONTHS, D] and published before D"),
        "fwd_credit_before_absent_window": ("creditEnd moved to the day before today's MARS absent window starts "
                                            "(join_v1.json window[0] minus one day), so the credit window does not "
                                            "overlap the period today's absent label is decided on"),
    },
    "leaks": {
        "universe": ("the universe is today's MARS targets: 'notified' is today's flag (common.py mars_target; MARS "
                     "publishes no notification date) and 'absent today' means no MARS plume in join_v1.json's window, "
                     "which lies inside the credit window. Removing it needs Carbon Mapper plume rows for the other "
                     "notified sources' neighbourhoods (network). Reported: the notified oil and gas MARS sources "
                     "outside the universe that meet absentAtD, and how many of them have a Carbon Mapper source of "
                     "cm_timelines_notified.json within RADIUS_KM with a detection dated before D"),
        "marsPublication": ("MARS plume rows carry no publication date: absentAtD reads today's MARS catalogue, which "
                            "may hold plumes imaged before D but published after it, and has lost re-processed ones"),
        "passPublication": ("a Carbon Mapper pass without a detection carries no publication date: the stake and the "
                            "hazard read today's catalogue of scenes dated up to D"),
    },
    "reporting": ("the forward figures are written next to portfolio_v3's; no threshold here decides which one a "
                  "headline carries (an adversarial review does)"),
}


# ------------------------------------------------------------------- board B1: the forward hold-out's building blocks
def absent_at(mars_dates: list[str], d: str, window_start: str) -> bool:
    """FORWARD.members.absentAtD: MARS knew the source at d (a plume dated before window_start) and saw none in
    [window_start, d], both ends inclusive (UTC dates)."""
    days = [x[:10] for x in mars_dates]
    return any(x < window_start for x in days) and not any(window_start <= x <= d for x in days)


def facility_plumes(fac: dict) -> list[dict]:
    """The facility's Carbon Mapper plume rows as the backtest counts a detection: an owned source, origin within the
    join radius of the MARS source, a scene timestamp; de-duplicated by plume_id (as backtest.facilities)."""
    return [r for r in _plumes_by_source().get_many(fac["cm_sources"]) if r["scene_timestamp"]
            and haversine_km(fac["lat"], fac["lon"], float(r["lat"]), float(r["lon"])) <= RADIUS_KM]


def truncated(fac: dict, end: str) -> dict:
    """The facility with only its passes dated on or before end."""
    return {**fac, "passes": [p for p in fac["passes"] if p["date"] <= end]}


def has_pass_after_start(fac: dict) -> bool:
    return any(p["date"] > fac["start"] for p in fac["passes"])


# Added 2026-10-24 AFTER the forward result was known (adversarial review, task P1b): reported only, never a value.
FORWARD_POST_RESULT = {
    "status": ("added 2026-10-24 after the forward result was known (adversarial review): reported only; the "
               "registered figure (FORWARD) is unchanged"),
    "sensitivity": {
        "fwd_stake_published_by_D": ("as FORWARD, with every detection dated after the start and up to D that no plume "
                                     "published before D shows dropped (its pass and its rates) from the series the "
                                     "stake, the hazard and the events read"),
        # P1c (second review): the 0.60 kt row cuts the credit window to six months AND ends it before the window
        # today's 'absent' label is decided on; this control keeps the six-month length and ends before that window
        # too, at an origin six months earlier, to separate the two
        "fwd_control_short_window": ("FORWARD's rules at origin controlShortWindow.origin with credit up to "
                                     "controlShortWindow.creditEnd: the same six-month credit length as "
                                     "fwd_credit_before_absent_window, also ending before today's absent window"),
    },
    "controlShortWindow": {
        "origin": "2025-03-12", "creditEnd": "2025-09-11",
        "reads": ("the short window explains most of the drop of fwd_credit_before_absent_window when the control's "
                  "share of stake is nearer that row's share than FORWARD's own share"),
    },
    "bootstrap": {
        "rule": ("members resampled with replacement, as many as there are, each draw re-measuring the re-onset "
                 "hazard on the drawn members' passes up to D and recomputing the central figure (central action share) "
                 "and its share of the drawn credited members' stake; percentile interval. Draws, seed and confidence "
                 "are the projection's existing bootstrap design (projection_sources.json parameters.evidence)"),
        "drawsFrom": "bootstrapDraws", "seedFrom": "seed", "confidenceFrom": "confidence",
    },
    "leaks": {
        "detectionPublication": ("the stake and the hazard at D count detections dated up to D whose plumes Carbon "
                                 "Mapper published only on or after D (today's catalogue); found after the result, so "
                                 "it is reported as the sensitivity row fwd_stake_published_by_D and does not change "
                                 "the registered figure"),
    },
}


# PRE-REGISTERED 2026-10-24 (task P1b step 6), committed on its own before any of its values was computed. Known when
# it was written: the D = 2025-09-12 forward result (3.38 kt CH4/yr central, 4.0 % of the credited stake) and its
# post-result rows; nothing at the origins below. Reported only: it sets no value and does not replace FORWARD.
FORWARD_ROLLING = {
    "id": "portfolio_forward_rolling",
    "status": "pre-registered 2026-10-24 after the D = 2025-09-12 result was known; reported only",
    "origins": ["2025-03-12", "2025-06-12", "2025-12-12"],
    "originsWhy": "three more origins three months apart around FORWARD.D, the latest leaving nine months of credit",
    "creditEnd": "2026-09-13",
    "rule": ("FORWARD's member, stake, credit, denominator, hazard and band rules unchanged, with D replaced by each "
             "origin (absentAtD, plumeBeforeD, the stake and hazard up to the origin, events first detected after it, "
             "credit up to creditEnd); no bootstrap and no post-result rows"),
    "perOrigin": ("members, credited members, credited stake, central figure (central action share, measured hazard) "
                  "and its no-recurrence end, each as kt CH4/yr and as a share of the credited stake"),
    "pooled": ("over the four origins (these three and FORWARD.D): the sum of the central figures over the sum of "
               "the credited stakes, and the same at no recurrence; a facility can be a member at several origins, so "
               "the pooled share is a summary across origins, not a larger sample"),
}


# Added 2026-10-24 (submission sprint task P2 item 1), committed on its own BEFORE any value of this control was
# computed. Known when it was written: the forward result and every reported row (full window 4.01 % of the credited
# stake, the credit ending before the label window 0.85 %, the six-month control 0.04 %, the rolling origin 2025-03-12
# to creditEnd 2.90 %, the member-bootstrap share interval 1.26-7.03 %). Reported only: it sets no value.
FORWARD_CONTROL_LENGTH = {
    "id": "fwd_control_full_length",
    "status": ("added 2026-10-24 after the forward result was known and before this control was computed; reported "
               "only, the registered figure (FORWARD) is unchanged"),
    "origin": "2025-03-12",
    "creditEnd": "2026-03-11",
    "why": ("a credit window as long as FORWARD's (twelve months) that ends, like fwd_credit_before_absent_window, the "
            "day before today's absent window starts (join_v1.json window[0] minus one day): it keeps the length and "
            "drops the label window, where the six-month control drops both"),
    "rule": ("FORWARD's member, stake, credit, denominator, hazard and band rules unchanged, with D replaced by origin "
             "and creditEnd by this creditEnd (as fwd_control_short_window); no bootstrap"),
    "near": ("the control's share of the credited stake (central action share, measured hazard) is near the full-window "
             "share when it lies inside FORWARD's member-bootstrap interval of that share "
             "(forward.bootstrap.shareOfStakeCentral.ci, both ends inclusive) and is nearer FORWARD's share than "
             "fwd_credit_before_absent_window's share"),
    "readsIfNear": "the window's length explains the share of the credit ending before the label window",
    "readsOtherwise": ("the control differs by period and members as well as length, so it does not separate the "
                       "window's length from the label"),
    "sentence": ("the forward limits sentence carries this control's figure and its reading in place of the six-month "
                 "control's reading; the six-month row stays reported in forward.sensitivity"),
}


def reonset_parts(evs: list[dict], last: str) -> tuple[int, float]:
    """One facility's (restarts, event-free days) as reonset_hazard counts them (central convention)."""
    restarts, off = 0, 0.0
    for i, e in enumerate(evs):
        if e["censored"]:
            continue
        end = event_end(e, "central")
        if i + 1 < len(evs):
            restarts += 1
            off += max(0.0, event_start_central(evs[i + 1]) - end)
        else:
            off += max(0.0, _day(last).toordinal() - end)
    return restarts, off


def credit_terms(fac: dict, evs: list[dict], lags: dict[str, float], delays: list[float], r_of: dict):
    """facility_credit's terms as arrays (weights r_e / n_delays, x_ej = end_e - (fix_e + d_j)) under the central
    convention with no censored-stop hazard, so the credit at any hazard is credit_at(terms, h)."""
    import numpy as np

    w, x = [], []
    for e in evs:
        end, fix0 = event_end(e, "central"), _day(e["f"]).toordinal() + lags[e["f"]]
        w += [r_of[plume_class(e["n"])] / len(delays)] * len(delays)
        x += [end - (fix0 + d) for d in delays]
    return np.array(w, dtype=float), np.array(x, dtype=float)


def credit_at(terms, hazard_per_year: float, days_per_year: float) -> float:
    """Sum of w x survival_days(x, hazard) over the terms (vectorised survival_days)."""
    import numpy as np

    w, x = terms
    xp = np.clip(x, 0.0, None)
    if hazard_per_year == 0:
        return float((w * xp).sum())
    h = hazard_per_year / days_per_year
    return float((w * (1 - np.exp(-h * xp)) / h).sum())


def without_late_published(fac: dict, d: str) -> tuple[dict, list[str]]:
    """The facility without its detections dated after the start and up to d that no plume published before d shows
    (their passes and rates dropped), and those dates."""
    pub = {r["scene_timestamp"][:10] for r in facility_plumes(fac) if r["published_at"] and r["published_at"][:10] < d}
    late = sorted(p["date"] for p in fac["passes"]
                  if fac["start"] < p["date"] <= d and p["detected"] and p["date"] not in pub)
    drop = set(late)
    return ({**fac, "passes": [p for p in fac["passes"] if p["date"] not in drop],
             "rates": [(day, r) for day, r in fac["rates"] if day not in drop]}, late)
