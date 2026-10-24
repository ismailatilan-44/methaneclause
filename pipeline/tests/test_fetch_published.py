"""pipeline/fetch_published.py: the CARB tables the projection's action share is built from can be re-read from the
publisher. Offline: the parser and the aggregates on layout texts built from synthetic rows (and tampered ones), the
committed summary's provenance; with the local row file (data/derived/carb_incidences.json, not tracked, rebuilt by
make fetch-published) also that the rows parse back and reproduce data/derived/carb_summary.json, skipped with the
reason when it is absent. Network (NETWORK=1, ask first): download, SHA-256, pdftotext -layout, Table 3 rows equal the
rule, Appendix Tables A1/A2 oil and gas rows reproduce the summary."""
from __future__ import annotations

import copy
import json
import os
import shutil
from pathlib import Path

import pytest

import fetch_published as fp

SRC = Path(__file__).resolve().parents[2] / "data" / "derived" / "projection_sources.json"


def _sources() -> dict:
    return json.loads(SRC.read_text(encoding="utf-8"))


def _layout(rows: list[str]) -> str:
    """A page laid out like pdftotext -layout: Table 1's rows (same sector name) before Table 3, Table 4's after."""
    pad = "            "
    return "\n".join(["Table 1. Plume, source, operator, and incidence statistics", f"{pad}Oil & Gas   78   40   10*   32*",
                      "Table 3. Incidence follow-up statistics based on operator response to emails.",
                      *(pad + r.replace(" ", "    ") for r in rows),
                      f"{pad}Landfills   30   1   5   15   1   8",
                      "Table 4. On-the-ground inspection rates", f"{pad}Oil & Gas   32   NA   100%   100%   100%   70%"])


def test_the_committed_rows_pass_and_a_changed_row_is_caught():
    s = _sources()
    rule = s["parameters"]["responseScenarios"]["rule"]
    good = fp.expected_rows(rule)
    assert good == s["sources"][rule["source"]]["tableRows"]
    assert fp.table3_rows(_layout(good), rule["sector"]) == good, "only Table 3's rows are read"
    assert fp.check(_layout(good), s) == []
    bad = copy.deepcopy(good)
    bad[1] = bad[1].rsplit(" ", 1)[0] + " 11"
    assert len(fp.check(_layout(bad), s)) == 2
    drifted = copy.deepcopy(s)
    drifted["parameters"]["responseScenarios"]["rule"]["rows"][0]["typeA"] += 1
    assert fp.check(_layout(good), drifted), "a rule row that is not the published row is caught"


@pytest.mark.skipif(os.environ.get("NETWORK") != "1" or not shutil.which("pdftotext"),
                    reason="network: NETWORK=1 make test-pipeline (ask first) and pdftotext on PATH")
def test_network_the_published_table_matches_the_rule(tmp_path):
    s = _sources()
    rule = s["parameters"]["responseScenarios"]["rule"]
    pdf = fp.fetch(s["sources"][rule["source"]], tmp_path / "carb.pdf", refresh=True)
    assert fp.check(fp.layout_text(pdf), s) == []


# ------------------------------------------------------------------------------ CARB Appendix Tables A1 and A2 (T4b)
INC = Path(__file__).resolve().parents[2] / "data" / "derived" / "carb_incidences.json"


def _appendix(rows: list[dict]) -> str:
    """A text laid out like pdftotext -layout on the appendix: Table A1's rows, then Table A2's; a landfill row and a
    wrapped two-line outcome ("Not" / "found") as in the publisher's PDF."""
    names = {"typeA": "Type A", "typeB": "Type B", "typeC": "Type C", "notFound": "Not found",
             "didNotRespond": "Did not respond"}

    def us(iso: str) -> str:
        y, m, d = iso.split("-")
        return f"{int(m)}/{int(d)}/{y}"

    def line(rid, earliest, plumes, sector, label, cells):
        """Date cells at fixed offsets (notification, inspection, mitigation, reply), blanks left blank."""
        s = f"{rid:>4}   {earliest:<12}{plumes:<4}{sector:<12}{label}".ljust(50)
        for i, c in enumerate(cells):
            s = s.ljust(50 + 13 * i) + (c or "")
        return s.rstrip()

    pages = []
    for year, caption in ((2021, "Table A1. List of all incidences that were sent to operators in 2021"),
                          (2023, "Table A2. List of all incidences that were sent to operators in 2023.")):
        out = [caption, line(99, f"6/1/{year}", 1, "Landfills", "Type A",
                             [f"6/2/{year}", f"6/3/{year}", f"6/4/{year}", f"6/5/{year}"])]
        for r in (r for r in rows if r["year"] == year):
            label = names[r["outcome"]]
            first = {"notFound": "Not", "didNotRespond": "Did not"}.get(r["outcome"], label)
            cells = [us(r["notified"]) if "notified" in r else f"6/15/{year}", None,
                     us(r["mitigated"]) if "mitigated" in r else None, f"7/30/{year}"]
            if r["outcome"] != "typeA":
                cells = [f"6/15/{year}", None, None, None]
            out.append(line(r["id"], us(r["earliestPlume"]), r["plumes"], "Oil & Gas", first, cells))
            if first != label:
                out.append("                                 " + label[len(first):].strip())
        pages.append("\n".join(out))
    return "\f".join(pages)


