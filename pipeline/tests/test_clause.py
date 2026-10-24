"""The model clause (docs/clause/model-clause.md) and the PDF's feasibility blocks (pipeline/clause.py): every value is
rendered from the committed policy, nothing is typed by hand, every policy key and contract function the clause names
exists, every quoted fact is verbatim in its research file, and a bad policy fails closed. Skipped where docs/clause/ is
not part of the checkout."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest

import clause

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not clause.CLAUSE.exists(), reason="docs/clause/ is not in this checkout")

GEN = re.compile(r"<!-- gen:(\w+):begin -->.*?<!-- gen:\1:end -->", re.S)
# Names with digits that are not values of ours: standards, legal identifiers, headings and cross-references.
NAMES = re.compile(
    r"^#{2} \d+\. |\*\*\d+\.\d+\*\*|[Cc]lauses? \d+(\.\d+)?|Schedule \d|\(EU\) \d{4}/\d{3,4}|Art\. \d+(\(\d+\))?"
    r"|RFC 8785|EIP-712|SHA-256|CC BY-NC-SA 4\.0|\[P90\]|^\| \d+ ", re.M)


def text() -> str:
    return clause.CLAUSE.read_text(encoding="utf-8")


def hand_written(t: str) -> str:
    t = GEN.sub("", t)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    return re.sub(r"`[^`]*`", "", t)


def norm(s: str) -> str:
    return " ".join(s.split())


def test_generated_blocks_are_current():
    assert clause.apply(text(), clause.clause_blocks()) == text(), "run make clause"
    names = set(re.findall(r"<!-- gen:(\w+):begin -->", text()))
    assert names == {"policy", "trigger", "tiers", "challenge", "refund", "term", "schedule"}


def test_no_value_is_typed_by_hand():
    left = NAMES.sub("", hand_written(text()))
    typed = [line.strip() for line in left.splitlines() if re.search(r"\d", line)]
    assert not typed, f"hand-typed digits outside gen blocks: {typed}"


def test_blocks_carry_the_policy_values():
    pol = json.loads(clause.POLICY_FILE.read_text())["policies"]["POLICY_V2"]
    b = clause.clause_blocks()
    assert pol["policyHash"] in b["policy"] and pol["onchainParamsHash"] in b["policy"]
    core, pool = pol["policy"]["core"], pol["policy"]["pool"]
    assert f"k = {core['k']} of n = {core['n']}" in b["trigger"]
    for n, share, _, _ in pool["installments"]:
        assert f"| {n} | {share / 100:g} % of the cap |" in b["refund"]
    assert f"{pool['cureTerm'] // 86400} days" in b["refund"]
    assert all(f"{m:,} kg/h" in b["tiers"] for m in core["rebateSchedule"]["tierMinKgH"])


def test_every_policy_key_named_exists():
    pol = json.loads(clause.POLICY_FILE.read_text())["policies"]["POLICY_V2"]["policy"]
    keys = set(re.findall(r"`((?:core|pool)\.[A-Za-z.]+)`", text()))
    assert len(keys) > 20
    for key in keys:
        node = pol
        for part in key.split("."):
            assert isinstance(node, dict) and part in node, f"{key} is not a field of POLICY_V2"
            node = node[part]


def test_every_contract_function_in_the_map_exists():
    sched = text().split("## Schedule 2")[1]
    calls = set(re.findall(r"`(\w+)\.(\w+)`", sched))
    assert {c for c, _ in calls} >= {"MethaneCovenant", "IncentivePool", "KeyRegistry"}
    for contract, fn in calls:
        src = (ROOT / "contracts" / "src" / f"{contract}.sol").read_text()
        assert re.search(rf"function {fn}\s*\(", src), f"{contract}.{fn} does not exist"
    rows = [r for r in sched.splitlines() if r.startswith("| ") and not r.startswith("| Clause")]
    numbered = {r.split()[1] for r in rows}
    assert numbered >= {str(i) for i in range(1, 12)}, "every clause section maps to a function"


def test_wording():
    t = text()
    assert t.lower().count("not legal advice") == 1 and "not legal advice" in "\n".join(t.splitlines()[:4])
    assert "This clause records verdicts and does not certify emission reductions." in t
    assert "no admission of liability" in t.lower()
    for banned in ("quantum-safe", "tamper-proof", "trustless", "unhackable", "compliant", "guarantee"):
        assert banned not in t.lower(), banned


NOTES = ROOT / "docs" / "research"  # the author's research notes; not published, so this test skips without them


@pytest.mark.skipif(not NOTES.is_dir(), reason="the research notes are not part of this checkout")
def test_research_quotes_are_verbatim():
    src = clause.sources()
    rows = src["precedents"] + src["euDates"] + src["settlementLaw"] + [src["renewalExample"], src["noPriceTerm"]]
    body = norm("\n".join(p.read_text(encoding="utf-8") for p in NOTES.rglob("*.md")))
    quotes = [q for row in rows for q in row["checks"]]
    assert len(quotes) >= 20 and all(row["checks"] for row in rows)
    missing = [q for q in quotes if norm(q) not in body]
    assert not missing, f"not verbatim in the research notes: {missing}"


def test_pdf_blocks_name_every_source_and_the_no_price_term_line():
    b = clause.pdf_blocks()
    src = clause.sources()
    assert len(src["precedents"]) == 5
    for r in src["precedents"]:
        assert r["url"] in b["precedents"] and f"read {r['read']}" in b["precedents"] and r["tag"] in b["precedents"]
    assert "No public LNG contract with a methane-intensity price term was found" in b["precedents"]
    for e in src["euDates"]:
        if e["id"] != "penaltyPause":
            assert e["date"] in b["pilot"], e["id"]
    ss = json.loads((clause.DERIVED / "sourceset_v3.json").read_text())
    assert f"{ss['n']}-facility" in b["pilot"] and "[SIM]" in b["pilot"]


def test_pilot_names_k_plus_two_organisations_so_a_contest_keeps_a_spare():
    """docs/PRODUCTION.md item 8: a contest needs k + 1 signatures; the pilot names n = k + 2 organisations (2 of 4 for
    k = 2), rendered from core.k, not from POLICY_V2's deployed n, which stays the demo setting."""
    b = clause.pdf_blocks()["pilot"]
    k = _policy()["policies"][clause.ONCHAIN]["policy"]["core"]["k"]
    n = clause.pilot_n(k)
    assert n == k + 2
    word = clause.NUMBER_WORDS[n]
    assert f"**Observers.** {word.capitalize()} organisations with a quorum of {k} of {n}" in b
    assert "a contest, which needs k + 1, keeps a spare" in b and f"register the {word} organisations" in b
    assert sum(c for c, _ in clause.PILOT_ORGS) == n
    assert "Three organisations" not in b and "register the three" not in b


