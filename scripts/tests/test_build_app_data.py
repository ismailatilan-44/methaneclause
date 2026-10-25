"""Page and builder offline tests (make test-scripts, part of make check through EXTRA_CHECKS).

Each fail-closed path of scripts/build_app_data.py must stop the build (SystemExit) on an inconsistent input, and the
demo's clock helper must only ever move a chain forward and record what it skipped. Inputs are the committed files,
copied and edited in memory; nothing is written.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("build_app_data", ROOT / "scripts" / "build_app_data.py")
bad = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bad)  # also puts pipeline/ on sys.path

from observer import demo  # noqa: E402

DERIVED = ROOT / "data" / "derived"
DZA = json.loads((DERIVED / "cases" / "DZA_S_012.clean_passes.json").read_text())
DZA_CASE = json.loads((DERIVED / "cases" / "DZA_S_012.json").read_text())
CLEANRUNS = json.loads((DERIVED / "cleanruns.json").read_text())
REAL = {"path": "data/derived/cases/DZA_S_012.clean_passes.json", "mock": False}
POOL = bad.policy_mod.POLICY_V2["pool"]


def view(doc: dict, cleanruns: dict = CLEANRUNS) -> dict:
    return bad.clean_view(doc, REAL, bad.relapse_of(DZA_CASE, doc), cleanruns, {"mock": False}, POOL)


# ------------------------------------------------------------------------------------------- the committed files pass
def test_committed_dza_builds_and_matches_its_summary():
    v = view(DZA)
    assert v["n"] == DZA["summary"]["qualifiedBeforeFirstPlume"] > 0
    assert v["upperBoundBp"] == DZA["summary"]["upperBoundBpAtThatCount"] == bad.u_bp(v["n"])
    assert v["floorKgH"] == DZA["summary"]["maxCountedFloorKgH"]
    assert v["atCloudBin"]["n"] == DZA["summary"]["countedAtCloudBp2500"]


# ------------------------------------------------------------------------------------------- fail-closed exits
@pytest.mark.parametrize("key", bad.SUMMARY_KEYS)
def test_missing_summary_key_fails(key):
    doc = copy.deepcopy(DZA)
    del doc["summary"][key]
    with pytest.raises(SystemExit, match=key):
        view(doc)


def test_count_disagreeing_with_summary_fails():
    doc = copy.deepcopy(DZA)
    doc["summary"]["qualifiedBeforeFirstPlume"] += 1
    with pytest.raises(SystemExit, match="counted scenes"):
        view(doc)


def test_upper_bound_not_for_the_count_shown_fails():
    doc = copy.deepcopy(DZA)
    n = doc["summary"]["qualifiedBeforeFirstPlume"]
    doc["summary"]["upperBoundBpAtThatCount"] = bad.u_bp(n + 2)  # the bound of another count
    with pytest.raises(SystemExit, match="upperBoundBpAtThatCount"):
        view(doc)


def test_edited_bound_table_fails():
    cr = copy.deepcopy(CLEANRUNS)
    cr["upper_bound_bp"][4] += 1
    with pytest.raises(SystemExit, match="upper_bound_bp"):
        view(DZA, cr)


def test_floor_disagreeing_with_floors_json_fails():
    doc = copy.deepcopy(DZA)
    inst = next(i for i in doc["rules"]["instrumentFloorKgH"])
    doc["rules"]["instrumentFloorKgH"][inst] += 1  # card floor no longer the cited one
    with pytest.raises(SystemExit, match="floor"):
        view(doc)


def test_floor_changed_only_in_policy_v2_fails():
    pool = copy.deepcopy(POOL)
    inst = next(iter(pool["instrumentFloorKgH"]))
    pool["instrumentFloorKgH"][inst] += 1  # floors.json and the case file still agree with each other
    with pytest.raises(SystemExit, match="POLICY_V2"):
        bad.clean_view(DZA, REAL, bad.relapse_of(DZA_CASE, DZA), CLEANRUNS, {"mock": False}, pool)


def test_missing_policy_v2_floor_table_fails():
    pool = {k: v for k, v in POOL.items() if k != "instrumentFloorKgH"}
    with pytest.raises(SystemExit, match="no instrumentFloorKgH"):
        bad.clean_view(DZA, REAL, bad.relapse_of(DZA_CASE, DZA), CLEANRUNS, {"mock": False}, pool)


def test_cloud_bin_count_disagreeing_with_summary_fails():
    doc = copy.deepcopy(DZA)
    doc["summary"]["countedAtCloudBp2500"] += 1
    with pytest.raises(SystemExit, match="cloud bin"):
        view(doc)


def test_relapse_scene_missing_from_case_file_fails():
    doc = copy.deepcopy(DZA)
    doc["summary"]["firstPlumeAfterSince"]["sceneName"] = "tan20990101t000000c00s0000"
    with pytest.raises(SystemExit, match="relapse scene"):
        bad.relapse_of(DZA_CASE, doc)


def test_mock_file_that_does_not_say_mock_is_refused(tmp_path, monkeypatch):
    (tmp_path / "x.json").write_text(json.dumps({"schema": "methaneclause.cleanruns/1", "note": "placeholder"}))
    monkeypatch.setattr(bad, "MOCK_DIR", tmp_path)
    with pytest.raises(SystemExit, match="does not say MOCK"):
        bad.v2_file(tmp_path / "absent.json", "x.json")


def test_cleanpass_signer_must_be_the_registered_observer():
    expected = {"OBS_A": "0x" + "11" * 20, "OBS_B": "0x" + "22" * 20}
    ok = [{"org": "OBS_A", "signer": expected["OBS_A"]}, {"org": "OBS_B", "signer": expected["OBS_B"]}]
    bad.check_signers(ok, expected)
    swapped = [{"org": "OBS_A", "signer": expected["OBS_B"]}, {"org": "OBS_B", "signer": expected["OBS_A"]}]
    with pytest.raises(SystemExit, match="recovers to"):
        bad.check_signers(swapped, expected)
    with pytest.raises(SystemExit, match="expected None"):
        bad.check_signers([{"org": "OBS_C", "signer": expected["OBS_A"]}], expected)


def test_cleanpass_demo_signers_come_from_the_demo_run():
    """End to end on the builder: the CleanPass it signs recovers to the demo's registered OBS_A / OBS_B addresses,
    and a wrong expectation stops it."""
    app = json.loads((ROOT / "app" / "data" / "app.json").read_text())
    reg = app["hybrid"]["registeredKeys"]
    cov = app["hybrid"]["entries"][0]["bundle"]["covenantId"]
    demo_out = bad.clean_pass_demo(reg, cov)
    assert {e["org"]: e["signer"] for e in demo_out["entries"]} == {k: reg[k]["address"] for k in bad.CLEAN_SIGNERS}
    with pytest.raises(SystemExit, match="recovers to"):
        bad.clean_pass_demo({"OBS_A": {**reg["OBS_A"], "address": reg["OBS_B"]["address"]},
                             "OBS_B": {**reg["OBS_B"], "address": reg["OBS_A"]["address"]}}, cov)
    wrong_pq = {o: {**k, "pqPubKeyHash": "0x" + "00" * 32} for o, k in reg.items()}
    with pytest.raises(SystemExit, match="pqPubKeyHash"):
        bad.clean_pass_demo(wrong_pq, cov)


def test_catalogue_sentence_is_lane_bs_verbatim_or_absent(monkeypatch):
    model = json.loads((DERIVED / "audit_numbers.json").read_text())["numbers"]["model"]
    said = "A quiet run is weak evidence: (the pipeline's words, whatever they are)."
    monkeypatch.setattr(bad, "v2_file", lambda *_: ({"card": {"sentence": said}}, {"mock": False, "path": "x"}))
    assert bad.cap_reasons(model)["catalogue"]["sentence"] == said
    monkeypatch.setattr(bad, "v2_file", lambda *_: ({"card": {"next_pass": {"rate": 0.22}}}, {"mock": False, "path": "x"}))
    assert bad.cap_reasons(model)["catalogue"] is None  # numbers alone are never turned into a typed sentence


@pytest.mark.parametrize("name", ["TKM_S_276.annex_d.json", "TKM_S_276.annex_d.csv", "TKM_S_276.annex_c.example.json",
                                  "TKM_S_276.annex_c.example.csv"])
def test_app_export_copies_are_lane_bs_current_files(name):
    """app/data carries byte-identical copies of the pipeline's exports; a stale copy fails here (run make app)."""
    assert (ROOT / "app" / "data" / name).read_bytes() == (DERIVED / "export" / name).read_bytes(), \
        f"app/data/{name} differs from data/derived/export/{name}: run make app"


