"""The model contract clause and the PDF's feasibility blocks, rendered from committed files (no number typed by hand).

Inputs:
- `data/derived/policy_v2.json` (`make policy-v2`): every clause parameter, by its policy key (`core.k`, `pool.cureTerm`…);
- `contracts/deployments/11155111.json`: the core's proposer and challenger bonds (fixed at deployment, not in a policy);
- `data/derived/sourceset_v3.json`, `data/derived/model/bond_sizing_v3.json`: the pilot's portfolio set and bond [SIM];
- `docs/clause/clause_sources.json`: the precedents, EU dates and data terms, each with its source URL and read date.

Outputs: the `<!-- gen:NAME:begin/end -->` blocks of `docs/clause/model-clause.md` (`--write` renders them, `--check`
exits 1 if any is stale; pipeline/tests/test_clause.py runs the check) and `pdf_blocks()` for the submission PDF's
adoption section (scripts/build_app_data.py renders those with `make readme`).

Fail closed: a policy whose installment shares do not sum to 100 %, whose thresholds do not rise, whose settlement modes
differ in anything but the mode, or whose hold window is shorter than the relapse grace exits 1; nothing is written."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from final_reach import final_reach

ROOT = Path(__file__).resolve().parent.parent
DERIVED = ROOT / "data" / "derived"
POLICY_FILE = DERIVED / "policy_v2.json"
DEPLOYMENT = ROOT / "contracts" / "deployments" / "11155111.json"
CLAUSE_DIR = ROOT / "docs" / "clause"
CLAUSE = CLAUSE_DIR / "model-clause.md"
SOURCES = CLAUSE_DIR / "clause_sources.json"

ONCHAIN, VERDICT = "POLICY_V2", "POLICY_V2_VERDICT_ONLY"
MULTIYEAR = "POLICY_V2_MULTIYEAR"
MULTIYEAR_RUN = DERIVED / "multiyear_run.json"
DAY = 86_400
TIER_NAMES = ("Tier 1", "Tier 2", "Tier 3")
INSTRUMENTS = {"tan": "Tanager", "emi": "EMIT"}


def _multiyear_note() -> str:
    """The three-year policy as the drafting note's worked example; the Sepolia covenant only once it is recorded."""
    pols = json.loads(POLICY_FILE.read_text())["policies"]
    my = pols[MULTIYEAR]["policy"]
    r = final_reach(my)
    note = (f" (`{MULTIYEAR}`: Term {_days(my['core']['term'])}, every episode in its first "
            f"{r['lastDetectionDay']} days")
    if MULTIYEAR_RUN.exists():
        run = json.loads(MULTIYEAR_RUN.read_text())
        note += f"; on Ethereum Sepolia as covenant {int(run['covenantId'], 16)}, its pool opened under this policy"
    return note + ")"

# The pilot's observer organisations (docs/PRODUCTION.md item 8): a contest is confirmed by k + 1 signatures and the
# clause keeps one spare organisation for it, so the pilot names n = k + CONTEST_EXTRA + SPARE_ORGS. POLICY_V2's
# deployed core.n stays the demo setting; the pilot's n is rendered from core.k.
CONTEST_EXTRA, SPARE_ORGS = 1, 1
NUMBER_WORDS = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine")
PILOT_ORGS = ((2, "two licensed satellite-data providers (each holds its own commercial data agreement)"),
              (1, "a university remote-sensing lab"), (1, "an NGO methane analyst"))


def pilot_n(k: int) -> int:
    n = k + CONTEST_EXTRA + SPARE_ORGS
    if sum(c for c, _ in PILOT_ORGS) != n:
        fail(f"the pilot lists {sum(c for c, _ in PILOT_ORGS)} observer organisations, the quorum rule needs {n}")
    return n


def fail(msg: str) -> None:
    sys.exit(f"clause: {msg}")


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------------- formatting helpers
def _days(seconds: int) -> str:
    if seconds % DAY:
        fail(f"{seconds} s is not a whole number of days")
    d = seconds // DAY
    return f"{d:,} day" + ("" if d == 1 else "s")


def _eth(wei) -> str:
    """Wei -> plain decimal ETH ("0.00001", never "1e-05")."""
    v = (Decimal(int(wei)) / Decimal(10**18)).normalize()
    return f"{v:f} ETH"


def _bp(bp: int) -> str:
    return f"{bp / 100:g} %"


def _units(u: int) -> str:
    return f"{u // 100}.{u % 100:02d} policy units"


def _kgh(x: int) -> str:
    return f"{x:,} kg/h"