def _policy() -> dict:
    return json.loads(clause.POLICY_FILE.read_text())


@pytest.mark.parametrize("edit", [
    lambda p: p["policies"]["POLICY_V2"]["policy"]["pool"]["installments"][0].__setitem__(1, 2000),
    lambda p: p["policies"]["POLICY_V2"]["policy"]["pool"]["installments"][1].__setitem__(0, 2),
    lambda p: p["policies"]["POLICY_V2_VERDICT_ONLY"]["policy"]["pool"].__setitem__("cureTerm", 86400),
    lambda p: p["policies"]["POLICY_V2"]["policy"]["pool"].__setitem__("holdWindow", 86400),
    lambda p: p["policies"]["POLICY_V2"]["policy"]["core"].__setitem__("challengeWindow", 3600),
    lambda p: p["policies"]["POLICY_V2_VERDICT_ONLY"].__setitem__("settlementMode", "ONCHAIN"),
    lambda p: p["policies"].pop("POLICY_V2_VERDICT_ONLY"),
], ids=["shares", "thresholds", "modes-differ", "hold", "partial-day", "mode-label", "missing"])
def test_bad_policy_fails_closed(edit):
    p = copy.deepcopy(_policy())
    edit(p)
    with pytest.raises(SystemExit):
        clause.clause_blocks(p)


def test_bad_source_fails_closed():
    s = json.loads(clause.SOURCES.read_text())
    s["precedents"][0]["read"] = ""
    with pytest.raises(SystemExit):
        clause.pdf_blocks(src=s)