def test_portfolio_view_refuses_another_set(tmp_path, monkeypatch):
    ss = json.loads((DERIVED / "sourceset_v3.json").read_text())
    run = {"root": ss["root"], "n": ss["n"], "covenantId": "0x03", "bondWei": "1", "poolWei": "1", "episodeId": 4,
           "claimId": "0x00", "proofSteps": 8, "checks": 7, "txs": 6, "status0": 1,
           "txlog": [{"label": "x", "tx": "0x01", "status": 0, "revert": "NotCovered"}]}
    demo_dir = tmp_path / "out" / "demo"
    demo_dir.mkdir(parents=True)
    (demo_dir / "portfolio.json").write_text(json.dumps(run))
    monkeypatch.setattr(bad, "ROOT", tmp_path)
    assert bad.portfolio_view()["rejected"][0]["revert"] == "NotCovered"
    (demo_dir / "portfolio.json").write_text(json.dumps({**run, "root": "0x" + "00" * 32}))
    with pytest.raises(SystemExit, match="sourceset_v3"):
        bad.portfolio_view()


def test_credit_note_must_cite_the_finalize_receipt(tmp_path, monkeypatch):
    """exports_view refuses a credit note whose verdict does not cite the finalize tx and its block time."""
    demo_dir = tmp_path / "out" / "demo"
    demo_dir.mkdir(parents=True)
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    verdict = {"covenantId": "0x02", "episodeId": 3, "claimId": "0x00", "finalizeTx": "0xf1", "finalizeTime": 100,
               "units": 300, "noteRef": "0x", "noteHash": "0x", "annexC": {"claimHash": "x", "tx": "0x"}, "checks": 1,
               "txs": 1, "status0": 0, "pool": {}}
    (demo_dir / "verdict.json").write_text(json.dumps(verdict))
    for ext in ("json", "csv"):
        (demo_dir / f"TKM_S_276.credit_note.{ext}").write_text("{}")
    monkeypatch.setattr(bad, "ROOT", tmp_path)
    monkeypatch.setattr(bad, "APP", app_dir)
    for tx, t in (("0xopenClaim", 100), ("0xf1", 101)):  # wrong tx; right tx with another block's time
        note = {"notes": [{"verdict": {"tx": tx, "blockTime": t}}]}
        (demo_dir / "TKM_S_276.credit_note.json").write_text(json.dumps(note))
        with pytest.raises(SystemExit, match="finalize"):
            bad.exports_view()


def test_cure_guard_refuses_a_live_episode_at_the_facility():
    """judge cure refuses to propose while the facility's latest episode on the covenant is live; settled is fine."""
    from observer import judge

    class FakeCh:
        def __init__(self, state):
            self.state = state
            self.cov = type("Cov", (), {"functions": type("F", (), {"latestEpisode": staticmethod(lambda c, s: None)})})

        def read(self, _):
            return 2

        def episode(self, _):
            return {"stateName": self.state}

    run = object.__new__(judge.Run)
    for live in ("PROPOSED", "CHALLENGED", "CONTESTED"):
        run.ch = FakeCh(live)
        with pytest.raises(SystemExit):
            run.require_no_live_episode("0x" + "00" * 31 + "01", "DZA_S_012")
    for settled in ("VOID", "CONFIRMED", "EXPIRED"):
        run.ch = FakeCh(settled)
        run.require_no_live_episode("0x" + "00" * 31 + "01", "DZA_S_012")


def test_readme_generated_blocks_are_current_and_fit(tmp_path, monkeypatch):
    """README.md and docs/devpost.md carry no stale generated number; README stays within its budget; a hand edit inside a
    generated block is caught."""
    bad.readme(write=False)
    assert len((ROOT / "README.md").read_bytes()) <= bad.README_BUDGET
    text = (ROOT / "README.md").read_text()
    a = text.index("<!-- gen:problem:begin -->")
    edited = tmp_path / "README.md"
    edited.write_text(text[:a] + text[a:].replace(" of ", " of about ", 1))
    monkeypatch.setattr(bad, "README", edited)
    with pytest.raises(SystemExit, match="stale"):
        bad.readme(write=False)


LEDGER = {"openedByBuyerWei": "3000", "sponsorTopUpWei": "90", "buyerContributionWei": "3000",
          "sponsorContributionWei": "90", "balanceWei": "1200", "reservedWei": "0", "releasedWei": "1800",
          "forfeitedWei": "1200", "observerFeesWei": "90", "sponsorWithdrawn": False}


def test_replay_view_refuses_an_incomplete_replay(tmp_path):
    good = {"chainId": 31337, "clock": {}, "pool": {}, "suretyBond": "pending", "skippedSeconds": {}, "txs": 2,
            "checks": 2, "status0": 1, "txlog": [],
            "variants": {"A": {"label": "real", "cid": "0x01", "cap": 3000, "outcome": {"paidBp": 6000},
                               "sponsorLedger": LEDGER}}}
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(good))
    assert bad.replay_view(path)["variants"]["A"]["outcome"]["paidBp"] == 6000
    for broken in ({**good, "status0": 0},
                   {**good, "variants": {"A": {"label": "real", "cid": "0x01", "sponsorLedger": LEDGER}}}):
        path.write_text(json.dumps(broken))
        with pytest.raises(SystemExit, match="incomplete"):
            bad.replay_view(path)