def _day(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


# ------------------------------------------------------------------------------------------------- policy checks
def policies(doc: dict | None = None) -> tuple[dict, dict, dict]:
    """(POLICY_V2 entry, POLICY_V2_VERDICT_ONLY entry, POLICY_V2 policy body), checked."""
    doc = doc if doc is not None else _load(POLICY_FILE)
    try:
        on, vo = doc["policies"][ONCHAIN], doc["policies"][VERDICT]
    except KeyError as e:
        fail(f"data/derived/policy_v2.json has no {e.args[0]}")
    if on["settlementMode"] != "ONCHAIN" or vo["settlementMode"] != "VERDICT_ONLY":
        fail("settlement modes of POLICY_V2 / POLICY_V2_VERDICT_ONLY are not ONCHAIN / VERDICT_ONLY")
    a, b = json.loads(json.dumps(on["policy"])), json.loads(json.dumps(vo["policy"]))
    a["pool"].pop("settlementMode"), b["pool"].pop("settlementMode")
    if a != b or on["onchainParamsHash"] != vo["onchainParamsHash"]:
        fail("POLICY_V2 and POLICY_V2_VERDICT_ONLY differ in more than settlementMode; the clause states one schedule")
    p = on["policy"]
    core, pool = p["core"], p["pool"]
    inst = pool["installments"]
    if sum(i[1] for i in inst) != 10_000:
        fail("installment shares do not sum to 100 %")
    if [i[0] for i in inst] != sorted({i[0] for i in inst}) or not inst[-1][3]:
        fail("installment thresholds must rise strictly and the last installment must be the final one")
    if pool["holdWindow"] < pool["publicationLag"] + core["challengeWindow"] + core["contestWindow"]:
        fail("holdWindow is shorter than publicationLag + challengeWindow + contestWindow")
    mins = core["rebateSchedule"]["tierMinKgH"]
    if mins != sorted(mins, reverse=True) or mins[-1] != core["minRateKgH"]:
        fail("tier floors must fall and the lowest must equal core.minRateKgH")
    if not 0 < core["k"] <= core["n"]:
        fail("core.k must be between 1 and core.n")
    return on, vo, p


# ------------------------------------------------------------------------------------------------ clause blocks
def clause_blocks(doc: dict | None = None, dep: dict | None = None) -> dict[str, str]:
    on, vo, p = policies(doc)
    dep = dep if dep is not None else _load(DEPLOYMENT)
    core, pool = p["core"], p["pool"]
    rs = core["rebateSchedule"]
    mins, amounts, units = rs["tierMinKgH"], rs["tierAmountsWei"], pool["unitTierAmounts"]
    grace = pool["publicationLag"] + core["challengeWindow"] + core["contestWindow"]
    reach = final_reach(p)
    floors = ", ".join(f"{INSTRUMENTS.get(k, k)} {_kgh(v)}" for k, v in pool["instrumentFloorKgH"].items())
    b: dict[str, str] = {}

    b["policy"] = (
        f"Generated by [`pipeline/clause.py`](../../pipeline/clause.py) from "
        f"[`data/derived/policy_v2.json`](../../data/derived/policy_v2.json): `{ONCHAIN}` (settlement on chain, "
        f"policyHash `{on['policyHash']}`) and `{VERDICT}` (settlement by credit note, policyHash `{vo['policyHash']}`). "
        f"Both carry onchainParamsHash `{on['onchainParamsHash']}`: the parameters are identical and only "
        "`pool.settlementMode` differs. A key in backticks, such as `core.k`, names the policy field that holds the value.")

    tier_rows = []
    for i, name in enumerate(TIER_NAMES):
        lo = _kgh(mins[i])
        rng = f"{lo} or more" if i == 0 else f"{lo} to below {_kgh(mins[i - 1])}"
        tier_rows.append(f"| {name} | {rng} | {_eth(amounts[i])} [SIM] | {_units(units[i])} |")
    b["tiers"] = "\n".join([
        "| Tier | Attested rate (`core.rebateSchedule.tierMinKgH`) | Rebate, settlement on chain "
        "(`core.rebateSchedule.tierAmountsWei`; testnet ETH in the prototype) | Rebate, credit note "
        "(`pool.unitTierAmounts`) |",
        "|---|---|---|---|", *tier_rows])

    b["trigger"] = (
        f"In the prototype policy: Quorum **k = {core['k']} of n = {core['n']}** Observer Organisations (`core.k`, "
        f"`core.n`); rate floor **{_kgh(core['minRateKgH'])}** (`core.minRateKgH`), the super-emitter threshold of "
        f"Art. 2(62) of Regulation (EU) 2024/1787 (`pool.euSuperEmitterKgH` = {_kgh(pool['euSuperEmitterKgH'])}); "
        f"Challenge Window **{_days(core['challengeWindow'])}** (`core.challengeWindow`); link window "
        f"**{_days(core['linkWindow'])}** (`core.linkWindow`). The proposer's deposit is "
        f"{_eth(dep['proposerBond'])} [SIM] (`proposerBond`, fixed when the covenant contract is deployed; "
        "`contracts/deployments/11155111.json`).")

    b["challenge"] = (
        f"In the prototype policy: a challenge deposit of {_eth(dep['challengerBond'])} [SIM] (`challengerBond`, fixed "
        f"at deployment); a resolution needs {core['k']} Observer Organisations, and {core['k'] + 1} to confirm over a "
        f"contest; the contest window is **{_days(core['contestWindow'])}** (`core.contestWindow`).")

    inst_rows = []
    for n, share, two, final in pool["installments"]:
        gates = ["passes from two instruments", "a MARS corroboration covering its triggering pass",
                 f"the Cure Term of {_days(pool['cureTerm'])} (`pool.cureTerm`) plus the relapse grace of "
                 f"{_days(grace)} (`pool.publicationLag` + `core.challengeWindow` + `core.contestWindow`) since the "
                 "claim's start"] if final else []
        if two and not final:
            gates = ["passes from two instruments"]
        inst_rows.append(f"| {n} | {_bp(share)} of the cap | {'; '.join(gates) if gates else '—'} |")
    b["refund"] = "\n".join([
        f"In the prototype policy: a pass counts after a clean window of **{_days(pool['cleanWindow'])}** "
        f"(`pool.cleanWindow`) and no earlier than **{_days(pool['publicationLag'])}** after the scene "
        f"(`pool.publicationLag`); a challenged pass is answered within {_days(pool['resolveWindow'])} "
        f"(`pool.resolveWindow`); a scheduled installment is released no earlier than "
        f"**{_days(pool['holdWindow'])}** after scheduling (`pool.holdWindow`). Pass deposit "
        f"{_eth(pool['passBondWei'])}, challenge deposit {_eth(pool['challengerBondWei'])}, observer fee "
        f"{_eth(pool['observerFeeWei'])} per counted pass, shared by the signing organisations (all [SIM], "
        "`pool.passBondWei`, `pool.challengerBondWei`, `pool.observerFeeWei`). Scene rules: Carbon Mapper's "
        f"`{pool['cloudRule']}` flag (`pool.cloudRule`), consistent with a cloud cover of at most "
        f"{_bp(pool['maxCloudBp'])} (`pool.maxCloudBp`); no UNEP MARS plume within ±{pool['marsWindowDays']} days "
        f"(`pool.marsWindowDays`); the "
        f"instrument's detection floor ({floors}; `pool.instrumentFloorKgH`) at most the episode's rate.",
        "",
        "| Counted passes (`pool.installments`) | Installment | Also needs |",
        "|---|---|---|", *inst_rows])

    start, term = core["start"], core["term"]
    b["term"] = (
        f"In the prototype policy: Term **{_days(term)}** (`core.term`) from {_day(start)} (`core.start`); the Sponsor "
        f"Pool stops accepting passes {_days(pool['publicationLag'] + pool['cleanWindow'])} after the Term ends "
        "(`pool.publicationLag` + `pool.cleanWindow`), because scenes are published late. The final installment "
        f"(clause 6) is scheduled only for an episode at least **{reach['minTermDays']} days** before the Term ends "
        "(`core.linkWindow` + `pool.cureTerm` + the relapse grace − `pool.publicationLag`); "
        + (f"with this {term // DAY}-day Term it is never scheduled and a refund stops at "
           f"{_bp(reach['nonFinalShareBp'])} of the cap. "
           if reach["lastDetectionDay"] is None else
           f"with this {term // DAY}-day Term that is every episode in its first {reach['lastDetectionDay']} days. ")
        + "Drafting note: set the Term to the supply contract's own (multi-year); the final share then stays "
        f"reachable for every episode up to {reach['minTermDays']} days before the Term ends"
        + _multiyear_note() + ".")

    ub = pool["upperBoundBp"]
    rows = [
        ("`core.k` / `core.n`", f"{core['k']} of {core['n']}", "Quorum", "3, 5, 6, 9"),
        ("`core.minRateKgH`", _kgh(core["minRateKgH"]), "Attested Detection floor", "3"),
        ("`core.rebateSchedule.tierMinKgH`", " / ".join(_kgh(x) for x in mins), "Tier floors", "4"),
        ("`core.rebateSchedule.tierAmountsWei`", " / ".join(_eth(x) for x in amounts) + " [SIM]",
         "Rebate, on chain", "4, 7"),
        ("`pool.unitTierAmounts`", " / ".join(_units(x) for x in units), "Rebate, credit note", "4, 7"),
        ("`core.challengeWindow`", _days(core["challengeWindow"]), "Challenge Window", "3, 5"),
        ("`core.contestWindow`", _days(core["contestWindow"]), "Contest window", "5"),
        ("`core.linkWindow`", _days(core["linkWindow"]), "One episode per window at a source", "3, 6"),
        ("`core.term`, `core.start`", f"{_days(term)} from {_day(start)}", "Term", "10"),
        ("`pool.publicationLag`", _days(pool["publicationLag"]), "Earliest attestation after a scene", "6, 10"),
        ("`pool.cleanWindow`", _days(pool["cleanWindow"]), "Challenge window of a clean pass", "6"),
        ("`pool.resolveWindow`", _days(pool["resolveWindow"]), "Observers' answer to a pass challenge", "6"),
        ("`pool.holdWindow`", _days(pool["holdWindow"]), "Wait before an installment is released", "6"),
        ("`pool.cureTerm`", _days(pool["cureTerm"]), "Cure Term (final installment)", "6"),
        ("`pool.installments`", " / ".join(f"{_bp(s)} after {n}" for n, s, _, _ in pool["installments"]),
         "Refund Installments", "6"),
        ("`pool.upperBoundBp`", f"u(1) {_bp(ub[0])} … u({len(ub)}) {_bp(ub[-1])}",
         "Upper bound on detection frequency after N counted passes, recorded with each pass", "6"),
        ("`pool.cloudRule`, `pool.maxCloudBp`", f"`{pool['cloudRule']}`, {_bp(pool['maxCloudBp'])}", "Scene rule", "6"),
        ("`pool.marsWindowDays`", f"±{pool['marsWindowDays']} days", "No MARS plume near the scene", "6, 8"),
        ("`pool.instrumentFloorKgH`", floors, "Instrument detection floors", "6"),
        ("`pool.euSuperEmitterKgH`", _kgh(pool["euSuperEmitterKgH"]), "Art. 2(62) threshold", "3"),
        ("`pool.passBondWei`", _eth(pool["passBondWei"]) + " [SIM]", "Clean-pass deposit", "6"),
        ("`pool.challengerBondWei`", _eth(pool["challengerBondWei"]) + " [SIM]", "Clean-pass challenge deposit", "6"),
        ("`pool.observerFeeWei`", _eth(pool["observerFeeWei"]) + " [SIM]", "Observer fee per counted pass", "6, 9"),
        ("`pool.settlementMode`", f"`{on['settlementMode']}` / `{vo['settlementMode']}`", "Settlement Mode", "7"),
    ]
    b["schedule"] = "\n".join(["| Policy key | Value | Meaning | Clause |", "|---|---|---|---|",
                               *[f"| {k} | {v} | {m} | {c} |" for k, v, m, c in rows]])
    return b


# --------------------------------------------------------------------------------------------------- PDF blocks
def sources(doc: dict | None = None) -> dict:
    s = doc if doc is not None else _load(SOURCES)
    for row in s["precedents"] + s["euDates"] + s["settlementLaw"] + s["providers"] + [s["renewalExample"]]:
        if not row.get("url", "").startswith("https://") or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", row.get("read", "")):
            fail(f"docs/clause/clause_sources.json: {row.get('id', row.get('date'))} has no https source or read date")
    return s


def pdf_blocks(doc: dict | None = None, src: dict | None = None) -> dict[str, str]:
    _, _, p = policies(doc)
    core, pool = p["core"], p["pool"]
    s = sources(src)
    rows = [f"| {r['party']}: {r['instrument']} | {r['moneyTerm']} | {r['differs']} | {r['tag']} [source]({r['url']}), "
            f"read {r['read']} |" for r in s["precedents"]]
    precedents = "\n".join([
        "| Party, instrument | Money term | How this clause differs | Source |", "|---|---|---|---|", *rows,
        "", s["noPriceTerm"]["line"]])

    ss = _load(DERIVED / "sourceset_v3.json")
    bond = _load(DERIVED / "model" / "bond_sizing_v3.json")
    if bond["sourceset_root"] != ss["root"]:
        fail("bond_sizing_v3.json was sized for a different facility set than sourceset_v3.json")
    d = {e["id"]: e for e in s["euDates"]}
    ren = s["renewalExample"]

    def cite(e: dict) -> str:
        return f"{e['tag']}, [source]({e['url']})"

    n_pilot = pilot_n(core["k"])
    n_word = NUMBER_WORDS[n_pilot]
    orgs = ", ".join(t for _, t in PILOT_ORGS[:-1]) + " and " + PILOT_ORGS[-1][1]
    pilot = "\n".join([
        f"**Pilot (proposed, not run).** One EU importer [SIM] writes the model clause into one supply contract "
        f"concluded or renewed on or after {d['art28Scope']['date']}, the contracts Art. 28 of Regulation (EU) "
        f"2024/1787 covers ({cite(d['art28Scope'])}). One portfolio covenant covers the contracted sources; the "
        f"stand-in is the {ss['n']}-facility set already run on a local chain "
        f"([`data/derived/sourceset_v3.json`](data/derived/sourceset_v3.json)), bonded at "
        f"{bond['recommended_bond_eth']:g} ETH [SIM], the P90 of one year's rebate exposure.",
        "",
        f"- **Observers.** {n_word.capitalize()} organisations with a quorum of {core['k']} of {n_pilot} (`core.k`; "
        f"n = k + 2, so a contest, which needs k + 1, keeps a spare organisation): {orgs}. None is the seller or the "
        "buyer or controlled by either; each holds its own keys.",
        "- **Sponsor.** The lender whose sustainability-linked margin already moves on methane intensity (rows above), "
        "or the buyer itself. Its pool pays the capped refund and the observer fee for each counted pass.",
        f"- **Timeline.** Before {d['art28Applies']['date']}, when Art. 28 applies ({cite(d['art28Applies'])}): sign, "
        f"register the {n_word} organisations, open the covenant and the pool, run beside the importer's systems. Year one "
        f"is the covenant term ({_days(core['term'])}, `core.term`); each record is exported as IEA Annex D and "
        f"attached as evidence to the importer's yearly Art. 27 report due {d['art27Report']['date']} "
        f"({cite(d['art27Report'])}); {d['penaltyPause']['what']} ("
        f"{cite(d['penaltyPause'])}), so the pilot runs while the obligation is new. Before "
        f"{d['art29Reporting']['date']} ({d['art29Reporting']['what'].removeprefix('Art. 29: ')}; "
        f"{cite(d['art29Reporting'])}) the importer holds a year of attested record; before "
        f"{d['art29Max']['date']} (maximum intensity values, {cite(d['art29Max'])}) it decides whether the clause "
        f"becomes standard in renewals. A renewal is the entry point: BOTAS–Bulgargaz, protocol of {ren['date']} "
        f"({cite(ren)}).",
        "- **Measured.** Episodes CONFIRMED, VOID, EXPIRED and LINKED per source; time from the provider's publication "
        "to CONFIRMED; challenges by reason code and outcome; observer disagreements that left no quorum; qualified "
        "clean passes, installments released and refunds cancelled by a relapse; ledger cost per episode against "
        "the Sepolia measurement; and the two ASSUMPTIONs of section 4, the share of attested episodes the seller "
        "acts on and the share of emissions that stops, which no public dataset measures today.",
    ])
    return {"precedents": precedents, "pilot": pilot}


# ------------------------------------------------------------------------------------------------ write / check
GEN = r"<!-- gen:{name}:begin -->.*?<!-- gen:{name}:end -->"


def apply(text: str, blocks: dict[str, str]) -> str:
    for name, body in blocks.items():
        pat = re.compile(GEN.format(name=name), re.S)
        if not pat.search(text):
            fail(f"docs/clause/model-clause.md has no gen:{name} block")
        text = pat.sub(lambda _: f"<!-- gen:{name}:begin -->\n{body}\n<!-- gen:{name}:end -->", text)
    return text


def main(argv: list[str]) -> None:
    if argv not in (["--write"], ["--check"]):
        sys.exit("usage: clause.py --write | --check")
    text = CLAUSE.read_text(encoding="utf-8")
    new = apply(text, clause_blocks())
    if argv == ["--write"]:
        CLAUSE.write_text(new, encoding="utf-8")
        print(f"docs/clause/model-clause.md blocks rendered ({len(new.encode()):,} bytes)")
    elif new != text:
        fail("docs/clause/model-clause.md generated blocks are stale: run make clause")
    else:
        print("docs/clause/model-clause.md generated blocks are current")


if __name__ == "__main__":
    main(sys.argv[1:])
