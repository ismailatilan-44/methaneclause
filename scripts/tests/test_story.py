"""Story page (app/story.html): its data is current and every headline number equals the committed source."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import build_story_data  # noqa: E402

STORY = json.loads((ROOT / "app/data/story.json").read_text())
S, X = STORY["scene"], STORY["extras"]
HTML = (ROOT / "app/story.html").read_text()
JS = (ROOT / "app/story.js").read_text()


def load(rel):
    return json.loads((ROOT / rel).read_text())


def test_story_json_is_current():
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts/build_story_data.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_headline_count_is_the_facility_radius_basis():
    case = load("data/derived/cases/TKM_S_276.json")
    assert S["headline"] == case["post_mars_last_plume"]
    assert S["stake"]["detected"] == case["post_mars_last_plume"]["n_cm_detected"]


def test_sepolia_numbers_match_the_run():
    tn = load("data/derived/testnet_run.json")
    steps = {s["step"]: s for s in tn["steps"]}
    assert S["sepolia"]["evidenceHash"] == tn["evidenceHash"]
    assert [s["step"] for s in S["sepolia"]["steps"]] == [s["step"] for s in tn["steps"]]
    assert int(steps["proposeEpisode"]["rebateWei"]) > 0
    times = {s["step"]: s.get("blockTime") for s in S["sepolia"]["steps"]}
    assert times["finalize"] and times["withdraw_buyer"] and times["finalize"] <= times["withdraw_buyer"]


def test_tamper_case_lowers_the_rate_below_its_tier():
    lead = next(p for p in S["cmPlumes"] if p["id"] == S["episode"]["plumeId"])
    assert S["tamper"]["from"] == lead["rate"] == S["episode"]["rateKgH"]
    assert S["tamper"]["to"] < S["policy"]["tierMinKgH"][1] <= S["tamper"]["from"]


def test_earn_back_chapter_adds_up():
    o = S["dza"]["replayA"]["outcome"]
    assert o["paidBp"] + o["forfeitedBp"] == 10000
    counted = sum(1 for x in S["dza"]["clean"]["scenes"] if x["qualified"] and not x["afterRelapse"])
    assert counted == o["counted"]
    sp = S["dza"]["replayA"]["sponsor"]
    assert int(sp["releasedWei"]) + int(sp["forfeitedWei"]) == int(sp["openedByBuyerWei"])


def test_verify_view_uses_the_committed_protocol():
    assert S["verify"]["steps"] == load("data/derived/verification_time.json")["manual"]["steps"]


def test_context_layers_present():
    assert X["passes"] and all(p["footprint"] for p in X["passes"])
    assert any(f["properties"].get("@class") == "pipeline" for f in X["osm"]["features"])
    assert X["cmWindByDate"].get(S["episode"]["detectedAtIso"][:10])


def test_page_hygiene():
    for url in re.findall(r'<(?:script[^>]*\ssrc|link[^>]*\shref)="(https://[^"]+)"', HTML):
        if "fonts.googleapis.com" not in url:
            assert re.search(r"@\d+\.\d+\.\d+/", url), url
    assert HTML.count('integrity="sha384-') >= 2
    for needle in ("Esri", "Mapzen", "Data by Carbon Mapper®", "UNEP IMEO MARS, CC BY-NC-SA 4.0",
                   "OpenStreetMap contributors", "Open-Meteo", "Copernicus", "What this does not prove"):
        assert needle in HTML + JS, needle
    assert 'href="story.html"' in (ROOT / "app/index.html").read_text()
    lead = f"{S['tamper']['from']:,}"
    for typed in (lead, str(S["tamper"]["from"])):
        assert typed not in JS, f"the lead rate {typed} is typed into story.js; it must come from story.json"
