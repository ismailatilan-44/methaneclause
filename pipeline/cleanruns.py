"""Clean runs after a second detection, and the u(N) table (spec §12).

Why: a pool that refunds on clean passes must say, with our own catalogue, how often a quiet run is followed by a
plume. This script measures that on the Carbon Mapper timelines the pipeline already holds, before any refund is
designed around it.

Definition (also written into the output as `definition`):
  pass          a UTC date on which Carbon Mapper observed the unit (`observation_dates`), plus every detection date
                (4 detection dates in the file are missing from `observation_dates`; they count as detected passes)
  detected      the date is in `detection_dates` (any instrument); for an instrument view, a plume of that unit on
                that date carries one of the instruments
  eligible      the unit has >= 2 detected passes
  run           the first sequence of >= 3 consecutive non-detected passes after the second detected pass; the
                run "qualifies" at its 3rd clean pass
  next pass     the first pass after the qualifying pass (detected or not); units whose run ends the record have none
  base rate     detected / passes over every pass before the run start, in the units with a run; also given without
                the two detections every eligible unit must have (`..._excl_eligibility`), because eligibility
                selects units that were detected before the run
  hazard        for k = 1, 2, ...: among units whose first k-1 passes after the qualifying pass were all clean and
                that have a k-th pass, the share detected on the k-th (k = 1 is the next-pass rate)
  re-detected   any detected pass after the qualifying pass. RIGHT-CENSORED: it grows with how long Carbon Mapper kept
                looking, so it is reported only with its breakdown by follow-up length and is not a headline figure
  unit          a catalogue cell, not a facility: Carbon Mapper sources that share a plume_id (CRITERION.md rule 4:
                250 m / 1000 m twins list the same plume ids) are merged, nearest pair first, only while every two
                members stay within RADIUS_KM (1.5 km, the facility radius of the contract); a cell's passes and
                detections are the union of its members'. Source-level figures are given beside it
  u(N)          1 - 0.025^(1/N): upper limit of the two-sided 95 % Clopper-Pearson interval for the per-pass
                detection probability after N passes and 0 detections; bp = round(10000 u), N = 1..32

All instruments (airborne GAO / AVIRIS-NG / AVIRIS-3 included) and unqualified passes (no cloud, footprint or MARS
filter: that needs scene data for every source, which is not fetched); a Tanager + EMIT subset is given beside it. The
population is the join neighbourhood (sources within 1.5 km of a MARS notified O&G 'absent' target, plus the case
sources), not the whole Carbon Mapper catalogue.

    cd pipeline && .venv/bin/python cleanruns.py      # writes data/derived/cleanruns.json
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from common import DERIVED, RADIUS_KM, ROOT, haversine_km, sha256_file, write_json

MIN_RUN = 3
N_MAX = 32
HAZARD_K = 10
INSTRUMENTS = ["tan", "emi", "av3", "ang", "GAO"]
SATELLITE_SUBSET = frozenset({"tan", "emi"})
FOLLOW_UP_BINS = [("0", 0, 0), ("1", 1, 1), ("2", 2, 2), ("3-5", 3, 5), ("6-10", 6, 10), ("11+", 11, 10**9)]
UNIT_LABEL = "catalogue cells, not facilities; all instruments; unqualified passes"
Unit = dict  # date -> (set of observing instruments, set of detecting instruments; empty = not detected)


def u_bp(n: int) -> int:
    if n < 1:
        raise ValueError("N must be >= 1")
    return round(10000 * (1 - 0.025 ** (1 / n)))


UPPER_BOUND_BP = [u_bp(n) for n in range(1, N_MAX + 1)]


def first_run(passes: list[tuple[str, bool]], min_run: int = MIN_RUN) -> dict | None:
    """`passes` sorted by date as (date, detected). None if < 2 detections or no run of `min_run` after the 2nd."""
    det_idx = [i for i, (_, d) in enumerate(passes) if d]
    if len(det_idx) < 2:
        return None
    clean = 0
    for j in range(det_idx[1] + 1, len(passes)):
        if passes[j][1]:
            clean = 0
            continue
        clean += 1
        if clean == min_run:
            start = j - min_run + 1
            end = j
            while end + 1 < len(passes) and not passes[end + 1][1]:
                end += 1
            later = [d for d, det in passes[j + 1:] if det]
            nxt = passes[j + 1] if j + 1 < len(passes) else None
            return {"second_detection": passes[det_idx[1]][0], "run_start": passes[start][0],
                    "qualifying_pass": passes[j][0], "run_length": end - start + 1,
                    "next_pass": nxt[0] if nxt else None, "next_pass_detected": nxt[1] if nxt else None,
                    "redetected_later": bool(later), "first_redetection": later[0] if later else None,
                    "passes_after_qualifying": len(passes) - j - 1}
    return None


def cells_from_plumes(names: list[str], plumes, coords: dict[str, tuple[float, float]] | None = None,
                      max_km: float | None = None) -> dict[str, list[str]]:
    """Merge sources that share a plume_id. With `coords` {name: (lat, lon)} and `max_km`, candidate pairs are merged
    nearest first and only while every two members of the merged cell stay within `max_km` (no chaining beyond the
    facility radius). Cell id = alphabetically first member."""
    members = {n: [n] for n in names}
    owner = {n: n for n in names}
    by_plume: dict[str, list[str]] = defaultdict(list)
    for p in plumes:
        if p["source_name"] in owner:
            by_plume[p["plume_id"]].append(p["source_name"])
    pairs = set()
    for srcs in by_plume.values():
        u = sorted(set(srcs))
        pairs.update((a, b) for i, a in enumerate(u) for b in u[i + 1:])

    def dist(a, b):
        return float(haversine_km(coords[a][0], coords[a][1], coords[b][0], coords[b][1]))

    order = sorted(pairs, key=lambda ab: ((dist(*ab) if coords else 0.0), ab))
    for a, b in order:
        ca, cb = owner[a], owner[b]
        if ca == cb:
            continue
        if coords is not None and max_km is not None:
            if any(dist(x, y) > max_km for x in members[ca] for y in members[cb]):
                continue
        keep, drop = min(ca, cb), max(ca, cb)
        members[keep] += members.pop(drop)
        for m in members[keep]:
            owner[m] = keep
    return {min(v): sorted(v) for v in members.values()}


def cell_diameter_km(cell: list[str], coords: dict[str, tuple[float, float]]) -> float:
    return max((float(haversine_km(coords[a][0], coords[a][1], coords[b][0], coords[b][1]))
                for i, a in enumerate(cell) for b in cell[i + 1:]), default=0.0)


def build_units(timelines: dict, plumes, groups: dict[str, list[str]] | None = None) -> dict[str, Unit]:
    """Per unit: date -> (observing instruments, detecting instruments)."""
    det_instr: dict[tuple[str, str], set] = defaultdict(set)
    for p in plumes:
        det_instr[(p["source_name"], str(p["scene_timestamp"])[:10])].add(p["instrument"])
    per_source: dict[str, Unit] = {}
    for name, tl in timelines.items():
        u: dict[str, tuple[set, set]] = {}
        for d, inst in tl["obs"]:
            u[d] = (set(inst.split("+")), set())
        for d in tl["det"]:
            obs = u.get(d, (set(), set()))[0]
            got = det_instr.get((name, d)) or obs or {"unk"}
            u[d] = (obs or set(got), set(got))
        per_source[name] = u
    if groups is None:
        return per_source
    out = {}
    for cid, members in groups.items():
        merged: dict[str, tuple[set, set]] = {}
        for m in members:
            for d, (o, k) in per_source[m].items():
                mo, mk = merged.get(d, (set(), set()))
                merged[d] = (mo | o, mk | k)
        out[cid] = merged
    return out


def passes_for(unit: Unit, instrument: str | set | frozenset | None = None) -> list[tuple[str, bool]]:
    keep = None if instrument is None else ({instrument} if isinstance(instrument, str) else set(instrument))
    out = []
    for d in sorted(unit):
        obs, det = unit[d]
        if keep is None:
            out.append((d, bool(det)))
        elif keep & obs or keep & det:
            out.append((d, bool(keep & det)))
    return out


def _ci(k: int, n: int) -> list[float] | None:
    if n == 0:
        return None
    from stake import clopper_pearson

    lo, hi = clopper_pearson(k, n)
    return [round(lo, 4), round(hi, 4)]


def _rate(k: int, n: int) -> dict:
    return {"detected": k, "passes": n, "rate": round(k / n, 4) if n else None, "ci95": _ci(k, n)}


def summarise(units: dict[str, Unit], instrument: str | set | frozenset | None = None) -> dict:
    runs, ge2 = {}, 0
    base_k = base_n = excl_k = excl_n = 0
    hazard = [[0, 0] for _ in range(HAZARD_K)]
    follow = {b: [0, 0] for b, _, _ in FOLLOW_UP_BINS}
    for uid, unit in units.items():
        ps = passes_for(unit, instrument)
        if sum(1 for _, d in ps if d) >= 2:
            ge2 += 1
        r = first_run(ps)
        if not r:
            continue
        runs[uid] = r
        before = [x for d, x in ps if d < r["run_start"]]
        base_k, base_n = base_k + sum(before), base_n + len(before)
        excl_k, excl_n = excl_k + sum(before) - 2, excl_n + len(before) - 2
        after = [x for d, x in ps if d > r["qualifying_pass"]]
        for k, det in enumerate(after[:HAZARD_K]):
            hazard[k][0] += 1
            hazard[k][1] += int(det)
            if det:
                break
        for b, lo, hi in FOLLOW_UP_BINS:
            if lo <= r["passes_after_qualifying"] <= hi:
                follow[b][0] += 1
                follow[b][1] += int(r["redetected_later"])
    n = len(runs)
    red = sum(r["redetected_later"] for r in runs.values())
    followed = [r for r in runs.values() if r["passes_after_qualifying"] > 0]
    with_next = [r for r in runs.values() if r["next_pass"] is not None]
    nxt_det = sum(r["next_pass_detected"] for r in with_next)
    by_inst: dict[str, dict] = {}
    for uid, r in runs.items():
        if r["next_pass"] is None:
            continue
        key = "+".join(sorted(units[uid][r["next_pass"]][0]))
        b = by_inst.setdefault(key, {"n": 0, "detected": 0})
        b["n"] += 1
        b["detected"] += int(r["next_pass_detected"])
    lengths = sorted(r["run_length"] for r in runs.values())
    return {
        "units": len(units), "units_ge2_detections": ge2, "with_run": n,
        "with_next_pass": len(with_next), "no_next_pass": n - len(with_next),
        "next_pass_detected": nxt_det,
        "next_pass_detection_rate": round(nxt_det / len(with_next), 4) if with_next else None,
        "next_pass_detection_ci95": _ci(nxt_det, len(with_next)),
        "base_rate_before_run": _rate(base_k, base_n),
        "base_rate_before_run_excl_eligibility": _rate(excl_k, excl_n),
        "hazard_after_run": [{"k": k + 1, "at_risk": a, "detected": d, "rate": round(d / a, 4) if a else None,
                              "ci95": _ci(d, a)} for k, (a, d) in enumerate(hazard) if a],
        "next_pass_by_instrument": dict(sorted(by_inst.items())),
        "run_length_median": lengths[len(lengths) // 2] if lengths else None,
        "redetected_later_right_censored": True,
        "redetected_later": red, "redetected_later_share": round(red / n, 4) if n else None,
        "redetected_later_ci95": _ci(red, n),
        "followed": len(followed),
        "redetected_share_of_followed": round(red / len(followed), 4) if followed else None,
        "redetected_by_follow_up": [{"passes_after": b, "units": u, "redetected": r} for b, (u, r) in follow.items()],
    }


def card_sentence(cells: int, next_detected: int, next_n: int, next_ci: list[float], hazard: list[dict]) -> str:
    """The card / README sentence of spec-v2 §15 ("after B1-fix"), filled from the numbers, never typed. The range
    "after longer quiet runs" is the hazard at k = 2..4 passes after the qualifying pass."""
    pct = lambda x: round(100 * x)  # noqa: E731
    later = [h["detected"] / h["at_risk"] for h in hazard if 2 <= h["k"] <= 4 and h["at_risk"]]
    return (f"A quiet run is weak evidence: in Carbon Mapper's catalogue ({cells} cells; all instruments, unqualified "
            f"passes) the pass after three non-detections still saw a plume {pct(next_detected / next_n)} % of the "
            f"time ({next_detected}/{next_n}, 95 % CI {pct(next_ci[0])}–{pct(next_ci[1])} %), and the passes after "
            f"longer quiet runs saw one {pct(min(later))}–{pct(max(later))} % of the time. That is why the refund is "
            "capped at the rebate, paid in installments, and cancelled by the next attested detection.")


def _rel(p: Path) -> str:
    return str(p.relative_to(ROOT))


def main() -> None:
    import pandas as pd

    tl_path, pl_path = DERIVED / "cm_timelines.json", DERIVED / "cm_plumes.csv"
    timelines = json.loads(tl_path.read_text())
    plumes = pd.read_csv(pl_path, usecols=["source_name", "plume_id", "scene_timestamp", "instrument"]
                         ).to_dict("records")
    coords = {n: (t["lat"], t["lon"]) for n, t in timelines.items()}
    src_units = build_units(timelines, plumes)
    groups = cells_from_plumes(sorted(timelines), plumes, coords=coords, max_km=RADIUS_KM)
    cell_units = build_units(timelines, plumes, groups=groups)
    by_source = summarise(src_units)
    by_cell = summarise(cell_units)
    sat_cell = summarise(cell_units, SATELLITE_SUBSET)
    sat_source = summarise(src_units, SATELLITE_SUBSET)
    by_instrument = {i: {"cell": summarise(cell_units, i), "source": summarise(src_units, i)} for i in INSTRUMENTS}
    cases = {}
    for f in sorted((DERIVED / "cases").glob("*_S_*.json")):
        if f.name.count(".") > 1:
            continue  # *.clean_passes.json
        names = [n for n in json.loads(f.read_text())["cm_source_ids"] if n in timelines]
        unit = build_units(timelines, plumes, groups={"case": names})["case"]
        cases[f.stem] = {"cm_sources": names, "first_run": first_run(passes_for(unit))}
    diam = [cell_diameter_km(v, coords) for v in groups.values()]

    def card(s: dict) -> dict:
        return {"cells_with_run": s["with_run"],
                "next_pass": {"detected": s["next_pass_detected"], "passes": s["with_next_pass"],
                              "rate": s["next_pass_detection_rate"], "ci95": s["next_pass_detection_ci95"]},
                "hazard_k2_to_k4": [h for h in s["hazard_after_run"] if 2 <= h["k"] <= 4]}

    def windows(s: dict) -> dict:
        return {"before_run_start": s["base_rate_before_run"],
                "before_run_start_excl_eligibility": s["base_rate_before_run_excl_eligibility"]}

    out = {
        "schema": "methaneclause.cleanruns/1",
        "generatedFrom": [{"path": _rel(p), "sha256": sha256_file(p)}
                          for p in (tl_path, pl_path, Path(__file__).resolve())],
        "definition": __doc__.split("Definition (also written into the output as `definition`):")[1]
        .split("    cd pipeline")[0].strip(),
        "unitLabel": UNIT_LABEL,
        "population": f"{len(timelines)} Carbon Mapper sources in cm_timelines.json (join neighbourhood + case "
                      f"sources), {len(groups)} catalogue cells (twin merge bounded at {RADIUS_KM} km); passes are "
                      "unqualified (no cloud / footprint / MARS filter)",
        "cellRadiusKm": RADIUS_KM, "cells": len(groups), "largest_cell_size": max(len(v) for v in groups.values()),
        "max_cell_diameter_km": round(max(diam), 3),
        "min_run": MIN_RUN,
        "sources_with_run": by_source["with_run"],
        "cells_with_run": by_cell["with_run"],
        "next_pass_detection_rate": by_cell["next_pass_detection_rate"],
        "card": {"unitLabel": UNIT_LABEL, "sentence": card_sentence(len(groups), by_cell["next_pass_detected"], by_cell["with_next_pass"],
                                           by_cell["next_pass_detection_ci95"], by_cell["hazard_after_run"]),
                 "all_instruments": card(by_cell), "tanager_emit": card(sat_cell),
                 "note": "a reason for the cap, not evidence of repair; no 'usual rate' comparator is quoted "
                         "(spec-v2 §15 after B1-fix)"},
        "selection_windows_not_base_rates": {
            "label": "selection window, not a base rate: every candidate 'usual rate' depends on the definition "
                     "(13-35 %) because eligibility and the run are defined by detections; not quoted",
            "all_instruments": windows(by_cell), "tanager_emit": windows(sat_cell)},
        "redetected_later_note": "right-censored (grows with follow-up length); not a headline figure, see "
                                 "by_unit.*.redetected_by_follow_up",
        "rightCensored": True,
        "redetected_later": by_cell["redetected_later"],
        "redetected_later_share": by_cell["redetected_later_share"],
        "by_unit": {"cell": by_cell, "source": by_source},
        "subset_tanager_emit": {"instruments": sorted(SATELLITE_SUBSET), "cell": sat_cell, "source": sat_source},
        "by_instrument": by_instrument,
        "cases": cases,
        "upper_bound_bp": UPPER_BOUND_BP,
        "upper_bound_definition": "u(N) = 1 - 0.025^(1/N), two-sided 95 % Clopper-Pearson upper limit (one-sided "
                                  "97.5 %) with 0 detections in N passes; bp = round(10000 u); index 0 = N 1",
    }
    write_json(DERIVED / "cleanruns.json", out)
    for name, s in (("all instruments", by_cell), ("Tanager+EMIT", sat_cell)):
        print(f"cleanruns [{name}]: {len(groups)} cells, {s['with_run']} with a run; next pass detected "
              f"{s['next_pass_detected']}/{s['with_next_pass']} = {s['next_pass_detection_rate']} "
              f"{s['next_pass_detection_ci95']}; hazard k=1..4 "
              + ", ".join(f"{h['detected']}/{h['at_risk']}" for h in s["hazard_after_run"][:4]))
    print("  card: " + out["card"]["sentence"])
    print(f"  sources with a run {by_source['with_run']}; max cell diameter {out['max_cell_diameter_km']} km")


if __name__ == "__main__":
    main()