SUMMARY = Path(__file__).resolve().parents[2] / "data" / "derived" / "carb_summary.json"
NO_ROWS = ("data/derived/carb_incidences.json is absent: the CARB rows are not in the tracked tree (data/derived/"
           "carb_summary.json is); make fetch-published (network, ask first) rebuilds them locally")
# synthetic rows (not CARB's): every outcome, a wrapped label, a blank mitigation cell, a mitigation printed before the
# notification, and two rows that share the (year, earliest plume, plumes) join key
SYNTHETIC = [
    {"id": 1, "year": 2021, "earliestPlume": "2021-11-05", "plumes": 4, "outcome": "typeB"},
    {"id": 4, "year": 2021, "earliestPlume": "2021-11-05", "plumes": 1, "outcome": "notFound"},
    {"id": 7, "year": 2021, "earliestPlume": "2021-11-06", "plumes": 2, "outcome": "typeA", "notified": "2021-11-12",
     "mitigated": "2021-11-19"},
    {"id": 9, "year": 2021, "earliestPlume": "2021-11-05", "plumes": 1, "outcome": "typeA", "notified": "2021-11-12"},
    {"id": 3, "year": 2023, "earliestPlume": "2023-08-01", "plumes": 1, "outcome": "didNotRespond"},
    {"id": 5, "year": 2023, "earliestPlume": "2023-08-02", "plumes": 3, "outcome": "typeC"},
    {"id": 8, "year": 2023, "earliestPlume": "2023-08-02", "plumes": 1, "outcome": "typeA", "notified": "2023-08-10",
     "mitigated": "2023-08-09"},
]


def _summary() -> dict:
    return json.loads(SUMMARY.read_text(encoding="utf-8"))


def _local_rows() -> dict:
    if not INC.exists():
        pytest.skip(NO_ROWS)
    return json.loads(INC.read_text(encoding="utf-8"))


def test_the_appendix_parser_reads_back_rows_and_a_changed_row_is_caught():
    rows, sector = copy.deepcopy(SYNTHETIC), _sources()["parameters"]["responseScenarios"]["rule"]["sector"]
    inc = fp.incidences_file(rows, "carb-2024-appendix", {"url": "u", "sha256": "s", "readOn": "r"}, sector)
    assert fp.appendix_rows(_appendix(rows), sector) == rows, "only the oil and gas rows of A1 and A2 are read"
    assert fp.check_incidences(_appendix(rows), inc) == []
    bad = copy.deepcopy(rows)
    bad[0]["outcome"] = "typeA"
    assert fp.check_incidences(_appendix(bad), inc), "a published row that differs from the local one is caught"
    late = copy.deepcopy(rows)
    next(r for r in late if "mitigated" in r)["mitigated"] = "2099-01-01"
    assert fp.check_incidences(_appendix(late), inc), "a changed mitigation date is caught"


def test_a_date_between_columns_fails_closed():
    cols = [5, 42, 55, 68, 81]
    ok = "   11/5/2021" + " " * 30 + "11/12/2021   11/12/2021" + " " * 16 + "12/17/2021"
    assert fp._row_dates(ok, cols, 11) == {"earliestPlume": "2021-11-05", "notified": "2021-11-12",
                                           "inspected": "2021-11-12", "replied": "2021-12-17"}
    with pytest.raises(ValueError, match="fits no column"):
        fp._row_dates("   11/5/2021" + " " * 36 + "11/12/2021", cols, 11)