def test_sponsor_view_comes_from_the_replay_and_fails_closed(tmp_path):
    """The sponsor persona's numbers are the pool state demo.py read back; the remainder and the sponsor's share follow
    sponsorWithdraw's formula; a missing field or an unbalanced ledger stops the build."""
    real = json.loads((ROOT / "app" / "data" / "app.json").read_text())["replay"]
    for key in ("A", "B"):
        s = real["variants"][key]["sponsor"]
        assert int(s["balanceWei"]) == (int(s["openedByBuyerWei"]) + int(s["sponsorTopUpWei"]) - int(s["releasedWei"])
                                        - int(s["observerFeesWei"]))
        assert int(s["sponsorTopUpWei"]) > 0 and int(s["releasedWei"]) > 0 and s["sponsorWithdrawn"] is False
    base = {"chainId": 31337, "clock": {}, "pool": {}, "skippedSeconds": {}, "txs": 2, "checks": 2, "status0": 1,
            "txlog": [], "variants": {}}
    path = tmp_path / "replay.json"
    ok = {**base, "variants": {"A": {"label": "real", "cid": "0x01", "cap": 3000, "outcome": {"paidBp": 6000},
                                     "sponsorLedger": LEDGER}}}
    path.write_text(json.dumps(ok))
    s = bad.replay_view(path)["variants"]["A"]["sponsor"]
    assert s["unreservedWei"] == "1200" and s["sponsorShareWei"] == str(1200 * 90 // 3090)
    # forfeited = the claim's cap minus what it paid (both from the replay); the outstanding contributions are the two
    # funders' only while nobody has called sponsorWithdraw (the pool keeps no public outstanding getter)
    for led in ({k: v for k, v in LEDGER.items() if k != "observerFeesWei"}, {**LEDGER, "balanceWei": "1201"},
                {**LEDGER, "sponsorTopUpWei": "x"}, {**LEDGER, "sponsorContributionWei": "0"}, None,
                {**LEDGER, "forfeitedWei": "1201"}, {**LEDGER, "forfeitedWei": "0"},
                {**LEDGER, "sponsorWithdrawn": True}):
        v = {"label": "real", "cid": "0x01", "cap": 3000, "outcome": {"paidBp": 6000}}
        if led is not None:
            v["sponsorLedger"] = led
        path.write_text(json.dumps({**base, "variants": {"A": v}}))
        with pytest.raises(SystemExit, match="sponsorLedger|balance|forfeited|sponsorWithdraw"):
            bad.replay_view(path)
    for v in ({"label": "real", "cid": "0x01", "outcome": {"paidBp": 6000}, "sponsorLedger": LEDGER},  # no cap
              {"label": "real", "cid": "0x01", "cap": 3100, "outcome": {"paidBp": 6000}, "sponsorLedger": LEDGER}):
        path.write_text(json.dumps({**base, "variants": {"A": v}}))
        with pytest.raises(SystemExit, match="forfeited"):
            bad.replay_view(path)


# the Devpost draft stays in the private repository (scripts/public_manifest.txt): skipped in the public export
NEEDS_DEVPOST = pytest.mark.skipif(not (ROOT / "docs" / "devpost.md").exists(),
                                   reason="docs/devpost.md is not in this checkout")


@NEEDS_DEVPOST
def test_devpost_carries_the_sepolia_core_and_the_chain_test_from_generated_blocks():
    """The Devpost draft's addresses, four-question table and stake low bound are generated, never typed; the
    README shares the table; the stake sentence no longer cites a figure the README does not show."""
    dep = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
    dp = (ROOT / "docs" / "devpost.md").read_text()
    readme = (ROOT / "README.md").read_text()
    for name in ("deploy", "chain", "stake"):
        assert f"<!-- gen:{name}:begin -->" in dp
    assert "<!-- gen:chain:begin -->" in readme and "<!-- gen:deploy:begin -->" not in readme
    block = dp[dp.index("<!-- gen:deploy:begin -->"):dp.index("<!-- gen:deploy:end -->")]
    assert dep["methaneCovenant"] in block and dep["keyRegistry"] in block
    assert ("admin renounced" in block) == (int(dep["registryAdmin"], 16) == 0)
    card = json.loads((DERIVED / "backtest.json").read_text())
    assert "unquoted" not in card["card"]["stakeSentence"]
    assert card["stake_band"]["dutyCycleConcentration"]["facilitiesWithDutyOne"] > 0  # the fact stays, off the card
    assert "P3271" not in readme and "P3271" not in dp


def test_deploy_block_refuses_a_record_for_another_chain():
    dep = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
    with pytest.raises(SystemExit):
        bad.deploy_block({**dep, "chainId": 84532})


@NEEDS_DEVPOST
def test_devpost_surfaces_the_pool_the_closed_loop_and_the_projection_headline():
    """The Devpost draft names the Sepolia pool and its libraries, the CONFIRMED + withdraw transactions of the
    operator's run and the projection headline, each from a committed file through a generated block."""
    dep = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
    run = json.loads((DERIVED / "testnet_run.json").read_text())
    head = json.loads((DERIVED / "projection.json").read_text())["forward"]["card"]["headlineSentence"]
    dp = (ROOT / "docs" / "devpost.md").read_text()

    def block(name: str) -> str:
        return dp[dp.index(f"<!-- gen:{name}:begin -->"):dp.index(f"<!-- gen:{name}:end -->")]

    deploy = block("deploy")
    assert dep["incentivePool"] in deploy and f"{len(dep['incentivePoolLibraries'])} linked libraries" in deploy
    assert "check_pool_record.py" in deploy
    loop = block("loop")
    steps = {s["step"]: s for s in run["steps"]}
    for step in ("finalize", "withdraw_buyer"):
        assert steps[step]["tx"] in loop, step
    assert "CONFIRMED" in loop and f"{int(steps['withdraw_buyer']['amountWei']) / 1e18:g} ETH" in loop
    assert head in block("projectionHeadline")
    readme = (ROOT / "README.md").read_text()
    assert "<!-- gen:loop:begin -->" not in readme and "<!-- gen:projectionHeadline:begin -->" not in readme
    # the README's first screen folds the closed loop (success first) into its status-0 line
    s0 = readme[readme.index("<!-- gen:status0:begin -->"):readme.index("<!-- gen:status0:end -->")]
    assert s0.index(steps["withdraw_buyer"]["tx"]) < s0.index("receipt status 0") < readme.index("## ")


def test_footprint_figures_are_generated_and_current(tmp_path, monkeypatch):
    """docs/FOOTPRINT.md types none of our figures: the transaction counts, the gas table, the energy illustration and
    the pool line are generated from measurements.json; a stale or hand-edited figure fails `make check`."""
    fp = (ROOT / "docs" / "FOOTPRINT.md").read_text()
    m = json.loads((DERIVED / "measurements.json").read_text())
    blocks = bad.footprint_blocks()
    for name, body in blocks.items():
        assert f"<!-- gen:{name}:begin -->\n{body}\n<!-- gen:{name}:end -->" in fp, f"{name} is stale: run make readme"
    assert f"{m['transactions']['logged']} logged" in blocks["fpTransactions"]
    for f, v in m["functions"].items():
        assert f"| {v['gas_sepolia_median']:,} |" in blocks["fpGas"] and f"| {v['n']} |" in blocks["fpGas"], f
    assert len(blocks["fpGas"].splitlines()) == 2 + len(m["functions"]), "one gas row per measured function"
    assert f'"Wh/tx per network {bad.CCRI_WH_PER_TX}"' in fp, "the CCRI quote and the generator's value differ"
    assert f"{m['transactions']['logged']} × {bad.CCRI_WH_PER_TX} Wh" in blocks["fpEnergy"]
    outside = re.sub(r"<!-- gen:(\w+):begin -->.*?<!-- gen:\1:end -->", "", fp, flags=re.S)
    assert "× 6" not in outside and " logged" not in outside, "a figure was typed outside the generated blocks"
    edited = tmp_path / "FOOTPRINT.md"
    edited.write_text(fp.replace(f"{m['transactions']['logged']} logged", "50 logged", 1))
    monkeypatch.setattr(bad, "FOOTPRINT", edited)
    with pytest.raises(SystemExit, match="FOOTPRINT.md generated blocks are stale"):
        bad.readme(write=False)


def test_loop_block_fails_closed_without_a_confirmed_withdrawal():
    run = json.loads((DERIVED / "testnet_run.json").read_text())
    no_withdraw = {**run, "steps": [s for s in run["steps"] if s["step"] != "withdraw_buyer"]}
    reverted = {**run, "steps": [{**s, "status": 0} if s["step"] == "finalize" else s for s in run["steps"]]}
    for broken in (no_withdraw, reverted):
        with pytest.raises(SystemExit):
            bad.loop_block(broken)


def test_deploy_block_without_a_pool_names_only_the_core():
    dep = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
    core = {k: v for k, v in dep.items() if not k.startswith("incentivePool")}
    text = bad.deploy_block(core)
    assert "IncentivePool" not in text and dep["methaneCovenant"] in text


def test_numbers_block_names_the_selection_once():
    """The selection parenthetical stays on the first Numbers bullet only; the later backtest sentences drop it."""
    from backtest import SELECTION

    nums = bad.readme_blocks(bad.load(bad.APP / "app.json"))["numbers"]
    assert nums.count(SELECTION) == 1
    with pytest.raises(SystemExit):
        bad._without_selection("a sentence that never names it")


# ------------------------------------------------------------------------------------- assurance line (IEEE A2)
DEP = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
SECURITY_MD = (ROOT / "docs" / "SECURITY.md").read_text()
SLITHER_MD = (ROOT / "contracts" / "slither.md").read_text()
HALMOS_MD = (ROOT / "contracts" / "halmos" / "REPORT.md").read_text()


def _block(text: str, name: str) -> str:
    return text[text.index(f"<!-- gen:{name}:begin -->"):text.index(f"<!-- gen:{name}:end -->")]


def test_assurance_line_reads_the_deployment_the_slither_report_and_the_halmos_report():
    """README's assurance line types nothing: the Etherscan #code links come from the deployment record, the
    verified claim from docs/SECURITY.md's deployment table, the Slither counts from contracts/slither.md's impact
    table and the Halmos count from contracts/halmos/REPORT.md, each recounted here independently."""
    line = bad.assurance_block(DEP, SECURITY_MD, SLITHER_MD, HALMOS_MD)
    assert line in _block((ROOT / "README.md").read_text(), "assurance"), "stale: run make readme"
    for key in ("methaneCovenant", "incentivePool"):
        assert f"https://sepolia.etherscan.io/address/{DEP[key]}#code" in line, key
    addrs = {DEP[k] for k in ("methaneCovenant", "keyRegistry", "incentivePool")}
    addrs |= {DEP["sandbox"][k] for k in ("methaneCovenant", "keyRegistry", "incentivePool")}
    addrs |= set(DEP["incentivePoolLibraries"].values()) | set(DEP["sandbox"]["incentivePoolLibraries"].values())
    assert f"The {len(addrs)} live Sepolia contracts are source-verified on Etherscan" in line
    # an independent read of slither.md: cells split by "|", the verdict line split by words (not the renderer's regex)
    cells = {c[0].strip("* "): c[1].strip("* ") for c in (ln.strip("|").split("|") for ln in SLITHER_MD.splitlines()
                                                           if ln.startswith("| ")) if len(c) == 2}
    words = next(ln for ln in SLITHER_MD.splitlines() if ln.startswith("Verdicts:")).rstrip(".").split()
    verdict = {words[i + 1].rstrip(","): int(words[i]) for i in range(1, len(words), 2)}
    assert sum(verdict.values()) == int(cells["Total"])
    assert (f"all {cells['Total']} findings triaged ({verdict['false-positive']} false positive, "
            f"{verdict['accepted']} accepted), no High or Medium open") in line
    assert int(cells["High"]) == 0 and "none left open" in SLITHER_MD
    passed = len(re.findall(r"^\| [\w-]+ \| `(?:check|invariant)_(?!canary)\w+` \| PASS \| PASS \|", HALMOS_MD, re.M))
    assert passed and f"{passed} bounded symbolic checks pass" in line
    assert "sandboxPrevious" in DEP and DEP["sandboxPrevious"]["methaneCovenant"] not in line
    assert "contracts/slither.md" in line and "contracts/halmos/REPORT.md" in line


@pytest.mark.parametrize("what", ["security", "slither", "verdicts", "halmos", "chain"])
def test_assurance_line_fails_closed(what):
    """No verified claim without the security table saying so; no Slither line with an open High/Medium finding; no
    Halmos line unless every outcome was as expected; no record for another chain."""
    sec, sl, ha, dep = SECURITY_MD, SLITHER_MD, HALMOS_MD, DEP
    if what == "security":
        sec = sec.replace("**live, Etherscan-verified**", "**live**")
    elif what == "slither":
        sl = sl.replace("none left open", "1 left open")
    elif what == "verdicts":
        sl = sl.replace("9 false-positive", "8 false-positive")  # verdicts no longer sum to the total
    elif what == "halmos":
        ha = ha.replace("all outcomes as expected", "1 outcome not as expected")
    else:
        dep = {**dep, "chainId": 84532}
    with pytest.raises(SystemExit):
        bad.assurance_block(dep, sec, sl, ha)


# ---------------------------------------------------------------------------------------------- submission wording
HEADLINE = ("No one runs the payout: once enough independent witnesses sign the same sighting and the appeal window "
            "passes, the contract pays the buyer from the bond. The seller can appeal — then the witnesses must confirm "
            "it again — but cannot delete the sighting or hold the money.")
OLD_HEADLINE = "no one — the seller included — can delete it"
PUBLIC_REPO = "https://github.com/ismailatilan-44/methaneclause"
PUBLIC_PAGE = "https://ismailatilan-44.github.io/methaneclause/"
PROJ_SOURCES = json.loads((DERIVED / "projection_sources.json").read_text())
PROJECTION = json.loads((DERIVED / "projection.json").read_text())


def _readme_blocks() -> dict:
    return bad.readme_blocks(bad.load(bad.APP / "app.json"))


def _docs() -> dict[str, str]:
    return {p: (ROOT / p).read_text() for p in ("README.md", "docs/devpost.md", "docs/pdf/methaneclause.md")
            if (ROOT / p).exists()}


def test_why_now_names_the_stakes_own_scope_and_calls_it_the_low_bound():
    """The stake is the backtest's, over the absent sources with a quantified stake, not over the join candidates
    named in the same paragraph; its figure is the band's low bound, never "at least"."""
    sb = json.loads((DERIVED / "backtest.json").read_text())["stake_band"]
    j = json.loads((DERIVED / "audit_numbers.json").read_text())["numbers"]["join_v1"]
    w = _readme_blocks()["whyNow"]
    assert f"across {sb['facilitiesQuantified']:,} of those sources (a Carbon Mapper plume any time after" in w
    assert "low bound; stake, not abatement" in w and "at least" not in w and "Across them" not in w


def test_stake_sentences_say_low_bound_and_name_the_measurement_that_removes_the_bias():
    b = _readme_blocks()
    for name in ("stake", "numbers", "readme:numbers"):
        assert "at least" not in b[name] and "the low bound of the band" in b[name], name
        assert "un-targeted EMIT passes" in b[name], name
    assert not hasattr(bad, "_stake_low_bound"), "the backtest card says it itself (pipeline/backtest.py)"


def test_forward_cost_leads_the_whole_window_cost_and_the_forward_hazard_is_in_the_methods():
    """The ledger cost on the forward basis comes first, the whole-window cost second (PDF section 4 and
    docs/SCALING.md); the methods paragraph states the forward re-onset hazard next to the whole-window one."""
    pj = PROJECTION
    fwd_cost, whole = pj["forward"]["card"]["costSentence"], bad._whole_window(pj["card"]["costLineSentence"])
    cost = bad.projection_blocks()["projectionCost"]
    assert cost.index(fwd_cost) < cost.index(bad._whole_window(pj["card"]["costSentence"]))
    lead = bad.projection_blocks()["projection"]
    assert fwd_cost not in lead and whole in lead and lead.endswith("|")
    pays = bad.scaling_blocks(bad.load(bad.APP / "app.json"))["scPays"]
    assert pays.index(fwd_cost) < pays.index(bad._whole_window(pj["card"]["costSentence"]))
    methods = bad._methods_with_forward(pj["card"]["methodsSentence"], pj["forward"])
    assert pj["forward"]["card"]["hazardSentence"] in methods
    assert methods.index("credits none of it.") < methods.index(pj["forward"]["card"]["hazardSentence"])


def test_alerts_paragraph_quotes_the_july_2026_source_and_the_event_study_null():
    """The problem opens on the newest committed MARS figures (alerts, countries, repairs, response share), each
    taken from the source's quote, with its link, and on our event study's null; the 2025 figures are gone."""
    src = PROJ_SOURCES["sources"][bad.ALERTS_SOURCE]
    a = _readme_blocks()["alerts"]
    for q in src["quotes"]:
        for n in re.findall(r"more than ([\d,]+)|across (\d+)|only (\d+) per cent", q):
            for x in filter(None, n):
                assert x in a, x
    assert src["url"] in a and "3,500" not in a
    did = PROJECTION["evidence"]["line2"]["coverageSets"]["allNotified"]["primary"]["did"]
    for x in (did["yesMinusNo"], *did["ci"]):
        assert bad._pm3(x) in a
    for path, t in _docs().items():
        assert "<!-- gen:alerts:begin -->" in t and "3,500 satellite alerts" not in t, path


@pytest.mark.parametrize("what", ["quote", "ci"])
def test_alerts_paragraph_fails_closed(what):
    ps, pj = copy.deepcopy(PROJ_SOURCES), copy.deepcopy(PROJECTION)
    if what == "quote":
        ps["sources"][bad.ALERTS_SOURCE]["quotes"] = ["The system has issued many alerts."]
    else:
        pj["evidence"]["line2"]["coverageSets"]["allNotified"]["primary"]["did"]["ci"] = [0.01, 0.2]
    with pytest.raises(SystemExit):
        bad.alerts_block(ps, pj)


def test_headline_and_contest_sentence_are_the_shared_wording():
    """README and Devpost carry the shared headline (the PDF's static text is edited separately); nothing still says
    the seller cannot hold back a rebate "on its own" (it can appeal: a challenge resolved by k witnesses or a contest
    resolved by k+1)."""
    for path, t in _docs().items():
        assert "hold back the rebate" not in t, path
        if path != "docs/pdf/methaneclause.md":
            assert HEADLINE in t and OLD_HEADLINE not in t, path


@NEEDS_DEVPOST
def test_devpost_learned_line_is_the_readme_model_wording_with_generated_values(monkeypatch):
    """Devpost's model line gives both held-out scores, as the README does, and fails closed if the model ever
    beats the rule on both sets."""
    audit = json.loads((DERIVED / "audit_numbers.json").read_text())
    m = audit["numbers"]["model"]
    dp = (ROOT / "docs" / "devpost.md").read_text()
    learned = dp[dp.index("<!-- gen:learned:begin -->"):dp.index("<!-- gen:learned:end -->")]
    a, b = (bad._signed(m[t]["model"]["bss_vs_B1"]) for t in ("test_a", "test_b"))
    readme = (ROOT / "README.md").read_text()
    assert f"BSS {a} and {b}" in learned and f"Brier skill score {a} and {b}" in readme
    assert "the product uses the rule" in learned
    app = bad.load(bad.APP / "app.json")
    m["test_b"]["model"]["bss_vs_B1"] = 0.01
    orig = bad.load
    monkeypatch.setattr(bad, "load", lambda p: audit if Path(p).name == "audit_numbers.json" else orig(p))
    with pytest.raises(SystemExit):
        bad.readme_blocks(app)


@NEEDS_DEVPOST
def test_devpost_and_readme_link_the_public_repo_and_the_hosted_page():
    """Track 4 opens the Devpost About; the repository and the hosted page (with the 60-second tamper
    link) replace the placeholder; the next steps no longer ask for an observer organisation that is registered."""
    dp = (ROOT / "docs" / "devpost.md").read_text()
    readme = (ROOT / "README.md").read_text()
    dep = json.loads((ROOT / "contracts" / "deployments" / "11155111.json").read_text())
    about = dp.split("## Inspiration", 1)[1].strip().splitlines()[0]
    assert "Track 4" in about
    assert "<repo-url>" not in dp and PUBLIC_REPO in dp and PUBLIC_PAGE + "?tamper=A" in dp
    sixty = readme[readme.index("**60 seconds.**"):readme.index("**10 minutes")]
    assert PUBLIC_PAGE + "?tamper=A" in sixty
    assert dep["observerC"] and "A third observer organisation" not in dp


# ------------------------------------------------------------------------------ the forward hold-out leads the projection
FWD = PROJECTION["forward"]["card"]


def _block(text: str, name: str) -> str:
    return text[text.index(f"<!-- gen:{name}:begin -->"):text.index(f"<!-- gen:{name}:end -->")]


def test_projection_headline_is_the_forward_hold_out_everywhere():
    """README, Devpost and the PDF lead the carbon projection with the pre-registered forward hold-out
    (headline, stake, interval), followed by the whole-window figure as context; the whole-window card headline (whose
    credit falls two-thirds on the plumes that selected the members) is no longer quoted as ours anywhere."""
    docs = _docs()
    old = PROJECTION["card"]["headlineSentence"]
    for path, t in docs.items():
        assert old not in t, path
    lead = [FWD["headlineSentence"], FWD["stakeSentence"], FWD["intervalSentence"]]
    readme = docs["README.md"]
    nums = _block(readme, "numbers")
    first = bad._forward_lead(PROJECTION)["numbers"]
    for s in [first, FWD["headlineSentence"], FWD["stakeSentence"], bad._rolling(FWD["rollingSentence"]),
              FWD["comparisonSentence"]]:
        assert s in nums, s[:40]
    assert nums.index(first) < nums.index(FWD["stakeSentence"]) < nums.index("<details>")
    if "docs/devpost.md" in docs:
        dp = _block(docs["docs/devpost.md"], "projectionHeadline")
        for s in lead + [FWD["comparisonSentence"]]:
            assert s in dp, s[:40]
    if "docs/pdf/methaneclause.md" in docs:
        pdf = _block(docs["docs/pdf/methaneclause.md"], "projection")
        for s in lead + [FWD["rollingSentence"], FWD["comparisonSentence"]]:
            assert s in pdf, s[:40]
        assert pdf.index(FWD["headlineSentence"]) < pdf.index(FWD["comparisonSentence"]) < pdf.index("| Scope |")
        assert "is future work" not in pdf and "credits none of it" in pdf


def test_forward_limits_are_said_once_in_the_readme_limits():
    """The forward hold-out's limits line (the label window, the publication leaks, the short-window control) is in
    the PDF's Limits, split into two sentences from the same card field, never retyped; README Limits gives the label
    window in one sentence and points to the pre-registration."""
    readme = (ROOT / "README.md").read_text()
    split = bad._forward_limits(FWD["limitsSentence"])
    limits = readme[readme.index("## Limits\n"):]
    limits = limits[:limits.index("\n## ", 1)]
    assert _readme_blocks()["readme:forwardLimit"] in limits and split not in readme
    assert "decided inside the credit window" in limits and "docs/preregistration.md" in limits
    pdf = ROOT / "docs" / "pdf" / "methaneclause.md"
    if pdf.exists():
        assert split in _block(pdf.read_text(), "forwardLimit")
    assert split.replace(". Today's", "; today's", 1) == FWD["limitsSentence"]
    with pytest.raises(SystemExit):
        bad._forward_limits(FWD["limitsSentence"].replace("; today's 'absent' label", "; the label"))


def test_methods_sentence_names_the_forward_hold_out_and_fails_closed():
    m = PROJECTION["card"]["methodsSentence"]
    fwd = PROJECTION["forward"]
    out = bad._methods_with_forward(m, fwd)
    assert "is future work" not in out and fwd["preRegistration"]["D"] in out and "credits none of it" in out
    with pytest.raises(SystemExit):
        bad._methods_with_forward(m.replace("is future work", "is planned"), fwd)
    broken = copy.deepcopy(fwd)
    broken["card"]["selectingShareOfCredit"] = 0.1
    with pytest.raises(SystemExit):
        bad._methods_with_forward(m, broken)


def test_whole_window_cost_is_labelled_and_fails_closed():
    c = PROJECTION["card"]["costLineSentence"]
    assert "over each member's whole window" in bad._whole_window(c)
    with pytest.raises(SystemExit):
        bad._whole_window(c.replace("projected at the central action share", "projected"))


def test_why_now_explains_the_two_counts_and_alerts_say_no_money_moves():
    """The 428 with a stake are sources with a Carbon Mapper plume any time after MARS's first plume
    there, not only inside MARS's quiet window (the 203); the alerts paragraph says money moves for none of them."""
    b = _readme_blocks()
    assert "a Carbon Mapper plume any time after MARS's first plume there" in b["whyNow"]
    assert b["whyNow"].index("lists") < b["whyNow"].index("still saw a plume") < b["whyNow"].index("kt CH₄/yr")
    assert "For most alerts no one answers, and the alert itself pays no one." in b["alerts"]
    assert "nothing moves when they arrive" not in b["alerts"] and "does money move" not in b["alerts"]


# ------------------------------------------------------------------------------------------------- problem wording
def test_problem_says_answering_moved_nothing_and_the_alert_pays_no_one():
    """The problem's opening says what the event study measured (answering an alert), that no alert pays anyone, and
    that the two datasets disagree about one gas facility (TKM_S_276), not about a "pipeline"."""
    a = _readme_blocks()["alerts"]
    assert "Answering an alert moved nothing we can measure: in our event study" in a and "A reply alone" not in a
    assert a.endswith("Two public datasets also disagree about the same gas facility:")
    sp = bad.spec_blocks(bad.load(bad.APP / "app.json"))["specProblem"]
    assert "two public datasets disagree about the same gas facility." in sp and "same pipeline" not in sp


def test_first_screen_quorum_is_the_production_rule_and_the_demo_quorum_is_said_once_in_limits():
    """The first screen gives the production quorum, k of k + 2 (a contest needs k + 1 and keeps a spare witness); the
    prototype's k of n is said once, in README Limits, from the committed policy."""
    core = bad.policy_mod.POLICY_V2["core"]
    k, n = core["k"], core["n"]
    seen, limit = bad.quorum_lines(core)
    paths = _readme_blocks()["paths"]
    assert seen in paths and f"{k} of {k + 2} witnesses" in seen and "n ≥ k + 2" in seen
    assert f"{k} of {n} witnesses" not in paths
    assert f"{k} of {n}" in limit and "`POLICY_V2`" in limit and "k + 1" in limit
    readme = (ROOT / "README.md").read_text()
    limits = readme[readme.index("## Limits\n"):]
    limits = limits[:limits.index("\n## ", 1)]
    assert limit in limits and readme.count(limit) == 1
    with pytest.raises(SystemExit):  # a prototype that already meets the production margin has no limit to state
        bad.quorum_lines({**core, "n": k + 2})


def test_readme_projection_bullet_leads_with_the_number_and_folds_the_rest():
    """The [PROJECTION] bullet leads with the central figure and its interval, every figure read from projection.json
    and checked against the reviewed headline; the stake and method follow; the reviewed headline (verbatim), the
    rolling origins and the whole-window comparison sit in one folded block; the interval is not repeated."""
    nums = _block((ROOT / "README.md").read_text(), "numbers").splitlines()
    lead = bad._forward_lead(PROJECTION)["numbers"]
    i = nums.index(f"- {lead}")
    assert lead.startswith(f"**[PROJECTION] ~{FWD['centralKtCh4PerYr']:.1f} kt CH₄/yr stopped**")
    assert nums[i + 1] == f"  - **Stake.** {FWD['stakeSentence']}"
    assert nums[i + 2].startswith("  - Method and output: [`pipeline/projection.py`](pipeline/projection.py)")
    assert "[`docs/preregistration.md`](docs/preregistration.md)" in nums[i + 2]
    fold = nums[i + 3:]
    assert fold[0] == "" and fold[1].startswith("<details><summary>") and fold[2] == "", "GitHub needs the blank line"
    body = "\n".join(fold[:fold.index("</details>")])
    for s in (FWD["headlineSentence"], bad._rolling(FWD["rollingSentence"]), FWD["comparisonSentence"]):
        assert s in body, s[:40]
    assert "the credited share of stake is" in body
    assert FWD["intervalSentence"] not in "\n".join(nums)
    with pytest.raises(SystemExit):
        bad._rolling(FWD["rollingSentence"].replace("the share is", "it is"))
    broken = copy.deepcopy(PROJECTION)
    broken["forward"]["card"]["headlineSentence"] = FWD["headlineSentence"].replace(
        f"~{FWD['centralKtCh4PerYr']:.1f} kt", "~99.9 kt")
    with pytest.raises(SystemExit):
        bad._forward_lead(broken)


def test_projection_sources_are_a_numbered_deduplicated_reference_list():
    """The PDF's source list is one numbered line per source (title, date, link), without the verbatim quotes, which
    stay in data/derived/projection_sources.json."""
    body = bad.projection_blocks()["projectionSources"]
    items = [x for x in body.splitlines() if re.match(r"\d+\. ", x)]
    urls = [s["url"] for s in PROJ_SOURCES["sources"].values()]
    assert len(items) == len({x.split(" — ")[0] for x in items})
    for sid, src in PROJ_SOURCES["sources"].items():
        if any(sid in (p.get(k) or []) for p in PROJ_SOURCES["parameters"].values() for k in ("sources", "informedBy")):
            assert sum(src["url"] in x and src["title"] in x for x in items) == 1, sid
        for q in src["quotes"]:
            assert q not in body
    assert urls


def test_refund_block_ends_without_the_that_is_why_sentence():
    b = _readme_blocks()
    for k in ("refund", "readme:refund"):
        assert "That is why" not in b[k], k


@NEEDS_DEVPOST
def test_devpost_whats_next_names_the_objective_challenge_rule():
    dp = (ROOT / "docs" / "devpost.md").read_text()
    nxt = dp.split("## What's next", 1)[1].split("\n## ", 1)[0]
    assert ("an objective challenge left unanswered ends CONFIRMED, or its bond goes to the buyer (a v2 wrapper; the "
            "core stays byte-identical)") in " ".join(nxt.split())


def test_headline_v3_everywhere_and_v2_nowhere():
    # the public export ships docs/spec-public.md as docs/spec.md
    spec = next(p for p in (ROOT / "docs" / "spec-public.md", ROOT / "docs" / "spec.md") if p.exists())
    docs = _docs() | {"spec": spec.read_text()}
    for path, t in docs.items():
        assert "witnesses decide" not in " ".join(t.split()), path
    sp = " ".join(docs["spec"].split())
    assert "cannot delete the sighting or hold the money" in sp


PREREG = ROOT / "docs" / "preregistration.md"
FWD_TESTS = ROOT / "pipeline" / "tests" / "test_projection_forward.py"


def _git(*args: str, text: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=text)


def _have(sha: str) -> bool:
    return _git("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0


@pytest.mark.skipif(not PREREG.exists(), reason="docs/preregistration.md is not in this checkout")
def test_preregistration_doc_matches_the_pinned_blocks_and_the_commit_record():
    """docs/preregistration.md names each pre-registered block with the SHA-256 its test pins (recomputed here from
    pipeline/projection_time.py), the test's name, and the commits that fixed and first computed it; where the
    development history is present, each listed time is the commit's own and each computation commit descends from its
    pre-registration commit. No private path."""
    import hashlib
    from datetime import datetime, timezone

    import projection_time as ptime

    doc, tests = PREREG.read_text(), FWD_TESTS.read_text()
    for const, block, test in (("FORWARD_SHA256", ptime.FORWARD,
                                "test_the_pre_registration_is_byte_for_byte_the_committed_one"),
                               ("FORWARD_ROLLING_SHA256", ptime.FORWARD_ROLLING,
                                "test_the_rolling_origin_pre_registration_is_byte_for_byte_the_committed_one"),
                               ("FORWARD_CONTROL_LENGTH_SHA256", ptime.FORWARD_CONTROL_LENGTH,
                                "test_the_full_length_control_rule_is_byte_for_byte_the_committed_one")):
        pinned = re.search(rf'^{const} = "([0-9a-f]{{64}})"', tests, re.M).group(1)
        assert hashlib.sha256(json.dumps(block, sort_keys=True, ensure_ascii=False).encode()).hexdigest() == pinned
        assert f"`{pinned}`" in doc and f"`{test}`" in doc, const
    for v in (ptime.FORWARD["D"], ptime.FORWARD["creditEnd"], *ptime.FORWARD_ROLLING["origins"],
              ptime.FORWARD_CONTROL_LENGTH["origin"], ptime.FORWARD_CONTROL_LENGTH["creditEnd"]):
        assert v in doc, v
    # the release gate (check_public) scans the exported doc for every private reference; here the paths only
    for private in ("/Users/", "docs/tasks", "docs/progress", ".claude"):
        assert private not in doc, private
    rows = re.findall(r"^\| ([^|]+?) \| `([0-9a-f]{40})` \| (\S+Z) \| (\S+Z) \|$", doc, re.M)
    assert len(rows) >= 4
    if not _have(rows[0][1]):
        pytest.skip("single-commit export: the commit record is attested by the development repository")

    def utc(sha: str, fmt: str) -> str:
        out = _git("show", "-s", f"--format={fmt}", sha, text=True).stdout.strip()
        return datetime.fromisoformat(out).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    for _, sha, authored, committed in rows:
        if _have(sha):  # a pre-rebase copy may be pruned from a clone; the rebased commits are always checked
            assert (utc(sha, "%aI"), utc(sha, "%cI")) == (authored, committed), sha
    shas = {role.strip(): sha for role, sha, *_ in rows}
    for pre, comp in (("FORWARD pre-registered", "FORWARD first computed"),
                      ("FORWARD_ROLLING pre-registered", "FORWARD_ROLLING first computed"),
                      ("FORWARD_CONTROL_LENGTH pre-registered", "FORWARD_CONTROL_LENGTH first computed")):
        assert _git("merge-base", "--is-ancestor", shas[pre], shas[comp]).returncode == 0, (pre, comp)


# ----------------------------------------------------------------------------- scaling and sustainability (IEEE A6)
SCALING_NAMES =re.compile(r"Phase \d|\bL2\b|SHA-256|EIP-170|ERC-20")


def test_scaling_figures_are_generated_and_current(tmp_path, monkeypatch):
    """docs/SCALING.md types none of our figures: the gas-vs-set-size table, the Sepolia cost per call, the cost per
    covenant-year, latency, who pays and the ledger footprint are generated from scale.json, measurements.json,
    projection.json and the committed policy; a stale or hand-edited figure fails `make check`."""
    sc = (ROOT / "docs" / "SCALING.md").read_text()
    blocks = bad.scaling_blocks(bad.load(bad.APP / "app.json"))
    for name, body in blocks.items():
        assert f"<!-- gen:{name}:begin -->\n{body}\n<!-- gen:{name}:end -->" in sc, f"{name} is stale: run make readme"
    scale = json.loads((DERIVED / "scale.json").read_text())
    for r in scale["rows"]:
        row = next(x for x in blocks["scSetSize"].splitlines() if x.startswith(f"| {r['n']:,} "))
        for v in (r["proofSteps"], r["createCovenantGas"], r["proposeEpisodeGas"], r["proposeEpisodeCalldataBytes"]):
            assert f"| {v:,} |" in row, (r["n"], v)
        assert ("[SIM padding]" in row) == (not r["real"])
    assert f"{scale['fit']['proposeEpisodeGasPerProofStep']:,} gas per proof step" in blocks["scSetSize"]
    m = json.loads((DERIVED / "measurements.json").read_text())
    for f in ("createCovenant", "proposeEpisode", "finalize", "withdraw", "pool_openClaim", "pool_attestCleanPass",
              "pool_countPass"):
        v = m["functions"][f]
        assert f"| {v['gas_sepolia_median']:,} | {v['usd_at_stated_gwei']:.2f} | {v['n']} |" in blocks["scCalls"], f
    lat = m["latency_seconds"]
    assert f"p50 {lat['p50']} s, p90 {lat['p90']} s (n = {lat['n']}" in blocks["scToday"]
    cost = json.loads((DERIVED / "projection.json").read_text())["card"]["costSentence"]
    assert bad._whole_window(cost) in blocks["scPays"]  # the per-tonne cost is the whole-window comparison's
    assert "over each member's whole window" in blocks["scPays"]
    pool = bad.policy_mod.POLICY_V2["pool"]
    from decimal import Decimal

    assert f"{(Decimal(int(pool['observerFeeWei'])) / 10**18).normalize():f} ETH per counted pass" in blocks["scPays"]
    assert f"{m['transactions']['logged']} Sepolia transactions" in blocks["scFootprint"]
    outside = re.sub(r"<!-- gen:(\w+):begin -->.*?<!-- gen:\1:end -->", "", sc, flags=re.S)
    outside = SCALING_NAMES.sub("", re.sub(r"`[^`]*`|\([^)]*\.(?:md|json|sol)\)", "", outside))
    typed = [x.strip() for x in outside.splitlines() if re.search(r"\d", x)]
    assert not typed, f"hand-typed digits outside the generated blocks: {typed}"
    readme = (ROOT / "README.md").read_text()
    assert readme.count("](docs/SCALING.md)") == 1, "README links docs/SCALING.md from exactly one line"
    edited = tmp_path / "SCALING.md"
    edited.write_text(sc.replace(f"{lat['p50']} s", "9.99 s", 1))
    monkeypatch.setattr(bad, "SCALING", edited)
    with pytest.raises(SystemExit, match="SCALING.md generated blocks are stale"):
        bad.readme(write=False)


# ------------------------------------------------------------------------------------------- forward-only demo clock
class FakeChain:
    local = True

    def __init__(self, t: int):
        self.t, self.next, self.calls = t, None, []
        self.w3 = type("W3", (), {"provider": type("P", (), {"make_request": self._rpc})()})()

    def _rpc(self, method, params):
        self.calls.append(method)
        if method == "evm_setNextBlockTimestamp":
            self.next = params[0]
        elif method == "evm_mine":
            self.t = self.next if self.next is not None else self.t + 1
            self.next = None

    def now(self) -> int:
        return self.t


def test_clock_moves_forward_and_records_the_skip(monkeypatch):
    monkeypatch.delenv("REALTIME", raising=False)
    monkeypatch.setattr(demo, "SKIPPED", {})
    ch = FakeChain(1_000)
    assert demo.advance_clock(ch, 1_000 + 86_400, "challenge window", log=lambda *_: None) == 86_400
    assert ch.now() == 1_000 + 86_400 and demo.SKIPPED == {"challenge window": 86_400}


def test_clock_never_moves_back(monkeypatch):
    monkeypatch.delenv("REALTIME", raising=False)
    monkeypatch.setattr(demo, "SKIPPED", {})
    ch = FakeChain(5_000)
    assert demo.advance_clock(ch, 4_000, "challenge window", log=lambda *_: None) == 0
    assert ch.now() == 5_000 and ch.calls == [] and demo.SKIPPED == {}


def test_clock_realtime_waits_and_skips_nothing(monkeypatch):
    monkeypatch.setenv("REALTIME", "1")
    monkeypatch.setattr(demo, "SKIPPED", {})
    slept = []
    monkeypatch.setattr(demo.time, "sleep", slept.append)
    ch = FakeChain(1_000)  # each evm_mine adds one second, like the wall clock moving on
    assert demo.advance_clock(ch, 1_005, "challenge window", log=lambda *_: None) == 0
    assert ch.now() >= 1_005 and "evm_setNextBlockTimestamp" not in ch.calls and slept and demo.SKIPPED == {}


def test_minutes_under_a_day_days_above():
    assert demo._days(600) == "10 min" and demo._days(86_400) == "1 day" and demo._days(2 * 86_400) == "2 days"
    assert demo._days(86_399) == "1 day"  # a challenge window skipped from one second in reads as a day


def test_realtime_cutoff_matches_contract():
    """REALTIME=1 refuses exactly when the wall clock leaves MethaneCovenant.MAX_LOOKBACK from the v1 start, and the
    message names that date."""
    import re
    from datetime import datetime, timezone

    sol = (ROOT / "contracts" / "src" / "MethaneCovenant.sol").read_text()
    days = int(re.search(r"MAX_LOOKBACK\s*=\s*(\d+)\s*days", sol).group(1))
    assert demo.MAX_LOOKBACK == days * 86_400
    cutoff = demo.COVENANT_START + demo.MAX_LOOKBACK
    assert demo.realtime_refusal(cutoff) is None
    msg = demo.realtime_refusal(cutoff + 1)
    assert msg and datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d") in msg


def test_deterministic_mode_refuses_other_chains():
    with pytest.raises(RuntimeError):
        demo.Determinism(11155111, "seed")
    assert demo.Determinism(11155111, "seed", on=False).kw() == {}  # off: the pipeline's random defaults
    a, b = demo.Determinism(31337, "seed"), demo.Determinism(31337, "seed")
    assert [a.kw() for _ in range(3)] == [b.kw() for _ in range(3)]  # same seed, same nonces


def test_clock_refuses_a_public_chain():
    ch = FakeChain(0)
    ch.local = False
    with pytest.raises(RuntimeError):
        demo.advance_clock(ch, 10, "challenge window")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
