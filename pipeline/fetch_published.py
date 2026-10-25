"""Re-read the published tables the projection's action share is built from (NETWORK, ask first).

    make fetch-published                                        # = fetch_published.py --write (REFRESH=1 re-downloads)
    cd pipeline && .venv/bin/python fetch_published.py          # check only, writes nothing under data/derived/
    cd pipeline && .venv/bin/python fetch_published.py --write  # rebuild the local row file, check it reproduces the summary
    cd pipeline && .venv/bin/python fetch_published.py --write-summary  # the publisher changed the appendix: new summary

Downloads the CARB airborne summary report (`carb-2024-airborne`) and its appendix (`carb-2024-appendix`), both named
in data/derived/projection_sources.json, into the raw-data folder (never committed), checks each SHA-256 against the
recorded one, extracts the text with `pdftotext -layout` (poppler) and checks that Table 3's oil and gas rows are
exactly the rows of `parameters.responseScenarios.rule` and the source's `tableRows`, and that the oil and gas rows of
Appendix Tables A1 (2021) and A2 (2023), one per incidence, reproduce the committed aggregates
data/derived/carb_summary.json exactly (counts by year, outcome and plume count, the join-key count, the Type A delays
and the rows' RFC 8785 hash). The per-incidence rows are not in the tracked tree: --write rebuilds them locally as
data/derived/carb_incidences.json (git-ignored) and checks that they reproduce the summary. Exits non-zero on any
difference. --write-summary rewrites carb_summary.json from the appendix whose SHA-256 is recorded.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from common import DERIVED, RAW, sha256_file

SOURCES = DERIVED / "projection_sources.json"
SUMMARY = DERIVED / "carb_summary.json"  # committed: the aggregates pipeline/projection.py reads
INCIDENCES = DERIVED / "carb_incidences.json"  # local only, not tracked: the rows, rebuilt by --write
OUT_DIR = RAW / "published"
# Appendix Tables A1 and A2: the year each table covers (its caption) and the outcome labels as printed; a wrapped
# label ("Not" / "Found", "Did not" / "respond") is matched on its first line
APPENDIX_TABLES = (("Table A1.", "2021"), ("Table A2.", "2023"))
OUTCOMES = {"Type A": "typeA", "Type B": "typeB", "Type C": "typeC", "Not found": "notFound", "Not": "notFound",
            "Did not": "didNotRespond"}
ROW = re.compile(r"^\s*(\d+)\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d+)\s+(.+?)\s{2,}(Type [ABC]|Not found|Not|Did not)\b",
                 re.I)
# the five date columns of a row, left to right; a row's cells are placed by the page's column offsets (pdftotext
# -layout keeps them) within DATE_TOLERANCE characters. Only the Type A rows keep dates (the time from first plume to
# mitigation is what the projection's time factor needs, model.t4c); kept: notification and mitigation
DATE = re.compile(r"\d{1,2}/\d{1,2}/\d{4}")
DATE_COLUMNS = ("earliestPlume", "notified", "inspected", "mitigated", "replied")
DATE_TOLERANCE = 3
DATED_OUTCOMES = ("typeA",)
DATED_KEYS = ("notified", "mitigated")


def table3_rows(layout_text: str, sector: str) -> list[str]:
    """The `sector` rows of Table 3 (between its caption and Table 4's), whitespace collapsed, in page order."""
    start = layout_text.index("Table 3.")
    end = layout_text.index("Table 4.", start)
    rows = []
    for line in layout_text[start:end].splitlines():
        flat = re.sub(r"\s+", " ", line).strip()
        if flat.startswith(sector + " "):
            rows.append(flat)
    return rows


def expected_rows(rule: dict) -> list[str]:
    return [f"{rule['sector']} " + " ".join(str(r[c]) for c in rule["columns"]) for r in rule["rows"]]


def check(layout_text: str, sources: dict) -> list[str]:
    """Differences between the published table and the committed rule (empty list = they agree)."""
    rule = sources["parameters"]["responseScenarios"]["rule"]
    src = sources["sources"][rule["source"]]
    got = table3_rows(layout_text, rule["sector"])
    errors = []
    if got != expected_rows(rule):
        errors.append(f"Table 3 {rule['sector']} rows {got} differ from responseScenarios.rule {expected_rows(rule)}")
    if got != src["tableRows"]:
        errors.append(f"Table 3 {rule['sector']} rows {got} differ from {rule['source']}.tableRows {src['tableRows']}")
    return errors


def _iso(us_date: str) -> str:
    m, d, y = us_date.split("/")
    return f"{y}-{int(m):02d}-{int(d):02d}"


def _date_columns(page: str) -> list[int] | None:
    """Start offsets of the five date columns on one page of the layout text, read from the first row that fills all
    five (earliest plume, notification, inspection, mitigation, reply); None if no row on the page does."""
    for line in page.splitlines():
        if ROW.match(line):
            pos = [m.start() for m in DATE.finditer(line)]
            if len(pos) == len(DATE_COLUMNS):
                return pos
    return None


def _row_dates(line: str, cols: list[int] | None, rid: int) -> dict:
    """The dates of one row, each assigned to the page's nearest date column (a blank cell is an absent key); fails
    closed if the page has no full row to read the columns from or a date sits between columns."""
    found = [(m.start(), m.group()) for m in DATE.finditer(line)]
    if len(found) == len(DATE_COLUMNS):
        return {c: _iso(d) for c, (_, d) in zip(DATE_COLUMNS, found)}
    if cols is None:
        raise ValueError(f"incidence {rid}: a blank date cell on a page with no full row to place it")
    out = {}
    for pos, d in found:
        i = min(range(len(cols)), key=lambda k: abs(cols[k] - pos))
        if abs(cols[i] - pos) > DATE_TOLERANCE or DATE_COLUMNS[i] in out:
            raise ValueError(f"incidence {rid}: date {d} at offset {pos} fits no column of {cols}")
        out[DATE_COLUMNS[i]] = _iso(d)
    return out


def appendix_rows(layout_text: str, sector: str) -> list[dict]:
    """The `sector` rows of Appendix Tables A1 and A2, one per incidence, in page order: incidence ID, campaign year
    (the table's), earliest plume date (ISO), number of plumes and outcome (Table 3's column names); for the outcomes
    in DATED_OUTCOMES also the notification and mitigation dates as printed (a blank cell stays absent)."""
    rows, year = [], None
    for page in layout_text.split("\f"):
        cols = _date_columns(page)
        for line in page.splitlines():
            year = next((int(y) for cap, y in APPENDIX_TABLES if line.strip().startswith(cap)), year)
            m = ROW.match(line)
            if not m or year is None or m.group(6).strip() != sector:
                continue
            label = next(k for k in OUTCOMES if k.lower() == m.group(7).lower())
            row = {"id": int(m.group(1)), "year": year,
                   "earliestPlume": f"{m.group(4)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}",
                   "plumes": int(m.group(5)), "outcome": OUTCOMES[label]}
            if row["outcome"] in DATED_OUTCOMES:
                dates = _row_dates(line, cols, row["id"])
                row.update({k: dates[k] for k in DATED_KEYS if k in dates})
            rows.append(row)
    return rows


def check_incidences(layout_text: str, incidences: dict) -> list[str]:
    """Differences between the published appendix rows and a local row file (empty list = they agree)."""
    got = appendix_rows(layout_text, incidences["sector"])
    if got == incidences["rows"]:
        return []
    diff = [(a, b) for a, b in zip(got, incidences["rows"]) if a != b][:3]
    return [f"Appendix Tables A1/A2 {incidences['sector']} rows differ from {INCIDENCES.name} "
            f"({len(got)} published, {len(incidences['rows'])} local; first differences {diff})"]


MISSING = ("no emission rate, location or plume identifier per incidence (none is published in the appendix), so the "
           "rows cannot weight the action share by mass")


def incidences_file(rows: list[dict], src_id: str, src: dict, sector: str) -> dict:
    return {"schema": "methaneclause.carb_incidences/1",
            "label": "CARB airborne campaigns 2021 and 2023: every oil and gas incidence sent to an operator, one row "
                     "each, as published in the appendix's Tables A1 and A2 (pipeline/fetch_published.py --write; a "
                     "local file, git-ignored: the tracked tree keeps only data/derived/carb_summary.json)",
            "source": src_id, "url": src["url"], "sha256": src["sha256"], "readOn": src["readOn"], "sector": sector,
            "columns": ["id", "year", "earliestPlume", "plumes", "outcome", *DATED_KEYS],
            "datedOutcomes": list(DATED_OUTCOMES),
            "datesNote": "notified and mitigated are the appendix's notification and mitigation dates, kept for the "
                         "outcomes in datedOutcomes only; a blank cell in the appendix is an absent key; dates are as "
                         "printed, not corrected",
            "missing": MISSING, "rowsJcsSha256": rows_hash(rows), "rows": rows}


def rows_hash(rows: list[dict]) -> str:
    """0x SHA-256 of the rows' RFC 8785 bytes: the summary records it, so a rebuilt row file is checked bit for bit."""
    from canon import evidence_hash

    return evidence_hash(rows)


def _days(a: str, b: str) -> int:
    """b - a in days, signed, from the dates as printed (pipeline/projection_time.py counts a negative as 0)."""
    from datetime import date

    return (date.fromisoformat(b) - date.fromisoformat(a)).days


def aggregates(rows: list[dict]) -> dict:
    """Everything pipeline/projection.py needs from the rows and nothing per incidence: counts by year and outcome
    (Table 3's check), by plume count and outcome (the plume-weighted row and the plume-class shares), the number of
    rows whose (year, earliest plume, plumes) key is unique (the weighting record), and for each dated outcome the
    histograms {days: incidences} of the day differences notified -> mitigated (rows with both dates) and earliest
    plume -> mitigated (rows with a mitigation date), signed as printed."""
    from collections import Counter

    outcomes = sorted(set(OUTCOMES.values()))

    def by(key) -> dict:
        c = Counter((str(key(r)), r["outcome"]) for r in rows)
        return {k: {o: c[(k, o)] for o in outcomes} for k in sorted({k for k, _ in c}, key=int)}

    def histogram(days) -> dict:
        c = Counter(days)
        return {str(d): c[d] for d in sorted(c)}

    keys = Counter((r["year"], r["earliestPlume"], r["plumes"]) for r in rows)
    delays = {o: {"notifiedToMitigatedDays": histogram(_days(r["notified"], r["mitigated"]) for r in rows
                                                       if r["outcome"] == o and "notified" in r and "mitigated" in r),
                  "earliestPlumeToMitigatedDays": histogram(_days(r["earliestPlume"], r["mitigated"]) for r in rows
                                                            if r["outcome"] == o and "mitigated" in r)}
              for o in DATED_OUTCOMES}
    return {"incidences": len(rows), "years": sorted({r["year"] for r in rows}), "outcomes": outcomes,
            "byYearOutcome": by(lambda r: r["year"]), "byPlumesOutcome": by(lambda r: r["plumes"]),
            "uniqueJoinKey": sum(1 for r in rows if keys[(r["year"], r["earliestPlume"], r["plumes"])] == 1),
            "delays": delays}


def aggregates_hash(agg: dict) -> str:
    """0x SHA-256 of the aggregates' RFC 8785 bytes: pipeline/projection.py refuses a summary that no longer matches."""
    from canon import evidence_hash

    return evidence_hash(agg)


def summary_file(rows: list[dict], src_id: str, src: dict, sector: str) -> dict:
    agg = aggregates(rows)
    return {"schema": "methaneclause.carb_summary/1",
            "label": "CARB airborne campaigns 2021 and 2023, oil and gas incidences sent to an operator (appendix "
                     "Tables A1 and A2): the aggregates the projection uses; no per-incidence row is kept here "
                     "(pipeline/fetch_published.py --write-summary)",
            "source": src_id, "url": src["url"], "sha256": src["sha256"], "readOn": src["readOn"], "sector": sector,
            "rowsFile": {"path": f"data/derived/{INCIDENCES.name}", "tracked": False,
                         "rebuild": "make fetch-published (network, ask first): downloads the appendix, checks its "
                                    "SHA-256, extracts the rows and checks that they reproduce this file",
                         "rows": len(rows), "rowsJcsSha256": rows_hash(rows)},
            "datesNote": "delays are histograms {days: number of incidences} of the day differences between the "
                         "appendix's dates as printed, not corrected, for the outcomes in delays only; a row with a "
                         "blank mitigation cell is in neither histogram, a row with a blank notification cell is not "
                         "in notifiedToMitigatedDays",
            "missing": MISSING, "aggregatesJcsSha256": aggregates_hash(agg), "aggregates": agg}


def check_summary(rows: list[dict], summary: dict) -> list[str]:
    """Differences between what the rows give and the committed summary (empty list = the rows reproduce it)."""
    errors = []
    if rows_hash(rows) != summary["rowsFile"]["rowsJcsSha256"]:
        errors.append(f"the {len(rows)} rows hash to {rows_hash(rows)}, not to the summary's rowsJcsSha256 "
                      f"{summary['rowsFile']['rowsJcsSha256']}")
    agg = aggregates(rows)
    diff = sorted(k for k in set(agg) | set(summary["aggregates"]) if agg.get(k) != summary["aggregates"].get(k))
    if diff:
        errors.append(f"the rows do not reproduce {SUMMARY.name}: {', '.join(diff)} differ")
    return errors


def fetch(src: dict, dest: Path, refresh: bool) -> Path:
    if refresh or not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(src["url"], headers={"User-Agent": "methaneclause-fetch-published"})
        with urllib.request.urlopen(req, timeout=120) as r:
            dest.write_bytes(r.read())
    got = sha256_file(dest)
    if got != src["sha256"]:
        raise SystemExit(f"{dest.name}: sha256 {got} differs from the recorded {src['sha256']} (the publisher changed "
                         "the file: re-read it before updating projection_sources.json)")
    return dest


def layout_text(pdf: Path) -> str:
    exe = shutil.which("pdftotext")
    if not exe:
        raise SystemExit("pdftotext (poppler) not on PATH")
    return subprocess.run([exe, "-layout", str(pdf), "-"], capture_output=True, text=True, check=True).stdout


def main() -> None:
    from common import write_json

    sources = json.loads(SOURCES.read_text(encoding="utf-8"))
    rule = sources["parameters"]["responseScenarios"]["rule"]
    refresh = os.environ.get("REFRESH") == "1"
    sid, aid = rule["source"], rule["incidences"]["source"]
    pdf = fetch(sources["sources"][sid], OUT_DIR / f"{sid}.pdf", refresh)
    errors = check(layout_text(pdf), sources)
    appendix = layout_text(fetch(sources["sources"][aid], OUT_DIR / f"{aid}.pdf", refresh))
    rows = appendix_rows(appendix, rule["sector"])
    if "--write-summary" in sys.argv[1:]:
        write_json(SUMMARY, summary_file(rows, aid, sources["sources"][aid], rule["sector"]))
        print(f"fetch_published: wrote {SUMMARY.name} from {len(rows)} rows")
    summary = json.loads(SUMMARY.read_text(encoding="utf-8"))
    errors += check_summary(rows, summary)
    if "--write" in sys.argv[1:] and not errors:
        write_json(INCIDENCES, incidences_file(rows, aid, sources["sources"][aid], rule["sector"]))
        print(f"fetch_published: wrote {INCIDENCES.name} ({len(rows)} rows; local, git-ignored)")
    if INCIDENCES.exists():
        local = json.loads(INCIDENCES.read_text(encoding="utf-8"))
        errors += check_incidences(appendix, local) + check_summary(local["rows"], summary)
    if errors:
        sys.exit("fetch_published: " + "; ".join(errors))
    print(f"fetch_published: {sid} and {aid} sha256 ok; Table 3 rows match responseScenarios.rule; Tables A1/A2 rows "
          f"reproduce {SUMMARY.name}" + (f"; {INCIDENCES.name} matches them" if INCIDENCES.exists() else ""))


if __name__ == "__main__":
    main()