def test_the_aggregates_count_every_row_and_keep_no_row():
    """What the summary keeps, on synthetic rows: counts by year, plume count and outcome, the unique join keys, and
    the signed delays as {days: incidences} histograms (a blank mitigation cell is in neither; projection_time expands
    them in ascending order and counts a negative as 0)."""
    import projection_time as ptime

    agg = fp.aggregates(SYNTHETIC)
    zero = dict.fromkeys(agg["outcomes"], 0)
    assert agg["incidences"] == len(SYNTHETIC) and agg["years"] == [2021, 2023]
    assert agg["byYearOutcome"] == {"2021": {**zero, "typeA": 2, "typeB": 1, "notFound": 1},
                                    "2023": {**zero, "typeA": 1, "typeC": 1, "didNotRespond": 1}}
    assert agg["byPlumesOutcome"] == {"1": {**zero, "typeA": 2, "notFound": 1, "didNotRespond": 1},
                                      "2": {**zero, "typeA": 1}, "3": {**zero, "typeC": 1}, "4": {**zero, "typeB": 1}}
    assert agg["uniqueJoinKey"] == len(SYNTHETIC) - 2, "rows 4 and 9 share year, earliest plume and plume count"
    assert agg["delays"] == {"typeA": {"notifiedToMitigatedDays": {"-1": 1, "7": 1},
                                       "earliestPlumeToMitigatedDays": {"7": 1, "13": 1}}}
    prm = _sources()
    assert ptime.response_delays({"aggregates": agg}, prm) == [0, 7]
    assert ptime.carb_first_plume_delays({"aggregates": agg}, prm) == [7, 13]
    assert ptime.r_by_plume_class({"aggregates": agg}, prm)["1"] == {"typeA": 2, "n": 4, "share": 0.5}
    summary = fp.summary_file(SYNTHETIC, "x", {"url": "u", "sha256": "s", "readOn": "r"}, "Oil & Gas")
    assert "rows" not in summary and summary["rowsFile"]["rowsJcsSha256"] == fp.rows_hash(SYNTHETIC)
    assert fp.check_summary(SYNTHETIC, summary) == []
    moved = copy.deepcopy(SYNTHETIC)
    moved[2]["mitigated"] = "2021-11-20"
    errs = fp.check_summary(moved, summary)
    assert len(errs) == 2 and "rowsJcsSha256" in errs[0] and "delays differ" in errs[1]


def test_the_summary_records_its_source():
    s, cs = _sources(), _summary()
    src = s["sources"][cs["source"]]
    assert (cs["url"], cs["sha256"], cs["readOn"]) == (src["url"], src["sha256"], src["readOn"])
    rule = s["parameters"]["responseScenarios"]["rule"]
    assert cs["sector"] == rule["sector"] and rule["incidences"]["source"] == cs["source"]
    assert rule["incidences"]["file"] == "data/derived/" + SUMMARY.name
    assert set(cs["aggregates"]["outcomes"]) <= set(rule["columns"]) - {rule["of"]}
    assert cs["aggregatesJcsSha256"] == fp.aggregates_hash(cs["aggregates"])
    assert cs["rowsFile"] == {**cs["rowsFile"], "path": "data/derived/" + INC.name, "tracked": False}


def test_the_local_rows_parse_back_and_reproduce_the_summary():
    """With the row file make fetch-published rebuilds: it parses back from its layout text and reproduces the
    committed summary exactly (rows hash and every aggregate); the Type A rows carry their dates."""
    inc = _local_rows()
    rows = inc["rows"]
    assert fp.appendix_rows(_appendix(rows), inc["sector"]) == rows
    assert fp.check_summary(rows, _summary()) == []
    a = [r for r in rows if r["outcome"] in inc["datedOutcomes"]]
    assert a and all("notified" in r and r["notified"] >= r["earliestPlume"] for r in a)
    assert all(set(r) <= {"id", "year", "earliestPlume", "plumes", "outcome"} for r in rows if r not in a)
    assert len([r for r in a if "mitigated" not in r]) < len(a) / 10, "the appendix leaves few mitigation cells blank"


@pytest.mark.skipif(os.environ.get("NETWORK") != "1" or not shutil.which("pdftotext"),
                    reason="network: NETWORK=1 make test-pipeline (ask first) and pdftotext on PATH")
def test_network_the_published_appendix_reproduces_the_summary(tmp_path):
    s, cs = _sources(), _summary()
    pdf = fp.fetch(s["sources"][cs["source"]], tmp_path / "appendix.pdf", refresh=True)
    assert fp.check_summary(fp.appendix_rows(fp.layout_text(pdf), cs["sector"]), cs) == []
