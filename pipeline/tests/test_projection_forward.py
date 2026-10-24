"""Board B1: the forward hold-out of the carbon projection (projection.json -> forward).

The pre-registration (projection_time.FORWARD) is pinned here and was committed on its own before any forward value was
computed (git log -- pipeline/projection_time.py). The members, the stake known at D and the credit are recomputed with
this file's own loop (the event, lag and credit helpers of tests/test_projection_t4d.py, independent of
projection_time.py). Offline: committed files only."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

import projection_time as ptime
from common import RADIUS_KM, WINDOW_MONTHS, haversine_km, minus_months
from test_projection_t4d import credit, delays, end_of, lags_for, o, plumes_by_source, r_classes, runs

ROOT = Path(__file__).resolve().parents[2]
DERIVED = ROOT / "data" / "derived"
# sha256 of json.dumps(FORWARD, sort_keys=True, ensure_ascii=False) as committed in the pre-registration commit
FORWARD_SHA256 = "7a53280eed63565eda3987f185c813ebbf2d1afa037fe03dc37f6433b897d2e2"


def out() -> dict:
    return json.loads((DERIVED / "projection.json").read_text(encoding="utf-8"))


def fwd() -> dict:
    return out()["forward"]


def central() -> float:
    return json.loads((DERIVED / "projection_sources.json").read_text(encoding="utf-8"))[
        "parameters"]["responseScenarios"]["central"]


# --------------------------------------------------------------------------------------------- the pre-registration
def test_the_forward_pre_registration_is_pinned():
    f = ptime.FORWARD
    assert f["id"] == "portfolio_forward"
    assert (f["D"], f["creditEnd"]) == ("2025-09-12", "2026-09-13")
    assert set(f["members"]) == {"absentAtD", "plumeBeforeD"}
    assert "WINDOW_MONTHS" in f["members"]["absentAtD"] and "RADIUS_KM" in f["members"]["plumeBeforeD"]
    assert "before D" in f["members"]["plumeBeforeD"] and "up to and including D" in f["stake"]
    assert "first detection is after D" in f["credit"]
    assert set(f["sensitivity"]) == {"fwd_stake_backtest", "fwd_join_rule", "fwd_credit_before_absent_window"}
    assert set(f["leaks"]) == {"universe", "marsPublication", "passPublication"}
    assert (WINDOW_MONTHS, RADIUS_KM) == (6, 1.5)  # the join's frozen constants the rule names


def test_the_pre_registration_is_byte_for_byte_the_committed_one():
    h = hashlib.sha256(json.dumps(ptime.FORWARD, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert h == FORWARD_SHA256, "FORWARD changed after it was pre-registered: register a new board, do not edit it"
    assert fwd()["preRegistration"] == ptime.FORWARD


FORWARD_ROLLING_SHA256 = "31a934171fe94b0d35deed19e5fab59c4c4ac5ed1d07601ab59deae30a4a02b3"


def test_the_rolling_origin_pre_registration_is_byte_for_byte_the_committed_one():
    r = ptime.FORWARD_ROLLING
    h = hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert h == FORWARD_ROLLING_SHA256, "FORWARD_ROLLING changed after it was pre-registered"
    assert r["origins"] == ["2025-03-12", "2025-06-12", "2025-12-12"] and r["creditEnd"] == ptime.FORWARD["creditEnd"]
    assert ptime.FORWARD["D"] not in r["origins"]


# Committed with the rule, before any value of the twelve-month control was computed (task P2 item 1).
FORWARD_CONTROL_LENGTH_SHA256 = "9e5feba8f94153bcd4c55cb7c3d3cbfa124d6bd6f9b2dd12999b3dece560b2dd"


def test_the_full_length_control_rule_is_byte_for_byte_the_committed_one():
    c = ptime.FORWARD_CONTROL_LENGTH
    h = hashlib.sha256(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    assert h == FORWARD_CONTROL_LENGTH_SHA256, "FORWARD_CONTROL_LENGTH changed after it was committed"
    assert (c["origin"], c["creditEnd"]) == (ptime.FORWARD_POST_RESULT["controlShortWindow"]["origin"], "2026-03-11")


def test_absent_at_needs_an_earlier_plume_and_none_in_the_window():
    ws = minus_months("2025-09-12", WINDOW_MONTHS)
    assert ws == "2025-03-12"
    assert ptime.absent_at(["2024-01-01"], "2025-09-12", ws)
    assert not ptime.absent_at([], "2025-09-12", ws), "a source MARS did not know at D is not absent at D"
    assert not ptime.absent_at(["2025-03-12T10:00:00"], "2025-09-12", ws), "window start is inclusive"
    assert not ptime.absent_at(["2024-01-01", "2025-09-12"], "2025-09-12", ws), "D is inclusive"
    assert ptime.absent_at(["2024-01-01", "2025-09-13"], "2025-09-12", ws), "a later plume is not known at D"


# --------------------------------------------------------------------------------- an independent forward loop
def _inputs() -> dict:
    import backtest

    facs, end, _ = backtest.facilities()
    bt = json.loads((DERIVED / "backtest.json").read_text(encoding="utf-8"))
    return {"by": {f["name"]: f for f in facs}, "bt": {f["name"]: f for f in bt["facilities"]}, "end": end,
            "plumes": plumes_by_source()}


@pytest.fixture(scope="module")
def ind() -> dict:
    return _inputs()


def _selecting(fac: dict, plumes: dict, d: str, ws: str | None) -> set[str]:
    out_ = set()
    for n in fac["cm_sources"]:
        for r in plumes.get(n, []):
            if not r["scene_timestamp"] or not r["published_at"] or r["published_at"][:10] >= d:
                continue
            if haversine_km(fac["lat"], fac["lon"], float(r["lat"]), float(r["lon"])) > RADIUS_KM:
                continue
            day = r["scene_timestamp"][:10]
            if ws is None or ws <= day <= d:
                out_.add(day)
    return out_


def _members(ind: dict, join_rule: bool = False, d: str | None = None) -> dict[str, set]:
    d = d or ptime.FORWARD["D"]
    ws = minus_months(d, WINDOW_MONTHS)
    out_ = {}
    for n, f in ind["by"].items():
        if not ind["bt"][n].get("evaluated"):
            continue
        md = [x[:10] for x in f["mars_dates"]]
        if not (any(x < ws for x in md) and not any(ws <= x <= d for x in md)):
            continue
        sel = _selecting(f, ind["plumes"], d, ws if join_rule else None)
        if sel:
            out_[n] = sel
    return out_


def _stake_at_d(f: dict, d: str | None = None) -> dict:
    from stake import band_from

    d = d or ptime.FORWARD["D"]
    after = [p for p in f["passes"] if f["start"] < p["date"] <= d]
    return band_from(len(after), sum(p["detected"] for p in after), [r for x, r in f["rates"] if f["start"] < x <= d])


def _late_dropped(ind: dict, f: dict) -> tuple[dict, int]:
    """The facility without the detections dated after its start and up to D that no plume published before D shows."""
    d = ptime.FORWARD["D"]
    pub = set()
    for n in f["cm_sources"]:
        for r in ind["plumes"].get(n, []):
            if (r["scene_timestamp"] and r["published_at"] and r["published_at"][:10] < d
                    and haversine_km(f["lat"], f["lon"], float(r["lat"]), float(r["lon"])) <= RADIUS_KM):
                pub.add(r["scene_timestamp"][:10])
    late = {p["date"] for p in f["passes"] if f["start"] < p["date"] <= d and p["detected"] and p["date"] not in pub}
    return ({**f, "passes": [p for p in f["passes"] if p["date"] not in late],
             "rates": [(x, r) for x, r in f["rates"] if x not in late]}, len(late))


def _kt(ind: dict, members: dict[str, set], end: str, hazard_of: str, stake_from: str = "atD",
        by: dict | None = None, d: str | None = None) -> dict:
    d = d or ptime.FORWARD["D"]
    by = by or ind["by"]
    rc, dl = r_classes(), delays()
    restarts, off = 0, 0.0
    for n in members:
        f = by[n]
        pre = [p for p in f["passes"] if p["date"] <= d]
        if not any(p["date"] > f["start"] for p in pre):
            continue
        last = max(p["date"] for p in pre if p["date"] > f["start"])
        evs = runs(pre, f["start"])
        for i, e in enumerate(evs):
            if e["censored"]:
                continue
            if i + 1 < len(evs):
                restarts += 1
                off += max(0.0, (o(evs[i + 1]["prev"]) + o(evs[i + 1]["f"])) / 2 - end_of(e, "central"))
            else:
                off += max(0.0, o(last) - end_of(e, "central"))
    lam = restarts / (off / 365.25)
    hz = lam if hazard_of == "measured" else 0.0
    kt = total = marked = 0.0
    for n, sel in members.items():
        f = by[n]
        st = _stake_at_d(f, d) if stake_from == "atD" else ind["bt"][n]["stake"]
        if st["status"] != "quantified":
            continue
        post = [p for p in f["passes"] if p["date"] <= end]
        after_d = [p["date"] for p in post if p["date"] > d]
        if not after_d:
            continue
        lag = lags_for(f, ind["plumes"])
        emit = st["duty_cycle"] * (o(max(after_d)) - o(d))
        c_f = 0.0
        for e in runs(post, f["start"]):
            if e["f"] <= d:
                continue
            r = rc[str(e["n"]) if e["n"] < 3 else "3+"]
            c = r * sum(credit(end_of(e, "central") - (o(e["f"]) + lag[e["f"]] + x), hz) for x in dl) / len(dl)
            c_f += c
            marked += c if any(e["f"] <= x <= e["l"] for x in sel) else 0
        total += c_f
        kt += st["kt_ch4_per_yr"]["low"] * min(1.0, c_f / emit if emit else 0.0)
    return {"kt": kt, "lambda": lam, "restarts": restarts, "selecting": marked / total if total else 0.0}


def _row(scope: dict) -> dict:
    return next(r for r in scope["scenarios"] if r["responseShare"] == central())


def test_the_members_are_the_rule_on_the_committed_rows(ind):
    sc = fwd()["scopes"]["portfolio_forward"]
    m = _members(ind)
    assert sc["memberNames"] == sorted(m)
    assert sc["members"] == len(m) > 0
    assert all(max(v) < ptime.FORWARD["D"] for v in m.values()), "selecting plumes are dated before D"


def test_the_stake_is_the_one_known_at_d(ind):
    sc = fwd()["scopes"]["portfolio_forward"]
    q = {n: _stake_at_d(ind["by"][n]) for n in _members(ind)}
    q = {n: s for n, s in q.items() if s["status"] == "quantified"}
    assert sc["membersQuantified"] == len(q)
    assert sc["stakeLowKtCh4PerYr"] == pytest.approx(sum(s["kt_ch4_per_yr"]["low"] for s in q.values()), abs=0.01)
    d, end = ptime.FORWARD["D"], ptime.FORWARD["creditEnd"]
    seen = [n for n in q if any(d < p["date"] <= end for p in ind["by"][n]["passes"])]
    assert sc["membersCredited"] == len(seen), "quantified members with a pass after D"
    assert sc["stakeLowKtCh4PerYrCredited"] == pytest.approx(sum(q[n]["kt_ch4_per_yr"]["low"] for n in seen), abs=0.01)


def test_the_forward_band_is_the_rule_recomputed_independently(ind):
    sc = fwd()["scopes"]["portfolio_forward"]
    row = _row(sc)
    m = _members(ind)
    lo, hi = _kt(ind, m, ptime.FORWARD["creditEnd"], "measured"), _kt(ind, m, ptime.FORWARD["creditEnd"], "none")
    assert sc["reonsetHazard"]["restarts"] == lo["restarts"]
    assert sc["reonsetHazard"]["perYear"] == pytest.approx(lo["lambda"], abs=1e-4)
    assert row["kt_ch4_per_yr"]["low"] == pytest.approx(lo["kt"], abs=0.01), "low = with the measured re-onset hazard"
    assert row["kt_ch4_per_yr"]["high"] == pytest.approx(hi["kt"], abs=0.01), "high = no recurrence"


def test_no_credit_falls_on_a_selecting_plume():
    f = fwd()
    assert f["scopes"]["portfolio_forward"]["selectingShareOfCredit"] == 0
    assert all(r["selectingShareOfCredit"] == 0 for r in f["sensitivity"])
    assert f["card"]["selectingShareOfCredit"] == 0


def test_the_sensitivity_rows_are_the_registered_ones_and_set_nothing(ind):
    f = fwd()
    rows = {r["id"]: r for r in f["sensitivity"]}
    post = ptime.FORWARD_POST_RESULT["sensitivity"]
    assert set(rows) == set(ptime.FORWARD["sensitivity"]) | set(post)
    assert all(r["rule"] == {**ptime.FORWARD["sensitivity"], **post}[r["id"]] for r in rows.values())
    assert all(r["postResult"] == (r["id"] in post) and not r["setsValue"] for r in rows.values())
    pub_by = {n: _late_dropped(ind, ind["by"][n]) for n in _members(ind)}
    assert rows["fwd_stake_published_by_D"]["detectionsDropped"] == sum(v[1] for v in pub_by.values())
    assert fwd()["leaks"]["detectionPublication"]["detections"] == sum(v[1] for v in pub_by.values())
    assert fwd()["leaks"]["detectionPublication"]["members"] == sum(v[1] > 0 for v in pub_by.values())
    pub_row = _kt(ind, _members(ind), ptime.FORWARD["creditEnd"], "measured", by={n: v[0] for n, v in pub_by.items()})
    assert rows["fwd_stake_published_by_D"]["ktCh4PerYrCentralAction"]["low"] == pytest.approx(pub_row["kt"], abs=0.01)
    assert rows["fwd_stake_published_by_D"]["reonsetHazardPerYear"] == pytest.approx(pub_row["lambda"], abs=1e-4)
    bt_row = _kt(ind, _members(ind), ptime.FORWARD["creditEnd"], "measured", stake_from="backtest")
    assert rows["fwd_stake_backtest"]["ktCh4PerYrCentralAction"]["low"] == pytest.approx(bt_row["kt"], abs=0.01)
    join_m = _members(ind, join_rule=True)
    assert set(join_m) <= set(_members(ind))
    assert rows["fwd_join_rule"]["members"] == len(join_m)
    assert rows["fwd_join_rule"]["ktCh4PerYrCentralAction"]["low"] == pytest.approx(
        _kt(ind, join_m, ptime.FORWARD["creditEnd"], "measured")["kt"], abs=0.01)
    early = rows["fwd_credit_before_absent_window"]
    win0 = json.loads((DERIVED / "join_v1.json").read_text(encoding="utf-8"))["summary"]["window"][0]
    assert early["creditEnd"] < win0
    assert early["ktCh4PerYrCentralAction"]["low"] == pytest.approx(
        _kt(ind, _members(ind), early["creditEnd"], "measured")["kt"], abs=0.01)


def test_the_universe_leak_is_counted_from_the_committed_files(ind):
    """Notified oil and gas MARS sources outside today's targets that MARS also called absent at D."""
    leak = fwd()["leaks"]["universe"]
    d = ptime.FORWARD["D"]
    ws = minus_months(d, WINDOW_MONTHS)
    with open(DERIVED / "mars_sources.csv", newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["sector"] == "Oil and Gas" and r["notified"] == "Yes"]
    dates: dict[str, list[str]] = {}
    with open(DERIVED / "mars_plumes_og.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            dates.setdefault(r["source_name"], []).append(r["tile_date"][:10])
    outside = [r for r in rows if r["persistency_category"] != "absent"
               and ptime.absent_at(dates.get(r["source_name"], []), d, ws)]
    assert leak["absentAtDOutsideUniverse"] == len(outside)
    assert leak["notifiedOutsideUniverse"] == sum(r["persistency_category"] != "absent" for r in rows)


def test_the_card_numbers_are_read_from_the_scope():
    f = fwd()
    sc, card = f["scopes"]["portfolio_forward"], f["card"]
    row = _row(sc)
    assert card["centralKtCh4PerYr"] == row["kt_ch4_per_yr"]["low"]
    assert card["upToNoRecurrenceKtCh4PerYr"] == row["kt_ch4_per_yr"]["high"]
    lo = min(sc["scenarios"], key=lambda r: r["responseShare"])
    hi = max(sc["scenarios"], key=lambda r: r["responseShare"])
    assert (card["lowKtCh4PerYr"], card["highKtCh4PerYr"]) == (lo["kt_ch4_per_yr"]["low"], hi["kt_ch4_per_yr"]["high"])
    assert (card["members"], card["membersQuantified"], card["membersCredited"]) == (
        sc["members"], sc["membersQuantified"], sc["membersCredited"])
    c, b = row["kt_ch4_per_yr"]["low"], (card["lowKtCh4PerYr"], card["highKtCh4PerYr"])
    bci = f["bootstrap"]["ktCh4PerYrCentral"]["ci"]
    want = (f"[PROJECTION] Pre-registered forward hold-out: a {sc['members']}-facility portfolio covenant [SIM], "
            f"chosen with Carbon Mapper plumes published before {ptime.FORWARD['D']} at sources MARS had not seen for "
            "six months and lists as absent today, and credited only for faults first detected after that date up to "
            f"{ptime.FORWARD['creditEnd']}, would stop ~{c:.1f} kt CH₄/yr at the central {central() * 100:.0f} % action "
            f"share (95 % member bootstrap ~{bci[0]:.1f}–{bci[1]:.1f} kt; "
            f"~{b[0]:.1f}–{b[1]:.1f} kt across action and recurrence scenarios), "
            f"~{row['mt_co2e_per_yr_gwp100']['low']:.2f} Mt CO₂e/yr at GWP-100, none of it on the plume that selected "
            "a member — assumptions: paper §4.")
    assert card["headlineSentence"] == want, "the reviewed sentence, fields generated"
    assert "began after" not in json.dumps(card)
    st = sc["stakeLowKtCh4PerYrCredited"]
    assert card["stakeSentence"].startswith(f"Stake, not abatement: ~{st:,.0f} kt")
    assert f"{row['shareOfStake']['low'] * 100:.1f} %" in card["stakeSentence"]
    allq = row["kt_ch4_per_yr"]["low"] / sc["stakeLowKtCh4PerYr"]
    assert card["shareOfStakeAllQuantifiedCentral"] == pytest.approx(allq, abs=1e-4)
    assert card["stakeSentence"].endswith(
        f"({allq * 100:.1f} % of the ~{sc['stakeLowKtCh4PerYr']:,.0f} kt stake of all {sc['membersQuantified']} "
        "quantified members).")
    ci = f["bootstrap"]["ktCh4PerYrCentral"]["ci"]
    assert card["bootstrapKtCh4PerYrCentralCi"] == ci
    assert f"~{ci[0]:.1f} and ~{ci[1]:.1f} kt" in card["intervalSentence"]
    pv = out()["t4d"]["scopes"]["portfolio_v3"]
    assert f"{pv['selectingShareOfCredit'] * 100:.0f} %" in card["comparisonSentence"]
    lim = card["limitsSentence"]
    pub = next(r for r in f["sensitivity"] if r["id"] == "fwd_stake_published_by_D")
    cut = next(r for r in f["sensitivity"] if r["id"] == "fwd_credit_before_absent_window")
    ctl = next(r for r in f["sensitivity"] if r["id"] == "fwd_control_short_window")
    assert (f"today's 'absent' label is decided inside the credit window; credit ending before it (to "
            f"{cut['creditEnd']}) gives ~{cut['ktCh4PerYrCentralAction']['low']:.1f} kt") in lim
    assert ctl["clause"] not in lim, "the twelve-month control's reading replaces the six-month one's"
    assert f["controlFullLength"]["clause"] in lim
    for w in (f"{f['leaks']['universe']['absentAtDOutsideUniverse']} other notified",
              f"{f['leaks']['detectionPublication']['detections']} detections dated up to",
              f"~{pub['ktCh4PerYrCentralAction']['low']:.1f} kt", "no publication date"):
        assert w in lim, w
    import re

    assert lim.endswith(".") and not re.search(r"[.;:] [A-Z]", lim[:-1].replace("MARS", "mars")), "one sentence"


def test_the_published_headline_is_untouched_by_the_forward_scope():
    """The forward scope is reported next to portfolio_v3; the published card and headline stay T4d option d's."""
    o_ = out()
    assert o_["headline"]["central"] == o_["t4d"]["headline"]["central"]
    assert "forward" not in o_["card"]["headlineSentence"].lower()


def test_the_short_window_control_is_recomputed_and_read_by_its_rule(ind):
    rows = {r["id"]: r for r in fwd()["sensitivity"]}
    c, cut = rows["fwd_control_short_window"], rows["fwd_credit_before_absent_window"]
    ctl = ptime.FORWARD_POST_RESULT["controlShortWindow"]
    assert (c["origin"], c["creditEnd"], c["postResult"]) == (ctl["origin"], ctl["creditEnd"], True)
    m = _members(ind, d=ctl["origin"])
    k = _kt(ind, m, ctl["creditEnd"], "measured", d=ctl["origin"])
    assert c["members"] == len(m) and c["ktCh4PerYrCentralAction"]["low"] == pytest.approx(k["kt"], abs=0.01)
    prim = _row(fwd()["scopes"]["portfolio_forward"])["shareOfStake"]["low"]
    cs, xs = c["shareOfStakeCentralAction"]["low"], cut["shareOfStakeCentralAction"]["low"]
    assert c["shortWindowExplainsMost"] == (abs(cs - xs) < abs(cs - prim))
    assert f"{cs * 100:.2f} % of stake" in c["clause"] and f"{prim * 100:.2f} % over the full window" in c["clause"]


def test_the_full_length_control_is_recomputed_and_read_by_its_rule(ind):
    """FORWARD_CONTROL_LENGTH (task P2 item 1): twelve months of credit ending before today's absent window; near the
    full-window share iff inside the member-bootstrap share interval and nearer the full-window share than the cut
    row's; the limits sentence carries the reading the rule picks, never the other."""
    f = fwd()
    c, rule = f["controlFullLength"], ptime.FORWARD_CONTROL_LENGTH
    assert c["preRegistration"] == rule and c["setsValue"] is False
    win0 = json.loads((DERIVED / "join_v1.json").read_text(encoding="utf-8"))["summary"]["window"][0]
    assert (c["origin"], c["creditEnd"]) == (rule["origin"], rule["creditEnd"])
    assert o(c["creditEnd"]) == o(win0) - 1, "ends the day before today's absent window starts"
    assert c["months"] == 12, "as long as FORWARD's credit window"
    m = _members(ind, d=rule["origin"])
    k = _kt(ind, m, rule["creditEnd"], "measured", d=rule["origin"])
    assert c["members"] == len(m) and c["ktCh4PerYrCentralAction"]["low"] == pytest.approx(k["kt"], abs=0.01)
    assert k["selecting"] == c["selectingShareOfCredit"] == 0
    cs = c["shareOfStakeCentralAction"]["low"]
    assert cs == pytest.approx(c["ktCh4PerYrCentralAction"]["low"] / c["stakeLowKtCh4PerYrCredited"], abs=1e-4)
    prim = _row(f["scopes"]["portfolio_forward"])["shareOfStake"]["low"]
    cut = next(r for r in f["sensitivity"] if r["id"] == "fwd_credit_before_absent_window")
    xs = cut["shareOfStakeCentralAction"]["low"]
    ci = f["bootstrap"]["shareOfStakeCentral"]["ci"]
    near = ci[0] <= cs <= ci[1] and abs(cs - prim) < abs(cs - xs)
    assert c["nearFullWindow"] is near
    assert c["reading"] == (rule["readsIfNear"] if near else rule["readsOtherwise"])
    other = rule["readsOtherwise"] if near else rule["readsIfNear"]
    lim = f["card"]["limitsSentence"]
    assert c["reading"] in lim and other not in lim
    for w in (f"{cs * 100:.2f} % of stake", f"{xs * 100:.2f} %", f"{prim * 100:.2f} % over the full window",
              f"from {c['origin']} to {c['creditEnd']}", f"~{c['ktCh4PerYrCentralAction']['low']:.2f} kt"):
        assert w in c["clause"], w


def test_the_forward_hazard_sentence_is_the_scope_hazard_next_to_the_whole_window_one():
    f, o_ = fwd(), out()
    lam = f["scopes"]["portfolio_forward"]["reonsetHazard"]
    whole = o_["t4d"]["scopes"]["portfolio_v3"]["reonsetHazard"]
    s = f["card"]["hazardSentence"]
    assert (f"{lam['perYear']:.2f} a year: {lam['restarts']} restarts over {lam['eventFreeYears']} event-free "
            "facility-years") in s
    assert f"up to {ptime.FORWARD['D']}" in s
    assert f"{whole['perYear']:.2f} a year" in s and s.index(f"{lam['perYear']:.2f}") < s.index(f"{whole['perYear']:.2f}")


def test_the_forward_ledger_cost_is_the_members_own_episodes_in_the_credit_window():
    f, o_ = fwd(), out()
    bt = {x["name"]: x for x in json.loads((DERIVED / "backtest.json").read_text(encoding="utf-8"))["facilities"]}
    d, end = ptime.FORWARD["D"], ptime.FORWARD["creditEnd"]
    eps = sum(1 for n in f["scopes"]["portfolio_forward"]["memberNames"] for e in bt[n].get("episodes", [])
              if d < e["date"] <= end)
    led, pv_led = f["ledger"], o_["scopes"]["portfolio_v3"]["ledger"]
    years = (o(end) - o(d)) / 365.25
    assert led["episodes"] == eps > 0
    assert led["episodesPerYear"] == pytest.approx(eps / years, abs=1e-3)
    usd = eps / years * pv_led["usdPerEpisode"] + pv_led["usdPerCovenantYear"]
    assert led["usdPerYear"] == pytest.approx(usd, abs=0.05)
    gwp = o_["gwp"]["gwp100"]
    sc = f["scopes"]["portfolio_forward"]
    t_central = _row(sc)["kt_ch4_per_yr"]["low"] * 1000 * gwp
    assert led["usdPerTco2eGwp100Central"] == pytest.approx(led["usdPerYear"] / t_central, rel=1e-3)
    every = [led["usdPerYear"] / (v * 1000 * gwp) for r in sc["scenarios"] for v in r["kt_ch4_per_yr"].values()]
    assert led["usdPerTco2eGwp100Range"] == pytest.approx([min(every), max(every)], rel=1e-3)
    s, whole = f["card"]["costSentence"], _row(o_["scopes"]["portfolio_v3"])["ledgerUsdPerTco2eGwp100"]["low"]
    assert led["usdPerTco2eGwp100WholeWindow"] == whole
    assert f"{eps} episodes" in s and f"USD {led['usdPerYear']:,.0f} a year" in s
    assert s.index("forward") < s.index("whole window"), "the forward basis first, the whole-window one as comparison"
    assert s.startswith("Ledger cost only, not the cost of the work on site")


# ------------------------------------------------------------------------------------- credit as a share of stake
def test_every_forward_row_reports_its_share_of_the_credited_stake():
    f = fwd()
    sc = f["scopes"]["portfolio_forward"]
    for r in sc["scenarios"]:
        for b, v in r["kt_ch4_per_yr"].items():
            assert r["shareOfStake"][b] == pytest.approx(v / sc["stakeLowKtCh4PerYrCredited"], abs=1e-4)
    for r in f["sensitivity"]:
        for b, v in r["ktCh4PerYrCentralAction"].items():
            assert r["shareOfStakeCentralAction"][b] == pytest.approx(v / r["stakeLowKtCh4PerYrCredited"], abs=1e-4)
    assert f["card"]["shareOfStakeCentral"] == _row(sc)["shareOfStake"]["low"]


def test_portfolio_v3_non_selecting_credit_is_recomputed_independently(ind):
    """portfolio_v3 at the central action share with the credit of events containing a selecting plume removed."""
    o_ = out()
    pv = o_["t4d"]["scopes"]["portfolio_v3"]
    lam = pv["reonsetHazard"]["perYear"]
    sel = {c["mars_source"]: {pl["scene_timestamp"][:10] for pl in c["plumes"]}
           for c in json.loads((DERIVED / "join_v1.json").read_text(encoding="utf-8"))["candidates"]}
    names = [x["source_name"] for x in json.loads((DERIVED / "sourceset_v3.json").read_text())["sources"]]
    rc, dl = r_classes(), delays()
    kt = 0.0
    for n in names:
        b = ind["bt"][n]
        if not b.get("evaluated") or b.get("stake", {}).get("status") != "quantified":
            continue
        f = ind["by"][n]
        lag = lags_for(f, ind["plumes"])
        last = max(p["date"] for p in f["passes"] if p["date"] > f["start"])
        emit = b["stake"]["duty_cycle"] * (o(last) - o(b["start"]))
        c_f = 0.0
        for e in runs(f["passes"], f["start"]):
            if any(e["f"] <= x <= e["l"] for x in sel.get(n, ())):
                continue
            r = rc[str(e["n"]) if e["n"] < 3 else "3+"]
            c_f += r * sum(credit(end_of(e, "central") - (o(e["f"]) + lag[e["f"]] + x), lam) for x in dl) / len(dl)
        kt += b["stake"]["kt_ch4_per_yr"]["low"] * min(1.0, c_f / emit if emit else 0.0)
    ns = pv["nonSelecting"]
    assert ns["ktCh4PerYrCentral"] == pytest.approx(kt, abs=0.01)
    stake = o_["scopes"]["portfolio_v3"]["stakeLowKtCh4PerYr"]
    assert ns["centralShareOfStake"] == pytest.approx(kt / stake, abs=1e-4), "the non-selecting share"
    assert ns["allCreditKtCh4PerYrCentral"] == _row(pv)["kt_ch4_per_yr"]["low"]
    assert ns["allCreditCentralShareOfStake"] == pytest.approx(_row(pv)["kt_ch4_per_yr"]["low"] / stake, abs=1e-4)
    assert "shareOfStake" not in ns
    cw = fwd()["card"]["comparedWith"]
    assert (cw["nonSelectingKtCh4PerYrCentral"], cw["nonSelectingCentralShareOfStake"],
            cw["allCreditCentralShareOfStake"]) == (ns["ktCh4PerYrCentral"], ns["centralShareOfStake"],
                                                    ns["allCreditCentralShareOfStake"])
    assert cw["denominator"] == ns["denominator"]
    assert cw["forwardCentralShareOfStake"] == _row(fwd()["scopes"]["portfolio_forward"])["shareOfStake"]["low"]
    q = [n for n in names if ind["bt"][n].get("evaluated") and ind["bt"][n].get("stake", {}).get("status") == "quantified"]
    assert all(any(p["date"] > ind["by"][n]["start"] for p in ind["by"][n]["passes"]) for n in q), \
        "every quantified portfolio_v3 facility is credited: same denominator as the forward scope's credited stake"


# ------------------------------------------------------------------------------- the member bootstrap (post-result)
def test_credit_terms_reproduce_facility_credit_at_any_hazard(ind):
    """The bootstrap's vectorised credit equals facility_credit (central convention) on real members."""
    import projection

    inp = projection.load_inputs()
    prm = inp["params"]
    dl, rcls = ptime.response_delays(inp["carbSummary"], prm), ptime.r_by_plume_class(inp["carbSummary"], prm)
    r_of = {c: v["share"] for c, v in rcls.items()}
    checked = 0
    for n in sorted(_members(ind))[:40]:
        f = ptime.truncated(ind["by"][n], ptime.FORWARD["creditEnd"])
        if not ptime.has_pass_after_start(f):
            continue
        evs = [e for e in ptime.censored_events(f) if e["f"] > ptime.FORWARD["D"]]
        if not evs:
            continue
        lags = ptime.publication_lags(f)
        terms = ptime.credit_terms(f, evs, lags, dl, r_of)
        for h in (0, 1.0128, 2.5):
            want = ptime.facility_credit(f, evs, lags, dl, r_of, h, "central", 365.25)["credit"]
            assert ptime.credit_at(terms, h, 365.25) == pytest.approx(want, rel=1e-9, abs=1e-9)
        checked += 1
    assert checked > 0


def test_reonset_parts_add_up_to_the_scope_hazard(ind):
    sc = fwd()["scopes"]["portfolio_forward"]
    restarts, off = 0, 0.0
    for n in _members(ind):
        pre = ptime.truncated(ind["by"][n], ptime.FORWARD["D"])
        if ptime.has_pass_after_start(pre):
            r, x = ptime.reonset_parts(ptime.censored_events(pre), ptime.last_pass(pre))
            restarts, off = restarts + r, off + x
    assert sc["reonsetHazard"]["restarts"] == restarts
    assert sc["reonsetHazard"]["perYear"] == pytest.approx(restarts / (off / 365.25), abs=1e-4)


def test_the_bootstrap_follows_its_design_and_brackets_the_point():
    b = fwd()["bootstrap"]
    ev = json.loads((DERIVED / "projection_sources.json").read_text(encoding="utf-8"))["parameters"]["evidence"]
    assert (b["draws"], b["seed"], b["confidence"]) == (ev["bootstrapDraws"], ev["seed"], ev["confidence"])
    assert b["rule"] == ptime.FORWARD_POST_RESULT["bootstrap"]["rule"] and b["unit"] == "member"
    lo, hi = b["ktCh4PerYrCentral"]["ci"]
    assert lo < b["ktCh4PerYrCentral"]["point"] == _row(fwd()["scopes"]["portfolio_forward"])["kt_ch4_per_yr"]["low"] < hi
    assert 0 < b["shareOfStakeCentral"]["ci"][0] < b["shareOfStakeCentral"]["ci"][1] < 1


# ------------------------------------------------------------------------- the rolling-origin hold-out (reported)
def test_every_rolling_origin_is_the_rule_recomputed_independently(ind):
    r = fwd()["rolling"]
    assert r["preRegistration"] == ptime.FORWARD_ROLLING
    rows = {x["origin"]: x for x in r["origins"]}
    assert sorted(rows) == sorted([ptime.FORWARD["D"], *ptime.FORWARD_ROLLING["origins"]])
    end = ptime.FORWARD_ROLLING["creditEnd"]
    for d, row in rows.items():
        m = _members(ind, d=d)
        assert row["members"] == len(m), d
        lo, hi = _kt(ind, m, end, "measured", d=d), _kt(ind, m, end, "none", d=d)
        assert row["ktCh4PerYrCentralAction"]["low"] == pytest.approx(lo["kt"], abs=0.01), d
        assert row["ktCh4PerYrCentralAction"]["high"] == pytest.approx(hi["kt"], abs=0.01), d
        assert row["reonsetHazardPerYear"] == pytest.approx(lo["lambda"], abs=1e-4), d
        assert lo["selecting"] == row["selectingShareOfCredit"] == 0, d
        for b, v in row["ktCh4PerYrCentralAction"].items():
            assert row["shareOfStakeCentralAction"][b] == pytest.approx(v / row["stakeLowKtCh4PerYrCredited"], abs=1e-4)
    stake = sum(x["stakeLowKtCh4PerYrCredited"] for x in rows.values())
    for b in ("low", "high"):
        assert r["pooled"]["shareOfStakeCentralAction"][b] == pytest.approx(
            sum(x["ktCh4PerYrCentralAction"][b] for x in rows.values()) / stake, abs=1e-4)
    assert rows[ptime.FORWARD["D"]]["ktCh4PerYrCentralAction"] == _row(fwd()["scopes"]["portfolio_forward"])[
        "kt_ch4_per_yr"]
    more = [x["shareOfStakeCentralAction"]["low"] for d, x in rows.items() if d != ptime.FORWARD["D"]]
    card = fwd()["card"]
    assert card["rollingShareOfStakeRange"] == [min(more), max(more)]
    assert card["rollingSentence"] == (
        "At three more origins registered after this result, with overlapping credit windows, the share is "
        f"{min(more) * 100:.1f}–{max(more) * 100:.1f} %.")
