"""Instrument detection floors and GWP values, each with the citation actually read and its status .

    cd pipeline && .venv/bin/python floors.py     # writes data/derived/floors.json

A value is used by the clause only through `policy.POOL_V2["instrumentFloorKgH"]`; this file says where each number
comes from and how strong that source is. Quotes are verbatim and short; "status" is one of:
  read                   the quote was read at the URL on `readOn`
  read-secondary         read, but the source is not the instrument team's own measurement of a detection limit
  not-read               the source could not be opened on `readOn` (HTTP 403 or too large); the value is not cited
  [pending citation]     no value is asserted until the source is read
Floor sources were opened on 2026-10-15 (web fetch); the GWP-100 PDF was read on 2026-10-16; the IPCC AR6 WG1
Chapter 7 PDF (Table 7.15: GWP-20 primary, GWP-100 cross-check) was read on 2026-10-15; this file is the record.
"""
from __future__ import annotations

from pathlib import Path

from common import DERIVED, write_json

READ_ON = "2026-10-15"

FLOORS = {
    "tan": {
        "kgH": 180,
        "basis": "90 % probability of detection, conservative end of the published 90-180 kg/h (varies with imaging "
                 "mode; Carbon Mapper also quotes about 100 kg/h as the predicted 90 % limit) — spec-v2 §15 2026-10-16",
        "status": "read",
        "sources": [
            {"title": "Carbon Mapper — Science", "url": "https://carbonmapper.org/work/science", "year": None,
             "locator": "Tanager section", "quote": "predicted 90% detection limit of about 100 kg/hr",
             "status": "read"},
            {"title": "Tanager Methane Performance Specifications", "year": 2024,
             "url": "https://carbonmapper.org/articles/tanager-methane-performance-specifications",
             "locator": "specification table and footnote",
             "quote": "CH4 90% Probability of Detection | 90-180 kg/hr* (varies with imaging mode); "
                      "*3 m/s wind, 35 deg Solar Zenith Angle, 25% albedo, 30 m GSD",
             "status": "read"},
        ],
    },
    "emi": {
        "kgH": 700,
        "basis": "90 % probability of detection under favourable conditions (Carbon Mapper authors, workshop "
                 "abstract) — the same basis as Tanager's floor; spec-v2 §15 2026-10-16",
        "status": "read-secondary",
        "note": "Read-secondary: a conference abstract, not a peer-reviewed detection-limit study. The 0.3 t/h in the "
                "primary EMIT paper (Thorpe et al. 2023) is the smallest emission observed, not a detection limit "
                "(the v1 value 300 kg/h came from it via the UNEP MARS FAQ table).",
        "sources": [
            {"title": "Thorpe et al., Attribution of individual methane and carbon dioxide emission sources using "
                      "EMIT observations from space, Science Advances 9(46) eadh2391", "year": 2023,
             "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC10656068",
             "locator": "Results; Discussion",
             "quote": "the lowest emission observed with EMIT was 0.3 tonnes CH4 hour−1 ... Analysis of additional "
                      "observations are required to determine EMIT's detection limit",
             "status": "read"},
            {"title": "Ayasse, Cusworth, O'Neill, Duren — Detection Limits and a Multi-Sensor Persistence Framework "
                      "for Airborne and Satellite Acquisitions of Methane Plumes (IWGGMS-20 abstract)", "year": None,
             "url": "https://cpaess.ucar.edu/abstract-iwggms-20/detection-limits-and-multi-sensor-persistence-"
                    "framework-airborne-and-satellite",
             "locator": "abstract",
             "quote": "under favorable conditions the 90% probability of detection for EMIT is 700 kg/hr and the "
                      "10% probability of detection is 275 kg/hr",
             "status": "read"},
            {"title": "Jacob et al., Quantifying methane emissions from the global scale down to point sources using "
                      "satellite observations, ACP 22, 9617", "year": 2022,
             "url": "https://acp.copernicus.org/articles/22/9617/2022/", "locator": "Section 5.2",
             "quote": "Instruments in the ∼500 kg h−1 class include the land hyperspectral sensors (PRISMA, EnMAP, "
                      "EMIT)",
             "status": "read"},
            {"title": "UNEP IMEO MARS — Frequently asked questions (table 'Approx. Minimum Detection Limit (tonnes "
                      "CH4/hour)', EMIT 0.3)", "year": None,
             "url": "https://methanedata.unep.org/frequently-asked-questions", "locator": "How does MARS use satellite data?",
             "quote": None,
             "status": "not-read (HTTP 403 on 2026-10-15)"},
        ],
    },
}

IPCC_READ_ON = "2026-10-15"
IPCC_TABLE_7_15 = {
    "title": "Forster, P. et al., 2021: The Earth's Energy Budget, Climate Feedbacks, and Climate Sensitivity. In: "
             "Climate Change 2021: The Physical Science Basis. Contribution of Working Group I to the Sixth Assessment "
             "Report of the IPCC, Chapter 7",
    "year": 2021,
    "url": "https://www.ipcc.ch/report/ar6/wg1/downloads/report/IPCC_AR6_WGI_Chapter07.pdf",
    "pdfSha256": "470d2662fdd212421617a32c684e8106a4221a7235bdfff40a278eff02febfa9",
    "locator": "Table 7.15 'Emissions metrics for selected species', rows 'CH4-fossil' and 'CH4-non fossil', printed "
               "page 1017 (page 95 of the 132-page chapter PDF)",
    "quote": "CH4-fossil ... GWP-20 82.5 ± 25.8, GWP-100 29.8 ± 11; CH4-non fossil ... GWP-20 79.7 ± 25.8, "
             "GWP-100 27.0 ± 11",
    "rangeLocator": "Executive Summary, printed page 929 (PDF page 7): 'Very likely (5–95%) ranges are presented "
                    "unless otherwise indicated.'",
    "quotedBy": "maintainer's agent, from the PDF text (pdftotext), 2026-10-15",
}

GWP = {
    "gwp100_fossil_ch4": {
        "value": 29.8, "status": "read", "readOn": "2026-10-16",
        "note": "Read from the PDF on 2026-10-16. Same value as v1's policy.POLICY gwp100.",
        "source": {"title": "GHG Protocol, IPCC Global Warming Potential Values, version 2.0 (AR6 values)",
                   "year": 2024, "date": "2024-08-07",
                   "url": "https://ghgprotocol.org/sites/default/files/2024-08/"
                          "Global-Warming-Potential-Values%20%28August%202024%29.pdf",
                   "locator": "AR6 table, row 'Methane - fossil'",
                   "quote": "Methane – fossil ... 29.8", "quotedBy": "maintainer, from the PDF (2026-10-16)"},
        "uncertainty": {"plusMinus": 11, "range": "very likely (5–95 %)"},
        "ipccCheck": {"status": "read", "readOn": IPCC_READ_ON, "agrees": True, "source": IPCC_TABLE_7_15},
    },
    "gwp20_fossil_ch4": {
        "value": 82.5, "status": "read", "readOn": IPCC_READ_ON,
        "note": "Read in the IPCC chapter PDF (primary source); the earlier secondary source (Wikipedia, read "
                "2026-10-15) gave the same value. Not inside any hashed policy (spec-v2 §4).",
        "uncertainty": {"plusMinus": 25.8, "range": "very likely (5–95 %)"},
        "source": IPCC_TABLE_7_15,
    },
}

IPCC_AR6_CH4 = {  # Table 7.15 as printed (value, ± very likely range): both methane rows, both horizons
    "fossil": {"gwp20": [82.5, 25.8], "gwp100": [29.8, 11]},
    "nonFossil": {"gwp20": [79.7, 25.8], "gwp100": [27.0, 11]},
}


def check_ipcc(pdf: str) -> dict:
    """Re-read Table 7.15 from the downloaded chapter PDF (untracked; pass its path) and compare with IPCC_AR6_CH4.
    Uses poppler's `pdftotext` on page 95 only; raises on a hash or value mismatch."""
    import hashlib
    import re
    import subprocess

    if hashlib.sha256(Path(pdf).read_bytes()).hexdigest() != IPCC_TABLE_7_15["pdfSha256"]:
        raise ValueError("IPCC chapter PDF: SHA-256 differs from the one recorded")
    text = subprocess.run(["pdftotext", "-f", "95", "-l", "95", "-layout", pdf, "-"], check=True,
                          capture_output=True, text=True).stdout
    if "Table 7.15 | Emissions metrics" not in text or "1017" not in text:
        raise ValueError("IPCC chapter PDF: Table 7.15 is not on PDF page 95 (printed 1017)")
    num = r"(\d+(?:\.\d+)?) ± (\d+(?:\.\d+)?)"
    found = {}
    for key, label, nxt in (("fossil", "CH4-fossil", "CH4-non fossil"), ("nonFossil", "CH4-non fossil", "N2O")):
        start = text.index(label)
        row = text[start:text.index(nxt, start + len(label))]
        # columns in order: lifetime, radiative efficiency, GWP-20, GWP-100, GWP-500, GTP-50, GTP-100, CGTP-50, -100
        pairs = [[float(a), float(b)] for a, b in re.findall(num, row)]
        if len(pairs) != 9:
            raise ValueError(f"IPCC Table 7.15 row {label!r}: {len(pairs)} value ± range pairs, expected 9")
        found[key] = {"gwp20": pairs[2], "gwp100": pairs[3]}
    want = {k: {h: [float(x) for x in v] for h, v in d.items()} for k, d in IPCC_AR6_CH4.items()}
    if found != want:
        raise ValueError(f"IPCC Table 7.15 read {found}, recorded {want}")
    return found


def main() -> None:
    import sys

    from policy import POOL_V2

    if len(sys.argv) > 2 and sys.argv[1] == "--check-ipcc":
        print(f"IPCC AR6 WG1 Table 7.15 (p. 1017) re-read from the PDF: {check_ipcc(sys.argv[2])}; matches floors.py")
        return
    assert {k: v["kgH"] for k, v in FLOORS.items()} == POOL_V2["instrumentFloorKgH"]
    assert GWP["gwp20_fossil_ch4"]["value"] == IPCC_AR6_CH4["fossil"]["gwp20"][0]
    assert GWP["gwp100_fossil_ch4"]["value"] == IPCC_AR6_CH4["fossil"]["gwp100"][0]
    out = {"schema": "methaneclause.floors/1", "readOn": READ_ON,
           "statusLegend": __doc__.split("\"status\" is one of:")[1].split("Sources were")[0].strip(),
           "instrumentFloorKgH": FLOORS, "gwp": GWP, "ipccAr6Ch4Table715": IPCC_AR6_CH4}
    write_json(DERIVED / "floors.json", out)
    print("floors: " + ", ".join(f"{k} {v['kgH']} kg/h ({v['status']})" for k, v in FLOORS.items())
          + "; GWP-20 " + GWP["gwp20_fossil_ch4"]["status"])


if __name__ == "__main__":
    main()
