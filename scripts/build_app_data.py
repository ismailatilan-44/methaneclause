#!/usr/bin/env python3
"""Build app/data/ from committed run files, so the page needs no server logic and no number is typed by hand.

    pipeline/.venv/bin/python scripts/build_app_data.py      # make app

    pipeline/.venv/bin/python scripts/build_app_data.py --resolve-scenes   # make app-scenes (network, keyless)

Reads data/derived (case files, audit, testnet run, measurements, Annex D export, adversarial set, and the pipeline's v2 files
or, until they land, their MOCK placeholders in data/derived/mock/), contracts/deployments/*.json, the contract ABIs,
the last local demo run (out/demo/run.json: hybrid signatures and observer receipts for the tamper and observer panels;
without it the previous app.json values are kept), the step-0 CleanPass vector, and app/scene_ids.json (Carbon Mapper
scene ids for the CH4 tile layer, written by --resolve-scenes). Writes app/data/app.json plus copies of the export
files and the JCS test vectors. Output is deterministic (RFC 6979 ECDSA, deterministic ML-DSA-44): two runs on the same
inputs write the same bytes. Fails closed (exit 1) if a signature it writes does not verify.
"""
from __future__ import annotations

import hashlib
import json
import math
import shutil
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# README size budget: 32,000 bytes since the final sprint (user, 21 Oct; not cut back to 25,000, user, same day).
README_BUDGET = 32_000
sys.path.insert(0, str(ROOT / "pipeline"))

from common import DERIVED  # noqa: E402
from policy import POLICY, TIER_AMOUNTS_WEI, TIER_MIN_KGH  # noqa: E402
import policy as policy_mod  # noqa: E402
from canon import canonical_bytes, evidence_hash  # noqa: E402
from eth_utils import to_checksum_address  # noqa: E402
from observer import attest, pq  # noqa: E402
from observer.service import ANVIL_INDEX, anvil_key  # noqa: E402
import cure_arc  # noqa: E402
from final_reach import final_reach  # noqa: E402

APP = ROOT / "app" / "data"
CASES = ("TKM_S_276", "DZA_S_012", "USA_S_077")
PLUME_KEYS = ("plume_id", "scene_timestamp", "published_at", "emission_auto", "emission_uncertainty_auto",
              "instrument", "platform", "lat", "lon", "source_name", "origin_dist_km")


def load(p: Path):
    return json.loads(p.read_text())


# ---------------------------------------------------------------------------------------------- v2 (spec-v2 §8)
MOCK_DIR = DERIVED / "mock"
SCENE_IDS = ROOT / "app" / "scene_ids.json"
CM_API = "https://api.carbonmapper.org/api/v1"
VECTOR = ROOT / "pipeline" / "tests" / "vectors" / "cleanpass_v1.json"
CLEAN_SIGNERS = ("OBS_A", "OBS_B")  # Anvil's public test accounts 3 and 4, the demo's observers [SIM]
MOCK_USED: set[str] = set()  # every data/derived/mock/ file the page renders; the page badges each block that uses one


SUMMARY_KEYS = ("candidates", "qualifiedBeforeFirstPlume", "upperBoundBpAtThatCount", "firstPlumeAfterSince",
                "maxCountedFloorKgH", "countedAtCloudBp2500", "statusLabel")


def fail(msg: str):
    """Every inconsistency between the files the page quotes stops the build (exit 1, nothing written)."""
    sys.exit(f"build_app_data: {msg}")


def u_bp(n: int) -> int:
    """u(N) = 1 - 0.025^(1/N) in basis points (spec-v2 §2), recomputed here to check the committed table."""
    return round(10000 * (1 - 0.025 ** (1 / n)))


def check_table(table: list[int]) -> None:
    if not table or any(b != u_bp(i + 1) for i, b in enumerate(table)):
        fail("upper_bound_bp in cleanruns.json is not 1 - 0.025^(1/N)")


def require_summary(doc: dict) -> dict:
    """A real clean_passes file must carry its summary; a MOCK file may not have one."""
    summ = doc.get("summary")
    missing = [k for k in SUMMARY_KEYS if summ is None or k not in summ]
    if missing:
        fail(f"{doc.get('case')}: clean_passes summary missing {missing}")
    return summ


def check_upper_bound(case: str, summ: dict, n_shown: int, table: list[int]) -> None:
    """The bound the card shows is table[N-1] for the count shown; the pipeline's summary must carry the same value."""
    want = table[n_shown - 1] if n_shown else None
    if summ["upperBoundBpAtThatCount"] != want:
        fail(f"{case}: summary upperBoundBpAtThatCount {summ['upperBoundBpAtThatCount']} != u({n_shown}) = {want}")


def check_floors(case: str, used: dict, floors_doc: dict, pool: dict | None) -> None:
    """The floor on the card comes from the clean_passes file's rules; it must equal data/derived/floors.json and the
    hashed POLICY_V2 pool table for every instrument the card uses. Never typed here."""
    table = {i: v["kgH"] for i, v in floors_doc["instrumentFloorKgH"].items()}
    pool_t = (pool or {}).get("instrumentFloorKgH")
    if not pool_t:
        fail(f"{case}: POLICY_V2 has no instrumentFloorKgH table to check the card's floor against")
    for inst, kgh in used.items():
        if table.get(inst) != kgh or pool_t.get(inst) != kgh:
            fail(f"{case}: floor for {inst} is {kgh} kg/h on the card, {table.get(inst)} in floors.json, "
                 f"{pool_t.get(inst)} in POLICY_V2")


def check_signers(entries: list[dict], expected: dict[str, str]) -> None:
    """Each recovered CleanPass signer must be the address the demo registered for that org (out/demo/run.json)."""
    for e in entries:
        want = expected.get(e["org"])
        if want is None or e["signer"].lower() != want.lower():
            fail(f"CleanPass signature of {e['org']} recovers to {e['signer']}, expected {want}")


def v2_file(real: Path, mock_name: str) -> tuple[dict | None, dict]:
    """The pipeline's real file if it exists, else the MOCK placeholder. Returns (doc, provenance)."""
    if real.exists():
        return load(real), {"path": str(real.relative_to(ROOT)), "mock": False}
    m = MOCK_DIR / mock_name
    if not m.exists():
        return None, {"path": None, "mock": False}
    doc = load(m)
    if "MOCK" not in json.dumps(doc):
        sys.exit(f"{m} is in mock/ but does not say MOCK; refusing to render it")
    MOCK_USED.add(str(m.relative_to(ROOT)))
    return doc, {"path": str(m.relative_to(ROOT)), "mock": True, "note": doc.get("note")}


def iso_day(ts: str) -> str:
    return ts[:10]


def unix_day(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def scene_name(plume_id: str) -> str:
    return plume_id.rsplit("-", 1)[0]


def plume_view(p: dict) -> dict:
    return {"plumeId": p["plume_id"], "sceneName": scene_name(p["plume_id"]), "sceneAt": p["scene_timestamp"],
            "publishedAt": p["published_at"], "instrument": p["instrument"], "platform": p["platform"],
            "rateKgH": round(p["emission_auto"]), "uncKgH": round(p["emission_uncertainty_auto"] or 0),
            "lat": p["lat"], "lon": p["lon"], "cmSource": p["source_name"]}


def ledger_plumes(name: str, attested_plume: str | None) -> tuple[dict | None, dict | None, dict]:
    """(leak, first detection after the MARS label, case) from the committed case file.

    Leak = the attested plume when this case carries the attested episode, else the last quantified Carbon Mapper
    detection on or before MARS's last plume (`post_mars_last_plume.since`). The relapse candidate = the largest plume
    of the first scene after that date."""
    c = load(DERIVED / "cases" / f"{name}.json")
    since = c["post_mars_last_plume"]["since"]
    q = sorted((p for p in c["cm_plumes_within_radius"] if p.get("emission_auto") is not None),
               key=lambda p: p["scene_timestamp"])
    leak = next((p for p in q if p["plume_id"] == attested_plume), None)
    if leak is None:
        before = [p for p in q if iso_day(p["scene_timestamp"]) <= since]
        leak = before[-1] if before else None
    after = [p for p in q if iso_day(p["scene_timestamp"]) > since]
    nxt = None
    if after:
        first_day = iso_day(after[0]["scene_timestamp"])
        nxt = max((p for p in after if iso_day(p["scene_timestamp"]) == first_day), key=lambda p: p["emission_auto"])
    return (plume_view(leak) if leak else None), (plume_view(nxt) if nxt else None), c


def relapse_of(case: dict, cp_doc: dict) -> dict | None:
    """The first plume after the claim's `since` (= episode + link window): the scene the pipeline's summary names, else (MOCK)
    the first quantified scene after `since`; the largest plume of that scene from the committed case file."""
    q = [p for p in case["cm_plumes_within_radius"] if p.get("emission_auto") is not None]
    named = ((cp_doc.get("summary") or {}).get("firstPlumeAfterSince") or {}).get("sceneName")
    if named:
        hits = [p for p in q if scene_name(p["plume_id"]) == named]
        if not hits:
            fail(f"{cp_doc['case']}: summary names relapse scene {named}, not in the case file's plumes")
    else:
        since = cp_doc["since"]
        after = sorted((p for p in q if datetime.fromisoformat(p["scene_timestamp"].replace("Z", "+00:00"))
                        .timestamp() > since), key=lambda p: p["scene_timestamp"])
        hits = [p for p in after if after and scene_name(p["plume_id"]) == scene_name(after[0]["plume_id"])]
    return plume_view(max(hits, key=lambda p: p["emission_auto"])) if hits else None


def scene_ids() -> dict:
    """Carbon Mapper scene ids by scene name: the pipeline's clean_passes files first, then app/scene_ids.json."""
    out = load(SCENE_IDS)["scenes"] if SCENE_IDS.exists() else {}
    for f in sorted((DERIVED / "cases").glob("*.clean_passes.json")):
        for x in load(f)["scenes"]:
            if x.get("sceneName"):
                out[x["sceneName"]] = {"id": x["sceneProviderId"]}
    return out


def build_ledger(attested_plume: str) -> dict:
    """Three-panel card per source (spec-v2 §8.1): leak -> qualified clean passes -> relapse."""
    ids = scene_ids()
    cleanruns, cr_prov = v2_file(DERIVED / "cleanruns.json", "cleanruns.json")
    pol_v2 = getattr(policy_mod, "POLICY_V2", None)
    pool = (pol_v2 or {}).get("pool") if isinstance(pol_v2, dict) else None
    out = {}
    for name in ("TKM_S_276", "DZA_S_012"):
        leak, nxt, c = ledger_plumes(name, attested_plume)
        pm = c["post_mars_last_plume"]
        for v in (leak, nxt):
            if v:
                v["sceneId"] = ids.get(v["sceneName"], {}).get("id")
        cp_doc, cp_prov = v2_file(DERIVED / "cases" / f"{name}.clean_passes.json", f"{name}.clean_passes.json")
        card = {"source": name, "country": c["mars_source"]["country"],
                "sourceType": c["mars_source"]["source_type"],
                "mars": {"category": c["mars_source"]["persistency_category"],
                         "lastPlume": iso_day(c["mars_source"]["last_plume_date"]),
                         "facility": [c["mars_source"]["lat"], c["mars_source"]["lon"]]},
                "postMars": {"since": pm["since"], "passes": pm["n_cm_passes"], "detected": pm["n_cm_detected"],
                             "radiusKm": pm["radius_km"]},
                # every Carbon Mapper pass since the MARS label carried a plume -> PLUME_ON_SCENE on each
                "allPassesDetected": pm["n_cm_passes"] > 0 and pm["n_cm_detected"] == pm["n_cm_passes"],
                "leak": leak, "clean": None, "relapse": None}
        if cp_doc is not None:
            rl = relapse_of(c, cp_doc)
            if rl:
                rl["sceneId"] = ids.get(rl["sceneName"], {}).get("id")
            card["clean"] = clean_view(cp_doc, cp_prov, rl, cleanruns, cr_prov, pool)
            if card["clean"]["n"] > 0:  # a relapse only matters to a claim that had something to cancel
                card["relapse"] = rl
        out[name] = card
    return out


def cap_reasons(model: dict) -> dict:
    """Why a refund is capped and cancellable (spec-v2 §2, §15 B1 audit): the rule's held-out false-zero rate, and lane
    B's catalogue sentence `cleanruns.json` -> card.sentence, rendered verbatim (a reason for the cap, never evidence of
    repair). No comparator, no "re-detected later" and no base rate are read here; until the pipeline writes the sentence the
    page shows nothing in its place."""
    cr, prov = v2_file(DERIVED / "cleanruns.json", "cleanruns.json")
    card = (cr or {}).get("card") or {}
    catalogue = None
    if isinstance(card.get("sentence"), str) and card["sentence"].strip():
        catalogue = {"sentence": card["sentence"], "unit": card.get("unitLabel"), "provenance": prov}
    return {"catalogue": catalogue, "catalogueProvenance": prov,
            "falseZero": [model[t]["B1_persistence"]["false_zero_rate"] for t in ("test_a", "test_b")
                          if "B1_persistence" in model.get(t, {})]}


def clean_view(doc: dict, prov: dict, relapse: dict | None, cleanruns: dict | None, cr_prov: dict,
               pool: dict | None) -> dict:
    """Panel 2 of the card from the pipeline's clean_passes file (real or MOCK). Under spec-v2 §15 (B1) the card counts the
    passes qualified with no look-ahead (`qualifiedNoLookAhead`) before the first plume after `since`."""
    relapse_t = int(datetime.fromisoformat(relapse["sceneAt"].replace("Z", "+00:00")).timestamp()) if relapse else None
    scenes = [{"sceneName": x.get("sceneName") or x["sceneProviderId"], "sceneProviderId": x["sceneProviderId"],
               "sceneAt": x["sceneAt"], "publishedAt": x["publishedAt"], "instrument": x["instrument"],
               "cloudCoverPct": x["cloudCoverPct"], "cloudCoverPctAssessed": x["cloudCoverPctAssessed"],
               "notCloudy": x["notCloudy"], "marsNearby": x["marsNearby"]["count"], "qualified": x["qualified"],
               "refusal": x["refusal"], "afterRelapse": bool(relapse_t and x["sceneAt"] >= relapse_t)}
              for x in doc["scenes"]]
    counted = [x for x in scenes if x["qualified"] is True and not x["afterRelapse"]]
    n = len(counted)
    ub = (cleanruns or {}).get("upper_bound_bp") or []
    check_table(ub)
    instruments = sorted({x["instrument"] for x in counted})
    floors = (doc.get("rules") or {}).get("instrumentFloorKgH") or (pool or {}).get("instrumentFloorKgH")
    if floors and instruments:
        check_floors(doc["case"], {i: floors[i] for i in instruments}, load(DERIVED / "floors.json"), pool)
    floor = max((floors[i] for i in instruments), default=None) if floors else None
    max_cloud = (pool or {}).get("maxCloudBp")
    at_bin = [x for x in counted if max_cloud and 100 * max(x["cloudCoverPct"], x["cloudCoverPctAssessed"]) == max_cloud]
    status = None
    if not prov["mock"]:
        summ = require_summary(doc)
        if summ["qualifiedBeforeFirstPlume"] != n:
            fail(f"{doc['case']}: {n} counted scenes != summary qualifiedBeforeFirstPlume {summ['qualifiedBeforeFirstPlume']}")
        check_upper_bound(doc["case"], summ, n, ub)
        if summ["maxCountedFloorKgH"] != floor:
            fail(f"{doc['case']}: card floor {floor} != summary maxCountedFloorKgH {summ['maxCountedFloorKgH']}")
        if summ["countedAtCloudBp2500"] != len(at_bin):
            fail(f"{doc['case']}: {len(at_bin)} counted passes at the cloud bin != summary {summ['countedAtCloudBp2500']}")
        status = summ["statusLabel"]
    refusals: dict[str, int] = {}
    for x in scenes:
        if x["qualified"] is not True and x["refusal"]:
            refusals[x["refusal"]] = refusals.get(x["refusal"], 0) + 1
    return {"provenance": prov, "since": doc["since"], "scenes": scenes, "n": n, "instruments": instruments,
            "candidates": len(scenes), "refusals": dict(sorted(refusals.items())),
            "upperBoundBp": ub[min(n, len(ub)) - 1] if n and ub else None, "upperBoundProvenance": cr_prov,
            "floorKgH": floor, "statusLabel": status,
            "installments": (pool or {}).get("installments"), "d0": doc.get("d0"),
            "covenantLabel": (doc.get("covenant") or {}).get("label"),
            "maxCloudPct": max_cloud // 100 if max_cloud else None,
            "atCloudBin": {"n": len(at_bin), "byInstrument": {i: sum(1 for x in at_bin if x["instrument"] == i)
                                                              for i in sorted({x["instrument"] for x in at_bin})}},
            "lowerBound": not prov["mock"]}


def demo_clock(local: Path) -> dict:
    """Skip controls (spec-v2 §8.9): the windows the demo skipped on its pinned clock, from out/demo/run.json."""
    if local.exists():
        r = load(local)
        return {"skippedSeconds": r.get("skippedSeconds") or {}, "clock": r.get("clock"),
                "windows": {"challenge window": POLICY["challengeWindow"], "contest window": POLICY["contestWindow"],
                            "link window": POLICY["linkWindow"]}}
    prev = APP / "app.json"
    return load(prev).get("demoClock") or {} if prev.exists() else {}


REPLAY_KEYS = ("finalize (episode)", "fund (buyer opens)", "openClaim", "release", "attestCorroboration",
               "attestCleanPass (seeded bad pass)", "challengePass CLOUD_FLAG", "resolvePass WITHDRAW_CLEAN",
               "proposeEpisode (relapse)", "relapse while the episode is PROPOSED", "finalize (relapse)",
               "relapse (cancel)", "openClaim (relapse episode)", "release while the relapse is PROPOSED",
               "createCovenant (SuretyBond)", "fund (surety)", "operator tries SuretyBond.withdraw", "settle",
               "SuretyBond.pull", "SuretyBond.withdraw (funder)")


SPONSOR_WEI = ("openedByBuyerWei", "sponsorTopUpWei", "buyerContributionWei", "sponsorContributionWei", "balanceWei",
               "reservedWei", "releasedWei", "forfeitedWei", "observerFeesWei")


def sponsor_ledger(key: str, v: dict) -> dict:
    """The sponsor view of one replay variant, from the pool state demo.py read back after settlement. Fail closed: a
    missing field, a negative amount or a balance that is not what went in minus what went out stops the build. The
    remainder and the sponsor's share are the contract's sponsorWithdraw formula (unreserved x contributed /
    outstanding), computed here because the replay does not call sponsorWithdraw."""
    led = v.get("sponsorLedger")
    if not isinstance(led, dict) or any(k not in led for k in SPONSOR_WEI) or "sponsorWithdrawn" not in led:
        fail(f"out/demo/replay.json variant {key} has no complete sponsorLedger: run make demo again")
    try:
        w = {k: int(led[k]) for k in SPONSOR_WEI}
    except (TypeError, ValueError):
        fail(f"out/demo/replay.json variant {key}: sponsorLedger amounts must be integers in wei")
    if any(x < 0 for x in w.values()):
        fail(f"out/demo/replay.json variant {key}: negative sponsorLedger amount")
    if (w["buyerContributionWei"] != w["openedByBuyerWei"] or w["sponsorContributionWei"] != w["sponsorTopUpWei"]
            or w["reservedWei"] > w["balanceWei"]
            or w["balanceWei"] != w["openedByBuyerWei"] + w["sponsorTopUpWei"] - w["releasedWei"] - w["observerFeesWei"]):
        fail(f"out/demo/replay.json variant {key}: the pool's balance is not opened + top-up - released - fees, or the "
             "contributions differ from the funding transactions")
    # forfeited = the cancelled claim's cap minus what it paid; cap and paid share are the replay's own records
    cap, out = v.get("cap"), v.get("outcome")
    if out:  # a replay without an outcome is refused as incomplete by replay_view
        if (not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0
                or w["forfeitedWei"] != cap - cap * int(out["paidBp"]) // 10_000
                or w["forfeitedWei"] > w["releasedWei"] + w["balanceWei"]):
            fail(f"out/demo/replay.json variant {key}: forfeitedWei is not the claim's cap minus what it paid")
    # IncentivePool keeps contributionsOutstanding private (no getter); before any sponsorWithdraw it equals the sum of
    # the public `contributions(covenantId, funder)` reads, which is what the replay recorded
    if led["sponsorWithdrawn"] is not False:
        fail(f"out/demo/replay.json variant {key}: a sponsorWithdraw happened, so the outstanding contributions are no "
             "longer the funders' sum")
    unreserved = w["balanceWei"] - w["reservedWei"]
    outstanding = w["buyerContributionWei"] + w["sponsorContributionWei"]
    return {k: str(x) for k, x in w.items()} | {
        "unreservedWei": str(unreserved), "sponsorShareWei": str(unreserved * w["sponsorContributionWei"] // outstanding),
        "sponsorWithdrawn": bool(led["sponsorWithdrawn"])}


def replay_view(path: Path) -> dict | None:
    """The DZA replay (spec-v2 §10) as the page shows it: per covenant variant the outcome and the transactions of
    each beat (hash, status, block), from out/demo/replay.json; deterministic fields only (no wall-clock seconds)."""
    if not path.exists():
        prev = APP / "app.json"
        return load(prev).get("replay") if prev.exists() else None
    r = load(path)
    out = {"chainId": r["chainId"], "clock": r["clock"], "pool": r["pool"], "suretyBond": r.get("suretyBond"),
           "skippedSeconds": r["skippedSeconds"], "txs": r["txs"], "status0": r["status0"], "checks": r["checks"],
           "variants": {}}
    for key, v in r["variants"].items():
        txs = {}
        for t in r["txlog"]:
            prefix = f"replay {key}: "
            if t["label"].startswith(prefix) and t["label"][len(prefix):] in REPLAY_KEYS:
                txs.setdefault(t["label"][len(prefix):], []).append(
                    {"tx": t["tx"], "status": t["status"], "block": t["block"], "revert": t.get("revert")})
        out["variants"][key] = {"label": v["label"], "outcome": v.get("outcome"), "covenantId": v["cid"],
                                "episodeId": v.get("eid"), "relapseEpisodeId": v.get("eid2"),
                                "claimId": v.get("firstClaim") or v.get("claim"), "txs": txs,
                                "sponsor": sponsor_ledger(key, v)}
    if out["status0"] < 1 or not all(v["outcome"] for v in out["variants"].values()):
        fail("out/demo/replay.json is incomplete (no status-0 step or no cancel outcome): run make demo again")
    return out


def exports_view() -> dict:
    """C4: the TKM verdict-only run (out/demo/verdict.json) with its credit-note register, and the pipeline's Annex C example
    (claimHash = the bytes32 recordMitigationClaim takes) — files copied to app/data, deterministic fields only."""
    from export import annex_c

    out = {}
    v = ROOT / "out" / "demo" / "verdict.json"
    if v.exists():
        r = load(v)
        out["verdict"] = {k: r[k] for k in ("covenantId", "episodeId", "claimId", "finalizeTx", "units", "noteRef",
                                            "noteHash", "annexC", "checks", "txs", "status0", "pool")}
        note = load(ROOT / "out" / "demo" / "TKM_S_276.credit_note.json")["notes"][0]
        if note["verdict"]["tx"] != r["finalizeTx"] or note["verdict"].get("blockTime") != r.get("finalizeTime"):
            fail("the credit note does not cite the verdict's finalize transaction and its block time")
        for ext in ("json", "csv"):
            shutil.copy(ROOT / "out" / "demo" / f"TKM_S_276.credit_note.{ext}", APP / f"TKM_S_276.credit_note.{ext}")
    elif (APP / "app.json").exists():
        out["verdict"] = (load(APP / "app.json").get("exports") or {}).get("verdict")
    form_path = DERIVED / "export" / "TKM_S_276.annex_c.example.json"
    form = annex_c.from_json(form_path.read_text())
    out["annexC"] = {"claimHash": annex_c.claim_hash(form), "label": form["x-methaneclause"]["label"],
                     "dateLabels": [k for k in form["fields"] if "date" in k.lower() or "DD-MM-YYYY" in k]}
    if out.get("verdict") and out["verdict"]["annexC"]["claimHash"] != out["annexC"]["claimHash"]:
        fail("the Annex C claimHash recorded in the verdict run is not the committed example's")
    for ext in ("json", "csv"):
        shutil.copy(DERIVED / "export" / f"TKM_S_276.annex_c.example.{ext}", APP / f"TKM_S_276.annex_c.example.{ext}")
    return out


def portfolio_view() -> dict | None:
    """C4: the portfolio covenant over the pipeline's set v3 (out/demo/portfolio.json; mechanics only, no stake or backtest
    figures), deterministic fields; the set's root and size are checked against the committed set file."""
    path = ROOT / "out" / "demo" / "portfolio.json"
    if not path.exists():
        prev = APP / "app.json"
        return load(prev).get("portfolio") if prev.exists() else None
    r = load(path)
    ss = load(DERIVED / "sourceset_v3.json")
    if r["root"] != ss["root"] or r["n"] != ss["n"]:
        fail("out/demo/portfolio.json does not match data/derived/sourceset_v3.json: run make demo again")
    rej = [t for t in r["txlog"] if t["status"] == 0]
    unsized = sorted(x["source_name"] for x in ss["sources"] if not x["linked_cm_sources"])
    sentence = ((load(DERIVED / "model" / "bond_sizing_v3.json").get("card") or {}).get("portfolioSentence"))
    if not sentence or any(u not in sentence for u in unsized) or f"{ss['n']} facilities" not in sentence:
        fail("bond_sizing_v3.json card.portfolioSentence does not name the set's size and every leaf without a Carbon "
             "Mapper source")
    return {k: r[k] for k in ("covenantId", "root", "n", "bondWei", "poolWei", "episodeId", "claimId", "proofSteps",
                              "checks", "txs", "status0")} | {
        "rejected": [{"label": t["label"], "tx": t["tx"], "revert": t.get("revert")} for t in rej],
        "unsized": unsized, "sentence": sentence}


def clean_look_rules() -> dict:
    """The clean-look rule the page's drawer states, read from the committed policy file (data/derived/policy_v2.json,
    POLICY_V2's pool part). Refuses a file that no longer equals POLICY_V2: the page never states a rule the pool does
    not run."""
    doc = load(DERIVED / "policy_v2.json")["policies"]["POLICY_V2"]["policy"]["pool"]
    if json.loads(json.dumps(doc)) != json.loads(json.dumps(policy_mod.POLICY_V2["pool"])):
        fail("data/derived/policy_v2.json is not POLICY_V2 (stale or edited): run pipeline/policy.py --write")
    day = 86_400
    if doc["cureTerm"] % day or doc["publicationLag"] % day:
        fail("data/derived/policy_v2.json: cure term or publication lag is not a whole number of days")
    return {
        "source": "data/derived/policy_v2.json (POLICY_V2, pool part)",
        "installments": [{"afterPasses": a, "shareBp": b, "twoInstruments": c, "isFinal": d}
                         for a, b, c, d in doc["installments"]],
        "cureTermDays": doc["cureTerm"] // day,
        "cureTermMonths": round(doc["cureTerm"] // day / 30),
        "marsWindowDays": doc["marsWindowDays"],
        "maxCloudPct": doc["maxCloudBp"] / 100,
        "cloudRule": doc["cloudRule"],
        "instrumentFloorKgH": doc["instrumentFloorKgH"],
        "cleanWindowSeconds": doc["cleanWindow"],
        "publicationLagDays": doc["publicationLag"] // day,
    }


def verification() -> dict:
    """W2 (spec-v2 §9.1): manual protocol vs tool, author-measured, from scripts/measure_verification.py."""
    doc, prov = v2_file(DERIVED / "verification_time.json", "verification_time.json")
    return {**(doc or {}), "provenance": prov}


def detection_list() -> dict:
    """The missing-record tamper (spec-v2 §8.4): the DZA detection list as the observer's re-fetch receipt hashes it."""
    c = load(DERIVED / "cases" / "DZA_S_012.json")
    rows = [{"plumeId": p["plume_id"], "sceneDay": iso_day(p["scene_timestamp"]), "instrument": p["instrument"],
             "emissionKgH": round(p["emission_auto"]), "cmSource": p["source_name"]}
            for p in sorted(c["cm_plumes_within_radius"], key=lambda p: (p["scene_timestamp"], p["plume_id"]))
            if p.get("emission_auto") is not None]
    return {"source": "DZA_S_012", "rows": rows, "listSha256": evidence_hash(rows),
            "from": "data/derived/cases/DZA_S_012.json (cm_plumes_within_radius, quantified)"}


def clean_pass_demo(registered: dict[str, dict], covenant_id: str) -> dict:
    """Clean-pass tamper (spec-v2 §8.4): the pipeline's real observer bundle for DZA_S_012's first qualified scene
    (observer.cleanpass.bundle_for, offline), its CleanPass message (cleanpass.message, nonce / expiry of the step-0
    vector), signed by two Anvil test observers [SIM] in the vector's domain. Verified here before writing."""
    from observer import cleanpass

    vec = load(VECTOR)
    # EIP-55: viem refuses a mixed-case address whose checksum is wrong; the digest does not depend on the case
    domain = (vec["chainId"], to_checksum_address(vec["verifyingContract"]))
    b32f = {f["name"] for f in attest.POOL_TYPES["CleanPass"] if f["type"] == "bytes32"}
    as_msg = lambda d: {k: (attest.b32(v) if k in b32f else v) for k, v in d.items()}  # noqa: E731
    if "0x" + attest.pool_digest("CleanPass", as_msg(vec["cleanPass"]), *domain).hex() != vec["cleanPassDigest"]:
        fail("CleanPass digest drifted from pipeline/tests/vectors/cleanpass_v1.json")
    cp_doc, prov = v2_file(DERIVED / "cases" / "DZA_S_012.clean_passes.json", "DZA_S_012.clean_passes.json")
    if prov["mock"]:
        fail("the clean-pass tamper needs the real data/derived/cases/DZA_S_012.clean_passes.json")
    s = next(x for x in cp_doc["scenes"] if x["qualified"])
    vcp = vec["cleanPass"]
    bundle, jcs_bytes, scene_hash = cleanpass.bundle_for("DZA_S_012", s["sceneProviderId"], covenant_id=covenant_id,
                                                         claim_id=vcp["claimId"])
    if "0x" + hashlib.sha256(canonical_bytes(bundle)).hexdigest() != scene_hash or canonical_bytes(bundle) != jcs_bytes:
        fail("cleanpass.bundle_for: sceneHash is not SHA-256 of the bundle's RFC 8785 bytes")
    msg = cleanpass.message(bundle, nonce=vcp["nonce"], expiry=vcp["expiry"])
    entries = []
    for org in CLEAN_SIGNERS:
        key = anvil_key(ANVIL_INDEX[org])
        sig = attest.pool_sign("CleanPass", msg, key, *domain)
        signer = attest.pool_recover("CleanPass", msg, sig, *domain)
        pk, sk = pq.keypair(pq.seed_for(org, 31337))
        pq_sig = pq.sign(sk, jcs_bytes, deterministic=True)  # deterministic FIPS 204: make app writes the same bytes
        if not pq.verify(pk, jcs_bytes, pq_sig):
            fail(f"{org}: ML-DSA-44 signature over the scene bundle does not verify")
        if pq.pk_hash(pk) != (registered.get(org) or {}).get("pqPubKeyHash"):
            fail(f"{org}: ML-DSA-44 public key does not hash to the pqPubKeyHash the registry anchors")
        entries.append({"org": org, "signer": signer, "signature": "0x" + sig.hex(),
                        "mldsa44": {"publicKey": "0x" + pk.hex(), "context": pq.CONTEXT.decode(),
                                    "sig": "0x" + pq_sig.hex()}})
    check_signers(entries, {org: k["address"] for org, k in registered.items()})
    if len({e["signer"] for e in entries}) != len(CLEAN_SIGNERS):
        fail("clean-pass signers are not distinct")
    # the tamper button copies the cloud fields of the first scene the cloud rule refused (data, not a typed value)
    cloudy = next((x for x in cp_doc["scenes"] if x["refusal"] in ("CLOUD_FLAG", "CLOUD_FLAG_MISMATCH")), None)
    js = lambda m: {k: ("0x" + v.hex() if isinstance(v, bytes) else str(v) if isinstance(v, int)  # noqa: E731
                        and not isinstance(v, bool) else v) for k, v in m.items()}
    return {"domain": {"name": "MethaneClause", "version": "1", "chainId": domain[0], "verifyingContract": domain[1]},
            "message": js(msg), "bundle": bundle, "sceneHash": scene_hash, "entries": entries, "provenance": prov,
            "bundleFrom": "the observer's scene bundle",
            "cloudTamper": cloudy and {k: cloudy[k] for k in ("sceneAt", "instrument", "cloudCoverPct",
                                                             "cloudCoverPctAssessed", "notCloudy", "refusal")},
            "vector": {"message": js(vcp), "digest": vec["cleanPassDigest"], "path": str(VECTOR.relative_to(ROOT))}}


def resolve_scenes() -> None:
    """Keyless Carbon Mapper scene lookup for the ledger cards' detection scenes -> app/scene_ids.json (ids only)."""
    hyb = load(ROOT / "app" / "data" / "app.json")["hybrid"]["entries"][0]["bundle"]["plumeId"]
    wanted = []
    for name in ("TKM_S_276", "DZA_S_012"):
        leak, nxt, _ = ledger_plumes(name, hyb)
        has_clean = any((d / f"{name}.clean_passes.json").exists() for d in (DERIVED / "cases", MOCK_DIR))
        wanted += [v for v in (leak, nxt if has_clean else None) if v]
    out = {}
    for v in wanted:
        day = datetime.fromisoformat(v["sceneAt"][:10]).replace(tzinfo=timezone.utc)
        q = urllib.parse.urlencode({"intersects": json.dumps({"type": "Point", "coordinates": [v["lon"], v["lat"]]}),
                                    "datetime": f"{(day - timedelta(days=1)):%Y-%m-%dT%H:%M:%SZ}/"
                                                f"{(day + timedelta(days=2)):%Y-%m-%dT%H:%M:%SZ}", "limit": 20})
        url = f"{CM_API}/catalog/scenes/annotated?{q}"
        with urllib.request.urlopen(url, timeout=60) as r:
            items = json.load(r)
        items = items.get("items", items) if isinstance(items, dict) else items
        hit = next((i for i in items if i.get("name") == v["sceneName"]), None)
        if hit is None:
            sys.exit(f"scene {v['sceneName']} not returned by {url}")
        out[v["sceneName"]] = {"id": hit["id"], "timestamp": hit["timestamp"], "query": url}
    doc = {"schema": "methaneclause.app_scene_ids/1", "licence": "Data by Carbon Mapper® — scene identifiers only",
           "fetchedAt": datetime.now(timezone.utc).strftime("%Y-%m-%d"), "scenes": dict(sorted(out.items()))}
    SCENE_IDS.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"scene ids -> {SCENE_IDS.relative_to(ROOT)} ({len(out)} scenes)")


def case_view(name: str) -> dict:
    c = load(DERIVED / "cases" / f"{name}.json")
    ms = c["mars_source"]
    return {"source": name, "sourceId": c["sourceId"],
            "mars": {k: ms.get(k) for k in ("lat", "lon", "persistency_category", "persistency", "last_plume_date",
                                            "n_plumes_detected", "notified", "feedback_operator", "emission_status",
                                            "source_type", "sector", "country")},
            "marsPlumes": c["mars_plumes"],
            "cmPlumes": sorted(({k: p.get(k) for k in PLUME_KEYS} for p in c["cm_plumes_within_radius"]),
                               key=lambda p: p["scene_timestamp"]),
            "postMars": {k: c["post_mars_last_plume"][k] for k in ("since", "n_cm_detected", "n_cm_passes", "radius_km")}}


def abi_subset() -> dict:
    out = {}
    for name, fns in (("MethaneCovenant", {"getCovenant", "getEpisode", "episodeCount"}),
                      ("KeyRegistry", {"keyStatus"})):
        abi = load(ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json")["abi"]
        out[name] = [e for e in abi if e.get("name") in fns]
    return out


# ---------------------------------------------------------------------------------------------- README blocks (C5)
# README.md carries no hand-typed number of ours: every `<!-- gen:NAME:begin/end -->` block is rendered here from the
# committed files (app/data/app.json, data/derived/*). `--readme` writes them (and runs `make test` for the counts block);
# `--readme-check` exits 1 if any block other than the counts is stale (a lane test runs it in make check).
README = ROOT / "README.md"
DEVPOST = ROOT / "docs" / "devpost.md"  # the Devpost draft shares the README's generated blocks
PDF = ROOT / "docs" / "pdf" / "methaneclause.md"  # the submission PDF's source: README blocks + pdf_blocks()
SPEC_PUBLIC = ROOT / "docs" / "spec-public.md"  # the public specification: spec_blocks() (exported as docs/spec.md)
COUNTS_BLOCK = "counts"
README_ONLY = "readme:"  # a block key with this prefix replaces that block in README.md only (25 KB budget)
DEVPOST_ONLY = ("deploy", "stake", "loop", "cure", "projectionHeadline")  # Devpost carries these, README does not
# (README.md's first screen folds the loop into its status-0 line: README_ONLY "status0")
CHAIN_TABLE = "\n".join([  # the organizer's four-question blockchain test; README and Devpost share it
    "| Question | Answer |", "|---|---|",
    "| Multiple parties? | Seller, buyer, a sponsor, ≥ 2 observer organisations, open challengers. |",
    "| No trusted operator? | None exists for this function. Registries (MiQ, JOGMEC CLEAN, UNEP MARS) certify and "
    "notify but cannot move a seller's money; a bank can move money but cannot adjudicate satellite evidence; a court "
    "can do both and takes years. Seller and buyer are counterparties: neither accepts an operator chosen by the other, "
    "so the decision has to come from k independent observers plus an open challenge window — which is what the "
    "contract executes. |",
    "| Outside verification? | Anyone can re-fetch Carbon Mapper, recompute the canonical bundle hash and challenge an "
    "episode or a clean pass with an objective reason code. |",
    "| Fits the ledger? | Only hashes, IDs and money: `evidenceHash`, `sceneHash`, `policyHash`, a Merkle root of covered "
    "facilities, the bond, the pool. Raw data stays with the providers and the observers. |"])


def _without_selection(sentence: str) -> str:
    """Drop the backtest's selection parenthetical (named once, on the first Numbers bullet) from a later sentence;
    exit 1 if the sentence does not name it, so a reworded card sentence cannot slip through unchecked."""
    from backtest import SELECTION

    for form in (f" (of {SELECTION})", f", among {SELECTION}", f"; over {SELECTION}"):
        if form in sentence:
            return sentence.replace(form, "", 1)
    fail(f"backtest card sentence no longer names its selection: {sentence[:60]}…")


def _lower_first(sentence: str) -> str:
    """A catalogue sentence quoted after a colon starts lower-case ("A quiet run" -> "a quiet run"); an acronym stays."""
    return sentence[:1].lower() + sentence[1:] if sentence[1:2] == " " or sentence[1:2].islower() else sentence


def deploy_block(dep: dict) -> str:
    """The Devpost draft's Sepolia core addresses, read from contracts/deployments/11155111.json (never typed)."""
    if dep.get("chainId") != 11155111:
        fail("contracts/deployments/11155111.json is not an Ethereum Sepolia record")
    ex = "https://sepolia.etherscan.io/address/"
    renounced = int(dep["registryAdmin"], 16) == 0
    libs = dep.get("incentivePoolLibraries") or {}
    pool = (f", and `IncentivePool` [`{dep['incentivePool']}`]({ex}{dep['incentivePool']}#code) with its {len(libs)} "
            "linked libraries, checked against the chain by `contracts/script/check_pool_record.py`"
            if dep.get("incentivePool") else "")
    return (f"Live on Ethereum Sepolia (chainId {dep['chainId']}), Etherscan-verified: `MethaneCovenant` "
            f"[`{dep['methaneCovenant']}`]({ex}{dep['methaneCovenant']}#code), `KeyRegistry` "
            f"[`{dep['keyRegistry']}`]({ex}{dep['keyRegistry']}#code){' (admin renounced)' if renounced else ''}"
            f"{pool}.")


SECURITY = ROOT / "docs" / "SECURITY.md"
SLITHER = ROOT / "contracts" / "slither.md"
HALMOS = ROOT / "contracts" / "halmos" / "REPORT.md"


def assurance_block(dep: dict, security: str, slither: str, halmos: str) -> str:
    """README's one assurance line (IEEE A2), every figure read from a committed record: the Sepolia contracts in
    contracts/deployments/11155111.json (main and judge-sandbox core, both pools, the shared libraries), stated
    Etherscan-verified only if docs/SECURITY.md's deployment table says so for the core and the pool; the Slither
    counts from contracts/slither.md (make slither) and the Halmos results from contracts/halmos/REPORT.md
    (make halmos). Exit 1 if a record does not support the sentence."""
    import re

    if dep.get("chainId") != 11155111:
        fail("contracts/deployments/11155111.json is not an Ethereum Sepolia record")
    addrs = set()  # the live pairs and pools; `sandboxPrevious` (the replaced sandbox pair) is not counted or claimed
    for r in (dep, dep.get("sandbox") or {}):
        addrs |= {r[k] for k in ("methaneCovenant", "keyRegistry", "incentivePool") if r.get(k)}
        addrs |= set((r.get("incentivePoolLibraries") or {}).values())
    for names in ("`KeyRegistry`, `MethaneCovenant`", "`IncentivePool` and its five linked libraries"):
        if not re.search(rf"^\| {re.escape(names)}[^|]*\| \*\*live, Etherscan-verified\*\*", security, re.M):
            fail(f"docs/SECURITY.md does not record {names} as Etherscan-verified on Sepolia")
    counts = {k: int(v) for k, v in re.findall(r"^\| \**(High|Medium|Total)\** \| \**(\d+)\** \|$", slither, re.M)}
    verdicts = re.search(r"^Verdicts: (\d+) fixed, (\d+) false-positive, (\d+) accepted\.$", slither, re.M)
    hm = re.search(r"^High or Medium findings: (\d+), each with a verdict and its reason below; none left open\.$",
                   slither, re.M)
    if (set(counts) != {"High", "Medium", "Total"} or not verdicts or not hm
            or sum(int(x) for x in verdicts.groups()) != counts["Total"]
            or int(hm.group(1)) != counts["High"] + counts["Medium"]):
        fail("contracts/slither.md: no impact table, verdicts that do not sum to the total, or a High/Medium finding "
             "left open: run make slither")
    fixed, fp, acc = (int(x) for x in verdicts.groups())
    triage = ", ".join(f"{n} {w}" for n, w in ((fixed, "fixed"), (fp, "false positive"), (acc, "accepted")) if n)
    rows = re.findall(r"^\| [\w-]+ \| `((?:check|invariant)_\w+)` \| (\w+) \| (\w+) \|", halmos, re.M)
    proved = [n for n, got, want in rows if got == want == "PASS" and "_canary" not in n]
    if not proved or "all outcomes as expected" not in halmos or any(got != want for _, got, want in rows):
        fail("contracts/halmos/REPORT.md does not show every outcome as expected: run make halmos")
    ex = "https://sepolia.etherscan.io/address/"
    code = ", ".join(f"[`{n}`]({ex}{dep[k]}#code)" for n, k in (
        ("MethaneCovenant", "methaneCovenant"), ("IncentivePool", "incentivePool")))
    return (f"**Assurance.** The {len(addrs)} live Sepolia contracts are source-verified on Etherscan (e.g. {code}); "
            f"Slither: all {counts['Total']} findings triaged ({triage}), no High or Medium open "
            f"([`slither.md`](contracts/slither.md)); Halmos: {len(proved)} bounded symbolic checks pass "
            "([`REPORT`](contracts/halmos/REPORT.md)).")


def loop_block(run: dict) -> str:
    """The Devpost draft's closed Sepolia loop, read from data/derived/testnet_run.json: the episode CONFIRMED and the
    buyer's rebate withdrawn, each with its transaction; exit 1 if the record does not show both with status 1."""
    steps = {s["step"]: s for s in run["steps"]}
    fin, wd = steps.get("finalize"), steps.get("withdraw_buyer")
    if not fin or not wd or fin.get("status") != 1 or wd.get("status") != 1 or "amountWei" not in wd:
        fail("data/derived/testnet_run.json does not record a CONFIRMED episode and the buyer's withdrawal")
    pe = steps["proposeEpisode"]

    def tx(s: dict) -> str:
        h = s["tx"].rsplit("/", 1)[-1]
        return f"[`{h[:12]}…`]({s['tx']})"

    return (f"**The loop is closed on Ethereum Sepolia:** the k-of-n episode on {run['case']} (tier {pe['tier']}) was "
            f"finalized CONFIRMED after its appeal window ({tx(fin)}), and the buyer "
            f"[SIM] withdrew the {int(wd['amountWei']) / 1e18:g} ETH rebate from the seller's bond ({tx(wd)}).")


def _n(x) -> str:
    return f"{x:,}"


def _day(t: int) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def _pct_bp(bp: int) -> str:
    return f"{bp / 100:g} %"


def _signed(x: float) -> str:
    return f"{x:+}".replace("-", "−")


def _status_since(status: str, day: str) -> str:
    """"no attestable clean pass yet" + episode date -> "no attestable clean pass since the <day> episode yet"."""
    yet = status.endswith(" yet")
    return f"{status.removesuffix(' yet')} since the {day} episode{' yet' if yet else ''}"


def tkm_count_sentence(case: dict, bold: bool = False) -> str:
    """The lead case's headline count on the facility-radius basis (pipeline/cases.py facility_post): passes and
    detections after MARS's last plume over the linked Carbon Mapper sources within radius_km of the MARS point. The
    union over every linked source stays in the case file as post_mars_last_plume_all_linked (secondary)."""
    import datetime as dt

    pm, name = case["post_mars_last_plume"], case["mars_source"]["source_name"]
    if not pm.get("cm_sources") or "radius_km" not in pm:
        fail(f"cases/{name}.json has no facility-radius count: run make cases (OFFLINE=1 uses the cache)")
    d = dt.date.fromisoformat(pm["since"])
    s = "**" if bold else ""
    src = "source" if len(pm["cm_sources"]) == 1 else "sources"
    return (f"Carbon Mapper observed the {src} within {pm['radius_km']:g} km of {name} on {s}{pm['n_cm_passes']} "
            f"passes{s} since MARS's last plume ({d.day} {d:%b %Y}) and recorded a plume on {s}{pm['n_cm_detected']}{s}")


def spec_blocks(app: dict) -> dict[str, str]:
    """docs/spec-public.md's generated block: the lead case's problem statement and its count basis."""
    tkm = load(DERIVED / "cases" / "TKM_S_276.json")
    lead = app["hybrid"]["entries"][0]["bundle"]
    pm, al, src = tkm["post_mars_last_plume"], tkm["post_mars_last_plume_all_linked"], tkm["cm_sources"]
    far = sorted((v["dist_km"], n) for n, v in src.items() if n not in pm["cm_sources"])
    extra = sorted(set(al["detections"]) - set(pm["detections"]))
    union = (f" The union over all {len(al['cm_sources'])} linked Carbon Mapper sources ({al['n_cm_detected']} of "
             f"{al['n_cm_passes']}) stays in the case file as `post_mars_last_plume_all_linked`, labelled secondary: "
             + (f"it adds {', '.join(extra)}, a date on which only linked sources centred beyond {pm['radius_km']:g} km "
                f"({', '.join(f'{d:g}' for d, _ in far)} km from the MARS point) recorded a plume."
                if extra and far else "it adds no date."))
    return {"specProblem": (
        f"- **Problem:** two public datasets disagree about the same pipeline. UNEP IMEO MARS labels source TKM_S_276 "
        f"({tkm['mars_source']['country']}) *{tkm['mars_source']['persistency_category']}* — no plume in its own "
        f"six-month window. {tkm_count_sentence(tkm)}, including {_n(lead['emissionKgH'])} kg/h on "
        f"{lead['sceneTimestamp'][:10]} (`cases.TKM_S_276` and `lead` in `data/derived/audit_numbers.json`; counting "
        f"rule: `facility_post` in `pipeline/cases.py`).{union}")}


def readme_blocks(app: dict) -> dict[str, str]:
    import policy as policy_mod

    audit = load(DERIVED / "audit_numbers.json")["numbers"]
    j = audit["join_v1"]
    tkm, dza = load(DERIVED / "cases" / "TKM_S_276.json"), load(DERIVED / "cases" / "DZA_S_012.json")
    tkm_cp = load(DERIVED / "cases" / "TKM_S_276.clean_passes.json")
    dza_cp = load(DERIVED / "cases" / "DZA_S_012.clean_passes.json")
    lead = app["hybrid"]["entries"][0]["bundle"]
    run = load(DERIVED / "testnet_run.json")
    tamper = next(x for x in run["steps"] if x["step"] == "reject_tamper")
    pool = policy_mod.POLICY_V2["pool"]
    floors = {k: v["kgH"] for k, v in load(DERIVED / "floors.json")["instrumentFloorKgH"].items()}
    bt = load(DERIVED / "backtest.json")["card"]
    cr = load(DERIVED / "cleanruns.json")["card"]["sentence"]
    pf = load(DERIVED / "model" / "bond_sizing_v3.json")["card"]["portfolioSentence"]
    rp, ex, port = app["replay"], app["exports"]["verdict"], app["portfolio"]
    va, vb = rp["variants"]["A"]["outcome"], rp["variants"]["B"]["outcome"]
    demo = load(DERIVED / "demo_run.json")
    m = audit["model"]
    fp = load(DERIVED / "model" / "model_metrics.json")["footprint"]
    inst = pool["installments"]
    reach = final_reach(policy_mod.POLICY_V2)
    replay_reach = final_reach(policy_mod.POLICY_V2_REPLAY)
    if replay_reach["lastDetectionDay"] is None:
        fail("POLICY_V2_REPLAY: the final installment is out of term for every episode; the README sentence needs it")
    from backtest import SELECTION

    b = {}
    b["status0"] = (f"**A rejection you can open in an explorer:** the witnesses' signed bundle for the "
                    f"{_n(lead['emissionKgH'])} kg/h plume, its rate edited and the signatures kept, sent to Ethereum "
                    f"Sepolia — [`{tamper['tx'].rsplit('/', 1)[-1][:12]}…`]({tamper['tx']}): receipt status 0, "
                    f"`{tamper['reason']}`; the bond did not move.")
    # README first screen: one proof line, success first (the closed loop), then the refused tamper
    steps = {s["step"]: s for s in run["steps"]}
    loop_block(run)  # fails closed unless the record shows CONFIRMED and the buyer's withdrawal with status 1

    def _tx(url: str) -> str:
        return f"[`{url.rsplit('/', 1)[-1][:8]}…`]({url})"

    b[README_ONLY + "status0"] = (
        "**Runs on Ethereum Sepolia (testnet):** a confirmed leak paid the buyer [SIM] (simulated party) a "
        f"{int(steps['withdraw_buyer']['amountWei']) / 1e18:g} ETH rebate from the seller's bond; the same signed "
        "record with its leak rate edited was refused and the bond did not move. Transactions: confirmed after the "
        f"appeal window {_tx(steps['finalize']['tx'])}, rebate withdrawn {_tx(steps['withdraw_buyer']['tx'])}, "
        f"refused {_tx(tamper['tx'])} (receipt status 0, `{tamper['reason']}`).")
    pm = tkm["post_mars_last_plume"]
    b["problem"] = (f"- UNEP MARS labels source **TKM_S_276** (Turkmenistan) *{tkm['mars_source']['persistency_category']}*."
                    f" {tkm_count_sentence(tkm, bold=True)}, including **{_n(lead['emissionKgH'])} kg/h on "
                    f"{lead['sceneTimestamp'][:10]}**.\n"
                    f"- Across the frozen join of the two datasets, **{_n(j['n_candidates'])} of "
                    f"{_n(j['n_mars_target_sources'])}** MARS *absent* oil and gas sources had a Carbon Mapper plume within "
                    f"{j['radius_km']:g} km inside MARS's own window ({j['window']}) "
                    f"([`pipeline/join/CRITERION.md`](pipeline/join/CRITERION.md)).")
    # the first screen: why now (the problem's two numbers + the stake's low bound) and why each side signs (the EU
    # date and article from docs/clause/clause_sources.json, the same row the PDF's pilot block cites)
    sb = load(DERIVED / "backtest.json")["stake_band"]
    w0, w1 = (datetime.fromisoformat(d) for d in j["window"].split(".."))
    months = (w1.year - w0.year) * 12 + w1.month - w0.month
    if w0.day != w1.day or months < 1:
        fail(f"join window {j['window']} is not a whole number of months: reword whyNow")
    words = {3: "three", 6: "six", 12: "twelve"}
    stake_floor = math.floor(sb["kt_ch4_per_yr"]["low"] / 100) / 10  # "at least": round down, never up
    b["whyNow"] = (f"**Why now.** Carbon Mapper saw a methane plume within {j['radius_km']:g} km of "
                   f"{_n(j['n_candidates'])} oil and gas sources that the UN's alert system (UNEP MARS) had listed as "
                   f"quiet (*absent*: no MARS plume in {words.get(months, str(months))} months), out of "
                   f"{_n(j['n_mars_target_sources'])} such sources ([`CRITERION`](pipeline/join/CRITERION.md)). "
                   f"Across them the stake is at least {stake_floor:g} Mt CH₄/yr (stake, not abatement).")
    core = policy_mod.POLICY_V2["core"]
    b["paths"] = "\n".join([
        "- **Who.** The seller posts a bond for named sources; the buyer, an EU gas importer, gets the rebate; "
        "witnesses (separate organisations) fetch and sign the public record; the sponsor (a lender, as a "
        "sustainability-linked margin step, or the buyer, to keep a clean supplier) funds the earn-back pool.",
        f"- **Seen → confirmed → paid.** {core['k']} of {core['n']} witnesses (the clause's quorum k of n) sign the "
        "same detection; after the appeal window the buyer withdraws the rebate.",
        "- **Clean passes → earn-back in steps.** k witnesses sign each clean pass; the sponsor's pool pays in steps.",
        "- **New leak → the earn-back stops.** The next confirmed leak cancels every unpaid step."])
    # README (first screen carries the join numbers): its problem section keeps the TKM_S_276 bullet only
    b[README_ONLY + "problem"] = b["problem"].split("\n")[0]
    if not b[README_ONLY + "problem"].startswith("- UNEP MARS labels source"):
        fail("problem block: the README's TKM_S_276 bullet is no longer the first line")
    eu = {e["id"]: e for e in load(ROOT / "docs" / "clause" / "clause_sources.json")["euDates"]}
    a28, scope = eu["art28Applies"], eu["art28Scope"]
    if "producer-level MRV equivalent to the EU's" not in a28["what"] or "concluded or renewed" not in scope["what"]:
        fail("docs/clause/clause_sources.json art28Applies/art28Scope changed their wording: reword whySign")
    b["whySign"] = (f"**Why each side signs.** The buyer, an EU gas importer, must show from {a28['date']} that gas "
                    f"bought under contracts concluded or renewed since {scope['date']} is under producer-level "
                    "monitoring, reporting and verification equivalent to the EU's "
                    f"([Regulation (EU) 2024/1787, Art. 28]({a28['url']})); this clause gives it an executable remedy. "
                    "The seller keeps that buyer, shows a bonded commitment that buyers and lenders can check, and earns "
                    "part of the rebate back with clean passes.")
    cure_days = pool["cureTerm"] // 86400
    cure_full = f"last needs two instruments, a MARS corroboration from other satellites and a cure term of {cure_days} days."
    if reach["lastDetectionDay"] is None:
        reach_full = (f" The final installment fits a multi-year supply contract: it needs {reach['minTermDays']} days "
                      "between the episode and the end of the covenant term (link window, cure term and relapse grace, "
                      "less the publication lag). Under the one-year prototype policy (`POLICY_V2`, on Sepolia) it is "
                      f"never scheduled (`FinalOutOfTerm`), so a refund there stops at "
                      f"{_pct_bp(reach['nonFinalShareBp'])}; the "
                      f"{policy_mod.POLICY_V2_REPLAY['core']['term'] // 86400 // 365}-year DZA replay reaches it for "
                      f"episodes in its first {replay_reach['lastDetectionDay']} days.")
        # README (25 KB budget): the same fact folded into the cure-term clause
        cure_short = (f"last needs two instruments, a MARS corroboration and a {cure_days}-day cure term within the "
                      f"covenant term (one-year Sepolia policy: ≤ {_pct_bp(reach['nonFinalShareBp'])}).")
        my_path = DERIVED / "multiyear_run.json"
        if my_path.exists():  # the three-year covenant on Sepolia (observer/testnet.py multiyear, make verify-evidence)
            my = load(my_path)
            my_reach = final_reach(policy_mod.POLICY_V2_MULTIYEAR)
            if my["finalReach"] != my_reach or {s["step"] for s in my["steps"] if s["status"] == 1} != {"createCovenant", "fund"}:
                fail("multiyear_run.json does not match POLICY_V2_MULTIYEAR or lacks a mined step: re-run make testnet-multiyear")
            years = policy_mod.MULTIYEAR_TERM // 86400 // 365
            last = datetime.fromtimestamp(my["lastFinalDetection"], timezone.utc).date().isoformat()
            fund_tx = next(s["tx"] for s in my["steps"] if s["step"] == "fund")
            reach_full += (f" A second Sepolia covenant over the same sources runs the {years}-year policy "
                           f"(`POLICY_V2_MULTIYEAR`, covenant {int(my['covenantId'], 16)}, the main pool opened for it "
                           f"[`{fund_tx.rsplit('/', 1)[-1][:8]}…`]({fund_tx})): there the final installment is reachable "
                           f"for every episode up to day {my_reach['lastDetectionDay']} ({last}).")
            cure_short = cure_short[:-2] + (f"; {years}-year Sepolia covenant {int(my['covenantId'], 16)}: final reachable "
                                            f"for episodes to {last}).")
    else:
        fail("POLICY_V2 now reaches the final installment: rewrite the mechanism block's term sentence")
    fee = " Each counted pass pays an observer fee from the sponsor's pool, split among the signing organisations."
    head_m = (
        "- **Refund (the IncentivePool).** A confirmed episode opens a claim. Qualified clean passes — Carbon Mapper's "
        "`not_cloudy` flag, the scene footprint over the source's plume origins, no plume on the scene, no UNEP MARS "
        f"plume within ±{pool['marsWindowDays']} days, the instrument's detection floor at most the episode's rate — "
        "are attested by k observer organisations, wait a clean window open to challenge, and count. At "
        f"{', '.join(str(r[0]) for r in inst[:-1])} and {inst[-1][0]} counted passes, installments of "
        f"{', '.join(_pct_bp(r[1]) for r in inst[:-1])} and {_pct_bp(inst[-1][1])} of the rebate are scheduled; the ")
    tail_m = (" The refund never exceeds the rebate the episode cost, is frozen while a new detection is under review, "
              "and any later attested detection cancels what is still unpaid.")
    b["mechanism"] = head_m + cure_full + tail_m + reach_full + fee
    # README (25 KB budget, no slack): the same facts, tighter; the PDF keeps the full sentences
    readme_mechanism = (head_m.replace(", wait a clean window open to challenge, and count.",
                                       " and count after a clean window open to challenge.")
                        .replace("passes, installments of ", "passes, ")
                        + cure_short
                        + " The refund never exceeds the episode's rebate, freezes while a new detection is reviewed, and "
                          "a later attested detection cancels what is unpaid.")
    if readme_mechanism.count("count after a clean window") != 1 or "passes, installments of" in readme_mechanism:
        fail("mechanism block: the README wording no longer matches its source sentence")
    tkm_ep = tkm_cp["episode"]["detectedAtIso"][:10]
    look = (f"With today's origin points [SIM look-ahead] {vb['counted']} passes would have been counted, the MARS "
            "corroboration would have scheduled the final installment, and the relapse would have cancelled it while "
            "pending. ")
    head = (f"On **DZA_S_012** (Algeria) the replay counts **{dza_cp['summary']['statusLabel']}** after the "
            f"{dza_cp['episode']['detectedAtIso'][:10]} episode; the relapse on "
            f"{dza_cp['summary']['firstPlumeAfterSince']['sceneAtIso'][:10]} cancels the claim: paid "
            f"{_pct_bp(va['paidBp'])}, forfeited {_pct_bp(va['forfeitedBp'])}. ")
    tail = (f"On **TKM_S_276**, Carbon Mapper saw a plume on {pm['n_cm_detected']} of {pm['n_cm_passes']} passes within "
            f"{pm['radius_km']:g} km since {pm['since']}: {_status_since(tkm_cp['summary']['statusLabel'], tkm_ep)}. "
            "Why the refund is capped: ")
    b["refund"] = head + look + tail + _lower_first(cr)
    if " That is why" not in cr:
        fail("data/derived/cleanruns.json card sentence no longer ends with its 'That is why' clause")
    # README (25 KB budget): its problem bullet already gives TKM's facility-radius count, so the refund line skips it
    short_tail = (f"On **TKM_S_276**: {_status_since(tkm_cp['summary']['statusLabel'], tkm_ep)}. "
                  "Why the refund is capped: ")
    b[README_ONLY + "refund"] = head + short_tail + _lower_first(cr.split(" That is why")[0])
    rej = {t: x for t, x in rp["variants"]["A"]["txs"].items() if x and x[0]["status"] == 0}
    b["demo"] = "\n".join([
        "| Run | What happens on chain | Checks |", "|---|---|---|",
        f"| **DZA replay**, own chain from {_day(rp['clock']['genesis'])} [SIM clock] | covenant via a SuretyBond (operator's "
        f"withdraw fails `NotFunder`); the {dza_cp['episode']['detectedAtIso'][:10]} episode CONFIRMED; real passes "
        "counted, installments 1–2 released; MARS corroboration; a seeded [SIM] bad pass voided (`CLOUD_FLAG`); while "
        "the relapse is PROPOSED, cancel and release fail `SourceUnderReview`; then `relapse()` cancels A (paid "
        f"{_pct_bp(va['paidBp'])}) and B [SIM look-ahead] (pending final voided); refunds pulled to the surety | "
        f"{rp['checks']} checks, {rp['txs']} txs, {rp['status0']} status 0 |",
        f"| **v1**, TKM_S_276 | the {lead['sceneTimestamp'][:10]} plume → CONFIRMED → rebate withdrawn; `InvalidSignature`, "
        "`SelfAttestation`, `AttestationMismatch`, `NotCovered`, `Replay`, `DuplicateEvidence`, `RevokedKey`; `LINKED` | "
        f"{demo['checks']} checks |",
        "| **TKM verdict-only** [SIM importer] | no bond; the pool refuses ETH (`BadParams`); CONFIRMED is the verdict; "
        f"Annex C hash read back; credit note round-trips | {ex['checks']} checks |",
        f"| **Portfolio**, set v3 | one covenant, {port['n']} facilities; a wrong-set proof fails `NotCovered`; an "
        f"episode proven by its {port['proofSteps']}-step path → CONFIRMED → claim | {port['checks']} checks |",
        "",
        "A fresh clone skips the DZA observer's Re-check step: no raw cache (`LIVE=1` or `METHANECLAUSE_RAW` enables "
        "it)."])
    b["numbers"] = "\n".join([
        f"- **AI vs the rule.** A gradient-boosted next-pass forecast measured against the frozen persistence rule on "
        f"region- and time-held-out sets: BSS {_signed(m['test_a']['model']['bss_vs_B1'])} and "
        f"**{_signed(m['test_b']['model']['bss_vs_B1'])}** → *the persistence rule is enough* "
        f"([`MODEL_CARD`](pipeline/model/MODEL_CARD.md)); held out, the rule called zero on passes that still showed a "
        f"plume {' / '.join(f'{100 * m[t]['B1_persistence']['false_zero_rate']:.1f} %' for t in ('test_a', 'test_b'))} of "
        "the time (the reason it is smoothed).",
        f"- **Bond of the v1 covenant:** {audit['bond_sizing']['recommended_bond_eth']} ETH, the P90 of simulated "
        "annual rebate exposure under the smoothed rule.",
        f"- **Backtest** over {SELECTION}:",
        f"  - **Episodes.** {_without_selection(bt['episodesSentence'])}",
        f"  - **Independent satellites.** {_without_selection(bt['l2Sentence'])}",
        f"  - **Refunds.** {_without_selection(bt['refundsSentence'])}",
        f"  - **Stake.** {_without_selection(bt['stakeSentence'])}",
        f"- {pf}",
        f"- {load(DERIVED / 'projection.json')['card']['headlineSentence']} Method and output: "
        "[`pipeline/projection.py`](pipeline/projection.py), [`data/derived/projection.json`](data/derived/projection.json).",
        f"- **Footprint.** The model that lost to the rule: {fp['train_plus_inference_kgCO2e']} kg CO₂e; chain side: "
        "[`FOOTPRINT`](docs/FOOTPRINT.md)."])
    # the bond-sizing bullets (v1 covenant, portfolio) moved to docs/SCALING.md's scToday and scPays; README.md keeps
    # the demo's portfolio row (25 KB budget)
    bond_line = next(x for x in b["numbers"].splitlines() if x.startswith("- **Bond of the v1 covenant:**"))
    b[README_ONLY + "numbers"] = b["numbers"].replace(f"\n- {pf}", "", 1).replace("\n" + bond_line, "", 1)
    if f"- {pf}" in b[README_ONLY + "numbers"] or bond_line in b[README_ONLY + "numbers"]:
        fail("README numbers block still carries a bond-sizing bullet")
    # the footprint bullet stays in the PDF's footprint block; README.md links FOOTPRINT in its header (25 KB budget)
    fp_line = next(x for x in b["numbers"].splitlines() if x.startswith("- **Footprint.**"))
    b[README_ONLY + "numbers"] = b[README_ONLY + "numbers"].replace("\n" + fp_line, "")
    b[README_ONLY + "mechanism"] = readme_mechanism  # no observer-fee or term sentence (25 KB budget; both in the PDF)
    b["chain"] = CHAIN_TABLE
    b["stake"] = bt["stakeSentence"]
    b["deploy"] = deploy_block(load(ROOT / "contracts" / "deployments" / "11155111.json"))
    b["assurance"] = assurance_block(load(ROOT / "contracts" / "deployments" / "11155111.json"), SECURITY.read_text(),
                                     SLITHER.read_text(), HALMOS.read_text())
    b["loop"] = loop_block(run)
    # the refund side on Sepolia, run by a judge test wallet in the judge sandbox [SIM] (pipeline/cure_arc.py)
    b["cure"] = cure_arc.loop_sentence(cure_arc.arc(), policy_mod.POLICY_V2_SANDBOX["pool"]["installments"][0][0])
    b["projectionHeadline"] = load(DERIVED / "projection.json")["card"]["headlineSentence"]
    v = load(DERIVED / "verification_time.json")
    t, mt = v["tool"], v["manual"]
    b["open"] = "\n".join([
        f"| Verification time by hand | {'n = ' + str(mt['n']) if mt['n'] else 'the manual protocol is not timed yet'} | "
        f"the tool path, author-measured, not a user study: median {t['medianSeconds']} s over n = {t['n']} "
        f"({t.get('mode', 'snapshot')}) |"])
    b["floors"] = (f"- **Floor blindness, both ways.** A source just below an instrument's detection floor (Tanager "
                   f"{_n(floors['tan'])} kg/h, EMIT {_n(floors['emi'])} kg/h, the conservative end of the published "
                   "ranges) is invisible to the rebate and to the refund alike.")
    return b


def _render_counts() -> str:
    """The test counts a judge sees in a fresh clone of the public repository: the tracked files the export manifest
    selects are copied (scripts/check_public.py --copy-to) next to an empty raw cache (data/raw/ is never committed)
    and the pipeline and page/builder tests run there; forge runs here (the contracts are exported unchanged, and
    `make check` builds them first, so their build output is copied along). Where the export tooling is absent (the
    public repository itself), the counts are this checkout's and say so."""
    import os
    import re
    import subprocess
    import tempfile

    py_bin = str(ROOT / "pipeline" / ".venv" / "bin" / "python")

    def last(cmd: list[str], cwd: Path, env: dict) -> str:
        out = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True).stdout.strip().splitlines()
        return re.sub(r" in [\d.]+s.*", "", (out[-1] if out else "no output").strip("= "))

    with tempfile.TemporaryDirectory(prefix="methaneclause-counts-") as tmp:
        no_raw = Path(tmp) / "no-raw"
        no_raw.mkdir()
        env = {**os.environ, "PATH": f"{Path.home()}/.foundry/bin:" + os.environ["PATH"], "METHANECLAUSE_RAW": str(no_raw)}
        where, label = ROOT, "in this checkout"
        if (ROOT / "scripts" / "check_public.py").exists():
            pub = Path(tmp) / "public"
            subprocess.run(["python3", "-I", "scripts/check_public.py", f"--copy-to={pub}"], cwd=ROOT, check=True,
                           capture_output=True)
            if (ROOT / "contracts" / "out").is_dir():
                shutil.copytree(ROOT / "contracts" / "out", pub / "contracts" / "out")
            where, label = pub, "in a fresh clone of the public export"
        py_n = last([py_bin, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"], where / "pipeline", env)
        lc_n = last([py_bin, "-m", "pytest", "-q", "-p", "no:cacheprovider", "scripts/tests"], where, env)
        fg = subprocess.run(["forge", "test"], cwd=ROOT / "contracts", env=env, capture_output=True, text=True).stdout
    fg_line = next(x for x in fg.splitlines() if x.startswith("Ran ") and "tests passed" in x)
    fg_n = re.search(r"(\d+) tests passed, (\d+) failed", fg_line).group(0)
    if "failed" in py_n or "failed" in lc_n or "error" in py_n or "error" in lc_n:
        fail(f"tests fail {label} (pipeline: {py_n}; page and builder: {lc_n}): fix them before rendering the counts")
    return "\n".join(["```sh", "make setup   # Python venv with pinned deps",
                      f"make test    # {label}: pipeline {py_n}; contracts (forge) {fg_n}",
                      "make demo    # the four runs in the table above",
                      f"make check   # + judge-local, the strict audit, wording, links, secrets; page and builder tests: {lc_n}",
                      "```"])


def pdf_blocks(app: dict) -> dict[str, str]:
    """Blocks only the PDF draft carries (docs/pdf/methaneclause.md), each read from a committed file: the observers'
    refusal rate, the stake's method and GWP note, the running footprint, and the cost / scale figures."""
    adv = load(DERIVED / "adversarial.json")
    layers = adv["by_layer"]
    if adv["caught"] != sum(v["caught"] for v in layers.values()) or adv["n"] != sum(v["n"] for v in layers.values()):
        fail("data/derived/adversarial.json: the layer counts do not add up to the totals")
    sb = load(DERIVED / "backtest.json")["stake_band"]
    gwp = load(DERIVED / "floors.json")["gwp"]
    co2 = sb["co2e"]
    for key, g in (("gwp100", "gwp100_fossil_ch4"), ("gwp20", "gwp20_fossil_ch4")):
        if co2[key]["factor"] != gwp[g]["value"] or co2[key]["kt_co2e_per_yr"]["low"] != round(
                sb["kt_ch4_per_yr"]["low"] * gwp[g]["value"], 1):
            fail(f"data/derived/backtest.json: the {key} conversion of the stake does not match pipeline/floors.py")
    meas = load(DERIVED / "measurements.json")
    fn, lat = meas["functions"], meas["latency_seconds"]
    fp = load(DERIVED / "model" / "model_metrics.json")["footprint"]
    port = app["portfolio"]
    pf = load(DERIVED / "model" / "bond_sizing_v3.json")["card"]["portfolioSentence"]
    if port.get("sentence") != pf:
        fail("app/data/app.json portfolio sentence differs from data/derived/model/bond_sizing_v3.json: run make app")

    def mt(kt: float) -> str:
        return f"~{kt / 1000:.1f} Mt"

    def status(g: dict) -> str:
        said = {"read": "read from the source", "read-secondary": "read through a secondary source"}
        if g["status"] not in said:
            fail(f"pipeline/floors.py: GWP status {g['status']!r} has no sentence")
        return said[g["status"]]

    g100, g20 = gwp["gwp100_fossil_ch4"], gwp["gwp20_fossil_ch4"]
    b = {}
    b["refusals"] = (
        f"Observers refuse bad input before signing; a refused input is never signed. On our own adversarial set "
        f"([`data/derived/adversarial.json`](data/derived/adversarial.json)) {adv['caught']} of {adv['n']} cases were "
        f"caught ({', '.join(f'{k} {v['caught']}/{v['n']}' for k, v in layers.items())}): {adv['scope']}.")
    b["gwp"] = "\n".join([
        f"- **Method** ([`data/derived/backtest.json`](data/derived/backtest.json), `stake_band`): {sb['method']}; "
        f"{sb['taskingBias']}. Only the low bound is quoted.",
        f"- **GWP.** In CO₂e the same low bound is {mt(co2['gwp100']['kt_co2e_per_yr']['low'])} CO₂e/yr at GWP-100 "
        f"(fossil CH₄ {g100['value']}, IPCC AR6 as tabled by the GHG Protocol, {status(g100)}) and "
        f"{mt(co2['gwp20']['kt_co2e_per_yr']['low'])} CO₂e/yr at GWP-20 (fossil CH₄ {g20['value']}, IPCC AR6, "
        f"{status(g20)}; [`pipeline/floors.py`](pipeline/floors.py)). The mass of CH₄ is the quoted unit because the "
        f"horizon alone changes the CO₂e figure by ×{g20['value'] / g100['value']:.1f}; GWP-20 is in no hashed "
        "policy."])
    b["footprint"] = (
        f"Running this prototype: the model that lost to the rule cost {fp['train_plus_inference_kgCO2e']} kg CO₂e to "
        f"train and score ({fp['tool']}); the chain is proof of stake (Ethereum Sepolia and a local chain; PoW: no), "
        f"with {meas['transactions']['logged']} Sepolia transactions logged ([`docs/FOOTPRINT.md`](docs/FOOTPRINT.md)).")
    pe, cc = fn["proposeEpisode"], fn["createCovenant"]
    b["scale"] = "\n".join([
        f"- **One covenant, many facilities.** {pf} In `make demo` the portfolio covenant's episode is proven by its "
        f"{port['proofSteps']}-step Merkle path, and a proof against the wrong set fails `NotCovered`.",
        f"- **Cost per call** ([`data/derived/measurements.json`](data/derived/measurements.json)). On Ethereum Sepolia "
        f"`proposeEpisode` took a median {_n(pe['gas_sepolia_median'])} gas (n = {pe['n']}) and `createCovenant` "
        f"{_n(cc['gas_sepolia_median'])} (n = {cc['n']}); under the Cancun schedule Foundry simulates locally they take "
        f"about {_n(pe['gas_anvil_cancun_rounded_1k'])} and {_n(cc['gas_anvil_cancun_rounded_1k'])}. Sepolia currently "
        "prices new state far above that schedule, so the Sepolia figures are the upper line.",
        f"- **Data on chain.** A canonical evidence bundle is {_n(meas['bundle_bytes_jcs'])} bytes off chain; the chain "
        "stores only its SHA-256 hash.",
        f"- **Latency.** Send to receipt through a public RPC on Sepolia: p50 {lat['p50']} s, p90 {lat['p90']} s "
        f"(n = {lat['n']}{', raw samples not kept' if 'raw samples' in lat.get('label', '') else ''}).",
        f"- {load(DERIVED / 'scale.json')['sentence']}"])
    b.update(projection_blocks())
    import clause  # adoption: precedents and pilot, from docs/clause/clause_sources.json + the committed policy

    b.update(clause.pdf_blocks())
    return b


# Crypto Carbon Ratings Institute, "The Merge - Implications on the Electricity Consumption and Carbon Footprint of the
# Ethereum Network", September 2022, Table 18 ("Wh/tx per network"): a secondary mainnet average, quoted verbatim in
# docs/FOOTPRINT.md (scripts/tests/test_build_app_data.py checks that the quote and this value agree).
CCRI_WH_PER_TX = 6.2943
FOOTPRINT = ROOT / "docs" / "FOOTPRINT.md"
# The gas table's row order: protocol calls, the pool, then deployment and setup; any other function follows sorted.
FP_ORDER = ["createCovenant", "proposeEpisode", "proposeEpisode_reverted", "challenge", "submitResolution", "finalize",
            "withdraw", "fund", "pool_fund", "deploy_IncentivePool", "deploy_PoolVerify", "deploy_PoolSetup",
            "deploy_PoolEpisodes", "deploy_PoolCorroboration", "deploy_PoolInstallments", "deploy_MethaneCovenant",
            "deploy_KeyRegistry", "deploy_registerKey", "deploy_setOrgController", "deploy_renounceAdmin", "registerKey",
            "transfer_new_account", "transfer"]
FP_LABEL = {"proposeEpisode_reverted": "`proposeEpisode`, rejected (status 0)"}
POOL_CALLS = ("openClaim", "attestCleanPass", "countPass", "release", "relapse", "attestCorroboration")


def footprint_blocks() -> dict[str, str]:
    """docs/FOOTPRINT.md's figures, read from data/derived/measurements.json and model_metrics.json: the compute row,
    the Sepolia transaction counts, the full gas table, the per-transaction energy illustration and what of the pool is
    measured on Sepolia."""
    meas = load(DERIVED / "measurements.json")
    tx, fn = meas["transactions"], meas["functions"]
    fp = load(DERIVED / "model" / "model_metrics.json")["footprint"]
    if not tx["logged"] or not fn:
        fail("data/derived/measurements.json has no logged transactions or no gas figures")
    tool, _, rest = fp["tool"].partition(" ")
    b = {}
    b["fpCompute"] = "\n".join([
        "| What | Tool | Result | Source |", "|---|---|---|---|",
        f"| Model training + inference (the boosted model that lost to the rule) | {tool.replace('codecarbon', 'CodeCarbon')} "
        f"{rest} | {fp['train_plus_inference_kgCO2e']} kg CO₂e | `footprint` in "
        "[`data/derived/model/model_metrics.json`](../data/derived/model/model_metrics.json) |",
        "| Backtest, clean-run statistics, observer runs | not measured | — | no CodeCarbon run exists for these scripts "
        "yet; nothing is claimed |"])
    fails = "s" if tx["unexpected_failures"] != 1 else ""
    b["fpTransactions"] = (
        f"- **Transactions on Sepolia:** {tx['logged']} logged, every transaction our addresses sent, rebuilt from the "
        f"chain; {tx['expected_reverts']} were expected rejections (status 0, shown on purpose) and "
        f"{tx['unexpected_failures']} were unexpected failure{fails} with their causes recorded (`transactions` in "
        "[`data/derived/measurements.json`](../data/derived/measurements.json); the list is "
        "[`data/derived/sepolia_txs.json`](../data/derived/sepolia_txs.json)).")
    rows = ["| Function | Gas, Sepolia median | Gas, Anvil (rounded to 1k) | n |", "|---|---|---|---|"]
    for f in [f for f in FP_ORDER if f in fn] + sorted(set(fn) - set(FP_ORDER)):
        v = fn[f]
        anvil = _n(v["gas_anvil_cancun_rounded_1k"]) if v.get("gas_anvil_cancun_rounded_1k") else "—"
        rows.append(f"| {FP_LABEL.get(f, f'`{f}`')} | {_n(v['gas_sepolia_median'])} | {anvil} | {v['n']} |")
    b["fpGas"] = "\n".join(rows)
    b["fpScale"] = "- " + load(DERIVED / "scale.json")["sentence"].replace("](data/", "](../data/")
    wh = tx["logged"] * CCRI_WH_PER_TX
    b["fpEnergy"] = (f"- **Scale only.** Allocated per transaction, the {tx['logged']} Sepolia transactions above "
                     f"correspond to {tx['logged']} × "
                     f"{CCRI_WH_PER_TX} Wh ≈ {wh:,.0f} Wh — an illustration of scale, not a measurement.")
    measured = [f for f in FP_ORDER + sorted(fn) if f in fn and (f == "deploy_IncentivePool" or f.startswith("pool_"))]
    measured = list(dict.fromkeys(measured))
    local = [c for c in POOL_CALLS if f"pool_{c}" not in fn]
    said = ", ".join(f"`{f}` (n = {fn[f]['n']})" for f in measured) or "nothing of the pool"
    b["fpPool"] = (f"- The pool's transactions measured on Sepolia: {said}. "
                   + (f"Its other calls ({', '.join(f'`{c}`' for c in local)}) run on Anvil in `make demo` and are not "
                      "yet in `data/derived/measurements.json`." if local else ""))
    return b


SCALING = ROOT / "docs" / "SCALING.md"
SCALING_CALLS = ("createCovenant", "proposeEpisode", "finalize", "withdraw", "pool_openClaim", "pool_attestCleanPass",
                 "pool_countPass")


def _relink(text: str) -> str:
    """A repo-relative Markdown link as seen from docs/ ("](data/x)" -> "](../data/x)"); web links stay."""
    import re

    return re.sub(r"\]\((?!https?:|#|\.\./)", "](../", text)


def _eth(wei) -> str:
    """Wei as plain decimal ETH (0.00001 ETH, never 1e-05)."""
    from decimal import Decimal

    return f"{(Decimal(int(wei)) / Decimal(10**18)).normalize():f} ETH"


def scaling_blocks(app: dict) -> dict[str, str]:
    """docs/SCALING.md's figures (IEEE A6), read from committed files: what runs today (portfolio, bundle size,
    latency), gas against facility-set size (scale.json), the Sepolia cost per call (measurements.json), who pays
    (the committed policy, the deployment record, projection.json's ledger-cost sentence), the ledger's own footprint,
    and the proposed pilot (pipeline/clause.py)."""
    import clause
    import policy as policy_mod

    meas, scale = load(DERIVED / "measurements.json"), load(DERIVED / "scale.json")
    fn, lat = meas["functions"], meas["latency_seconds"]
    missing = [f for f in SCALING_CALLS if f not in fn]
    if missing:
        fail(f"data/derived/measurements.json has no Sepolia gas for {', '.join(missing)}")
    port = app["portfolio"]
    bsz = load(DERIVED / "model" / "bond_sizing_v3.json")
    pf = bsz["card"]["portfolioSentence"]
    if port.get("sentence") != pf:
        fail("app/data/app.json portfolio sentence differs from data/derived/model/bond_sizing_v3.json: run make app")
    rows = scale["rows"]
    bad_proof = [r["n"] for r in rows if not (r["wrongProof"]["status"] == 0 and r["wrongProof"]["revert"] == "NotCovered"
                                               and r["wrongProof"]["episodeCountAndBondUnchanged"])]
    if bad_proof or not rows:
        fail(f"data/derived/scale.json: a wrong-set proof did not fail closed (sets {bad_proof}): run make scale")
    later = [x for x in meas.get("latency_blocks", []) if x.get("label") != lat.get("label")]
    first = next((x for x in meas.get("latency_blocks", []) if x.get("label") == lat.get("label")), {})
    when = (f"measured {first['measuredAt'][:10]}, raw samples not kept" if "raw samples" in lat["label"] and
            first.get("measuredAt") else lat["label"])
    b = {}
    b["scToday"] = "\n".join([
        f"- **One covenant, many facilities.** {pf} In `make demo` the portfolio covenant's episode is proven by its "
        f"{port['proofSteps']}-step Merkle path, and a proof against the wrong set fails `NotCovered`.",
        f"- **Data on chain.** A canonical evidence bundle is {_n(meas['bundle_bytes_jcs'])} bytes off chain; the chain "
        "stores only its 32-byte SHA-256 hash, the IDs and the money.",
        f"- **Latency.** Send to receipt through a public RPC on Ethereum Sepolia: p50 {lat['p50']} s, p90 {lat['p90']} s "
        f"(n = {lat['n']}, {when})"
        + "".join(f"; {x['n']} {x['label']}: p50 {x['p50']} s, p90 {x['p90']} s" for x in later)
        + " ([`data/derived/measurements.json`](../data/derived/measurements.json)). The challenge window, not the "
        "chain, sets how long a verdict takes."])
    tbl = ["| Facilities in the set | Proof steps | `createCovenant` gas | `proposeEpisode` gas "
           "| `proposeEpisode` calldata, bytes | Set |", "|---|---|---|---|---|---|"]
    for r in rows:
        tbl.append(f"| {_n(r['n'])} | {_n(r['proofSteps'])} | {_n(r['createCovenantGas'])} | "
                   f"{_n(r['proposeEpisodeGas'])} | {_n(r['proposeEpisodeCalldataBytes'])} | "
                   f"{_relink(r['label']) if r['real'] else r['label']} |")
    fit = scale["fit"]
    b["scSetSize"] = "\n".join(tbl + ["", (
        f"`createCovenant` is flat ({_n(fit['createCovenantGasMin'])}–{_n(fit['createCovenantGasMax'])} gas: the whole "
        f"set is one 32-byte Merkle root); `proposeEpisode` grows only with the Merkle path, about "
        f"{_n(fit['proposeEpisodeGasPerProofStep'])} gas per proof step, and a path has at most ⌈log2 n⌉ steps. Real "
        f"transactions on {scale['chain'].split(',')[0]} with Foundry's Cancun schedule, {scale['transactions']} in all; every wrong-set proof reverted `NotCovered` and left the episode count and "
        f"the bond unchanged ({scale['status0']} status 0). `make scale` → "
        "[`data/derived/scale.json`](../data/derived/scale.json).")])
    usd = meas["usd_assumption"]
    calls = [f"| Call | Gas, Sepolia median | USD at {usd['stated_gwei']:g} gwei | n |", "|---|---|---|---|"]
    for f in SCALING_CALLS:
        v = fn[f]
        calls.append(f"| `{f}` | {_n(v['gas_sepolia_median'])} | {v['usd_at_stated_gwei']:.2f} | {v['n']} |")
    b["scCalls"] = "\n".join(calls + ["", (
        f"USD at the stated {usd['stated_gwei']:g} gwei and ETH/USD {usd['eth_usd']:,} from {usd['eth_usd_source']}; "
        f"measured {meas['measuredAt'][:10]} ([`data/derived/measurements.json`](../data/derived/measurements.json)). "
        "Ethereum Sepolia prices new state far above Foundry's local schedule (`createCovenant` "
        f"{_n(fn['createCovenant']['gas_anvil_cancun_rounded_1k'])}, `proposeEpisode` "
        f"{_n(fn['proposeEpisode']['gas_anvil_cancun_rounded_1k'])} gas there, rounded), so these are the upper line. "
        "Every other call, deployment included, is in [`FOOTPRINT.md`](FOOTPRINT.md).")])
    pool = policy_mod.POLICY_V2["pool"]
    dep = load(ROOT / "contracts" / "deployments" / "11155111.json")
    v1_bond = load(DERIVED / "audit_numbers.json")["numbers"]["bond_sizing"]["recommended_bond_eth"]
    cost = load(DERIVED / "projection.json")["card"]["costSentence"]
    b["scPays"] = "\n".join([
        f"- **The seller** posts the bond the rebates come from, sized by a rule committed in `policyHash`: "
        f"{v1_bond} ETH for the single-source v1 covenant, the P90 of simulated annual rebate exposure under the "
        f"smoothed rule, and {bsz['recommended_bond_eth']} ETH for the {port['n']}-facility portfolio [SIM], the P90 of "
        "a year's rebate exposure across its facilities.",
        "- **The sponsor** — the lender whose sustainability-linked margin already moves on methane intensity, or the "
        f"buyer itself — funds the pool: the capped refunds, and an observer fee of {_eth(pool['observerFeeWei'])} per "
        "counted pass, split among the signing organisations (`pool.observerFeeWei`). Observing is paid work, paid by "
        "the party that already prices methane.",
        f"- **Open roles post bonds.** Proposing a core episode posts {_eth(dep['proposerBond'])} and challenging one "
        f"{_eth(dep['challengerBond'])} (main Sepolia deployment); attesting a clean pass posts "
        f"{_eth(pool['passBondWei'])} and challenging it {_eth(pool['challengerBondWei'])} (`POLICY_V2`). A challenge "
        "that fails pays its bond to the party the evidence favours: the buyer for a core episode, the seller for a "
        "clean pass or a MARS corroboration. A challenge that succeeds wins the proposer's or the clean-pass "
        "submitter's bond and gets its own back; a challenge left unresolved returns both bonds.",
        f"- **The ledger.** {cost}"])
    tx = meas["transactions"]
    fp = load(DERIVED / "model" / "model_metrics.json")["footprint"]
    b["scFootprint"] = (
        f"Proof of stake (Ethereum Sepolia and a local chain; no proof of work). Our addresses sent {tx['logged']} "
        f"Sepolia transactions; at the Crypto Carbon Ratings Institute's mainnet average of {CCRI_WH_PER_TX} Wh per "
        f"transaction that is ≈ {tx['logged'] * CCRI_WH_PER_TX:,.0f} Wh — an illustration of scale, not a measurement. "
        f"The forecast model that lost to the rule cost {fp['train_plus_inference_kgCO2e']} kg CO₂e to train and score; "
        "the product runs the rule, so routine operation trains nothing. Details and sources: "
        "[`FOOTPRINT.md`](FOOTPRINT.md).")
    cl = clause.pdf_blocks()
    b["scPrecedents"] = _relink(cl["precedents"])
    pilot = cl["pilot"]
    for pdf_ref, here in ((" (rows above)", " (the precedents under “Who pays”)"),
                          ("the two ASSUMPTIONs of section 4", "the two ASSUMPTIONs of the carbon projection")):
        if pdf_ref not in pilot:
            fail(f"pipeline/clause.py pilot block no longer says {pdf_ref!r}: update docs/SCALING.md's rendering")
        pilot = pilot.replace(pdf_ref, here)
    b["scPilot"] = _relink(pilot)
    return b


def _two_sig(x: float) -> str:
    """Two significant digits in plain decimals (0.00088, never 8.8e-04)."""
    import math

    return f"{x:.{max(0, 1 - math.floor(math.log10(x)))}f}"


def projection_blocks() -> dict[str, str]:
    """The PDF's carbon projection (section 4), read from data/derived/projection.json and projection_sources.json
    (pipeline/projection.py): the vision-first lead (headline, scale, floor, cost, then one "How we estimated this"
    paragraph) and the scenario table, the ledger cost, the MARS negative finding, the adoption assumption and the
    sources with their tags."""
    pj = load(DERIVED / "projection.json")
    ps = load(DERIVED / "projection_sources.json")
    if pj["parameters"] != ps["parameters"]:
        fail("data/derived/projection.json is older than projection_sources.json: run make projection")
    card, sc = pj["card"], pj["scopes"]
    names = {"portfolio_v3": "Portfolio [SIM]", "backtest_set": "Backtest set"}
    # the table follows projection.json's headline kind: "central" (board T4d option d) prints the measured-hazard
    # figure with the no-recurrence end in brackets; "range" (the reviewed interim) prints the two ends of the stop share
    central = pj["headline"]["kind"] == "central"
    if central:
        rows = ["| Scope | Stake low bound, kt CH₄/yr | Acted on "
                "| Projected stops, kt CH₄/yr: with the measured re-onset hazard (up to: no recurrence) "
                "| Mt CO₂e/yr at GWP-100 (up to) | Mt CO₂e/yr at GWP-20 (up to) | Ledger USD per t CO₂e |",
                "|---|---|---|---|---|---|---|"]
    else:
        rows = ["| Scope | Stake low bound, kt CH₄/yr | Acted on "
                "| Projected stops, kt CH₄/yr: one round net of new faults – every fix holding all year "
                "| Mt CO₂e/yr at GWP-100 | Mt CO₂e/yr at GWP-20 | Ledger USD per t CO₂e |", "|---|---|---|---|---|---|---|"]
    for key, s in ((k, sc[k]) for k in names):
        for r in s["scenarios"]:
            kt, g100, g20 = r["kt_ch4_per_yr"], r["mt_co2e_per_yr_gwp100"], r["mt_co2e_per_yr_gwp20"]
            led = r["ledgerUsdPerTco2eGwp100"]
            of = f"{s['facilitiesQuantified']} of {s['facilities']}" if key == "portfolio_v3" else s["facilitiesQuantified"]
            if central:
                rows.append(f"| {names[key]} ({of} facilities quantified) | {s['stakeLowKtCh4PerYr']:,.0f} "
                            f"| {r['responseShare'] * 100:.0f} % | {kt['low']:,.0f} ({kt['high']:,.0f}) "
                            f"| {g100['low']:.2f} ({g100['high']:.2f}) | {g20['low']:.2f} ({g20['high']:.2f}) "
                            f"| {_two_sig(led['low'])} |")
            else:
                rows.append(f"| {names[key]} ({of} facilities quantified) | {s['stakeLowKtCh4PerYr']:,.0f} "
                            f"| {r['responseShare'] * 100:.0f} % | {kt['low']:,.0f} – {kt['high']:,.0f} "
                            f"| {g100['low']:.2f} – {g100['high']:.2f} | {g20['low']:.2f} – {g20['high']:.2f} "
                            f"| {_two_sig(led['high'])} – {_two_sig(led['low'])} |")
    ev = pj["evidence"]["line2"]
    labels = {"allNotified": "All notified", "joinNeighbourhood": "Committed join neighbourhood (absent today)"}
    es_rows = ["| Coverage | Proxy date | Window / gap, days | Min passes per side | Sources: Yes / No "
               "| T0 before feedback tracking: Yes / No | Post window truncated: Yes / No "
               "| Detection rate before → after: Yes; No | Difference in differences (CI) | Minimum detectable |",
               "|---|---|---|---|---|---|---|---|---|---|"]
    for key, lab in labels.items():
        for run in [ev["coverageSets"][key]["primary"], *ev["coverageSets"][key]["variants"]]:
            s, g, d = run["spec"], run["groups"], run["did"]
            es_rows.append(
                f"| {lab} | {'first notified plume' if s['proxy'] == 'firstNotifiedPlume' else 'first plume'} "
                f"| {s['windowDays']} / {s['gapDays']} | {s['minPassesEachSide']} "
                f"| {g['Yes']['sources']} / {g['No']['sources']} "
                f"| {g['Yes']['t0BeforeFeedbackTracking']} / {g['No']['t0BeforeFeedbackTracking']} "
                f"| {g['Yes']['postWindowTruncated']} / {g['No']['postWindowTruncated']} "
                f"| {g['Yes']['preRate']:.2f} → {g['Yes']['postRate']:.2f}; {g['No']['preRate']:.2f} → "
                f"{g['No']['postRate']:.2f} | {d['yesMinusNo']:+.3f} ({d['ci'][0]:+.3f} to {d['ci'][1]:+.3f}) "
                f"| {d['minimumDetectableEffect']:.3f} |")
    srcs = ps["sources"]
    src_lines = []
    for n, p in ps["parameters"].items():
        for key, how in (("sources", ""), ("informedBy", " (ASSUMPTION, informed by)")):
            for sid in p.get(key) or []:
                s = srcs[sid]
                link = s["url"] if s["url"].startswith("http") else f"[`{s['url']}`]({s['url']})"
                own = " (the source's wording, not a claim of ours)" if any("abat" in q for q in s["quotes"]) else ""
                quotes = " · ".join(f"“{q}”" for q in s["quotes"])
                src_lines.append(f"- `{n}`{how} — {s['title']} ({s['date']}) {s['tag']}: {quotes}{own} {link}")
    for sid, s in srcs.items():
        if s.get("context"):
            src_lines.append(f"- Context — {s['title']} ({s['date']}) {s['tag']}: “{s['quotes'][-1]}” {s['url']}. "
                             f"{s['context']}")
    return {
        # vision first (user decision 2026-10-16): headline ([PROJECTION] once), scale, floor, cost, then the
        # ASSUMPTIONs, the null and "not a measured reduction" once, in the methods paragraph; the table last
        "projection": "\n\n".join([card["headlineSentence"], card["scaleSentence"], card["floorLineSentence"],
                                   card["costLineSentence"], card["methodsSentence"], "\n".join(rows)]),
        "projectionLimit": f"- **Carbon projection.** {card['limitSentence']}",
        "projectionCost": card["costSentence"],
        "projectionFinding": card["negativeFindingSentence"],
        "projectionEvidence": "\n\n".join([card["line1Sentence"], card["line2Sentence"], "\n".join(es_rows)]),
        "projectionAdoption": card["adoptionSentence"],
        "projectionSources": "\n".join(
            [f"Parameters and their sources ([`data/derived/projection_sources.json`]"
             f"(data/derived/projection_sources.json); [V] read at the source on the date recorded, [S] secondary; "
             f"the action scenarios and the {'time' if central else 'stop'} share are ASSUMPTION): formula "
             f"`{pj['formula']}`."] + src_lines),
    }


def _apply(text: str, blocks: dict[str, str], require: bool = True, doc: str = "README.md") -> str:
    import re

    for name, body in blocks.items():
        pat = re.compile(rf"<!-- gen:{name}:begin -->.*?<!-- gen:{name}:end -->", re.S)
        if not pat.search(text):
            if require:
                fail(f"{doc} has no gen:{name} block")
            continue
        text = pat.sub(lambda _: f"<!-- gen:{name}:begin -->\n{body}\n<!-- gen:{name}:end -->", text)
    return text


def readme(write: bool) -> None:
    app = load(APP / "app.json")
    text = README.read_text()
    blocks = readme_blocks(app)
    dp_only = {k: blocks.pop(k) for k in DEVPOST_ONLY}
    short = {k.removeprefix(README_ONLY): blocks.pop(k) for k in [k for k in blocks if k.startswith(README_ONLY)]}
    dp = DEVPOST.read_text() if DEVPOST.exists() else None
    pdf = PDF.read_text() if PDF.exists() else None  # not in every checkout (the public export may leave it out)

    def devpost(t: str) -> str:
        return _apply(_apply(t, blocks, require=False), dp_only)

    def pdf_doc(t: str) -> str:
        return _apply(_apply(t, blocks | dp_only, require=False), pdf_blocks(app), doc="docs/pdf/methaneclause.md")

    # README.md (25 KB budget) quotes the backtest's short refunds sentence; Devpost and PDF keep the full one.
    bt = load(DERIVED / "backtest.json")["card"]
    full = f"  - **Refunds.** {_without_selection(bt['refundsSentence'])}"
    if full not in short["numbers"]:
        fail("README numbers block no longer carries the backtest refunds sentence")
    short["numbers"] = short["numbers"].replace(full, f"  - **Refunds.** {_without_selection(bt['refundsShortSentence'])}")
    rd = blocks | short  # README.md's blocks; Devpost and the PDF keep `blocks`

    fpt = FOOTPRINT.read_text() if FOOTPRINT.exists() else None
    fp_blocks = footprint_blocks()
    spt = SPEC_PUBLIC.read_text() if SPEC_PUBLIC.exists() else None
    sp_blocks = spec_blocks(app)
    sct = SCALING.read_text() if SCALING.exists() else None
    sc_blocks = scaling_blocks(app)

    if write:
        if fpt is not None:
            FOOTPRINT.write_text(_apply(fpt, fp_blocks, doc="docs/FOOTPRINT.md"))
        if spt is not None:
            SPEC_PUBLIC.write_text(_apply(spt, sp_blocks, doc="docs/spec-public.md"))
        if sct is not None:
            SCALING.write_text(_apply(sct, sc_blocks, doc="docs/SCALING.md"))
        if dp is not None:
            DEVPOST.write_text(devpost(dp))
        if pdf is not None:
            PDF.write_text(pdf_doc(pdf))
        text = _apply(text, rd)  # the export the counts are run in checks the other blocks: write them first
        README.write_text(text)
        rd[COUNTS_BLOCK] = _render_counts()
        new = _apply(text, rd)
        README.write_text(new)
        size = len(new.encode())
        print(f"README.md blocks rendered ({size:,} bytes)")
        if size > README_BUDGET:
            fail(f"README.md is {size:,} bytes, over the {README_BUDGET:,}-byte budget")
        return
    if _apply(text, rd) != text:
        fail("README.md generated blocks are stale: run make readme")
    if dp is not None and devpost(dp) != dp:
        fail("docs/devpost.md generated blocks are stale: run make readme")
    if pdf is not None and pdf_doc(pdf) != pdf:
        fail("docs/pdf/methaneclause.md generated blocks are stale: run make readme")
    if fpt is not None and _apply(fpt, fp_blocks, doc="docs/FOOTPRINT.md") != fpt:
        fail("docs/FOOTPRINT.md generated blocks are stale: run make readme")
    if spt is not None and _apply(spt, sp_blocks, doc="docs/spec-public.md") != spt:
        fail("docs/spec-public.md generated blocks are stale: run make readme")
    if sct is not None and _apply(sct, sc_blocks, doc="docs/SCALING.md") != sct:
        fail("docs/SCALING.md generated blocks are stale: run make readme")
    print("README.md generated blocks are current")


def main() -> None:
    if sys.argv[1:] == ["--resolve-scenes"]:
        resolve_scenes()
        return
    if sys.argv[1:] in (["--readme"], ["--readme-check"]):
        readme(write=sys.argv[1] == "--readme")
        return
    APP.mkdir(parents=True, exist_ok=True)
    audit = load(DERIVED / "audit_numbers.json")["numbers"]
    dep = load(ROOT / "contracts" / "deployments" / "11155111.json")
    run = load(DERIVED / "testnet_run.json")
    demo = load(DERIVED / "demo_run.json")
    local = ROOT / "out" / "demo" / "run.json"
    hybrid = load(local)["hybrid"] if local.exists() else None
    if hybrid is None:
        prev = APP / "app.json"
        hybrid = load(prev)["hybrid"]["entries"] if prev.exists() else None
        if hybrid is None:
            sys.exit("run `make demo` first (the tamper panel needs the hybrid signatures from out/demo/run.json)")
    for e in hybrid:  # uint64 values exceed 2^53: as strings, so JSON.parse in the browser keeps them exact
        e["attestation"] = {k: str(v) if isinstance(v, int) else v for k, v in e["attestation"].items()}
    local_dep = load(local)["deployment"] if local.exists() else None
    receipts = load(local).get("observerReceipts") if local.exists() else None
    if receipts is None and (APP / "app.json").exists():
        receipts = (load(APP / "app.json").get("observerReplay") or {}).get("receipts")
    # the KeyRegistry record the demo read on chain (address from the deployment record, pqPubKeyHash from the
    # registry): the page and the CleanPass check verify against it, not against the signer field of the same entry
    if local.exists():  # a demo run is the source: its registry record must be there, never the old app.json's
        r = load(local)
        registered = r.get("registeredKeys")
        if not registered:
            fail("out/demo/run.json has no registeredKeys (an older demo run): run make demo again")
        for org, k in registered.items():
            if k["address"].lower() != r["deployment"].get(f"observer{org[-1]}", "").lower():
                fail(f"{org}: registered address {k['address']} is not the deployment record's observer{org[-1]}")
    else:  # fresh clone without a demo run: the committed record
        registered = (load(APP / "app.json")["hybrid"].get("registeredKeys") if (APP / "app.json").exists() else None)
        if not registered:
            fail("run `make demo` first (the signature checks need the registry record in out/demo/run.json)")
    for e in hybrid:
        k = registered.get(e["org"])
        if k is None or k["address"].lower() != e["signer"].lower() or k["pqPubKeyHash"] != e["mldsa44"]["pkHash"]:
            fail(f"{e['org']}: the demo's signer / ML-DSA key is not the one the registry record holds")
    lead = hybrid[0]
    receipts = [r for r in (receipts or []) if r.get("evidenceHash") == lead["evidenceHash"]]
    if not receipts:
        sys.exit("run `make demo` first (the observer panel replays the demo's observer receipts)")
    for r in receipts:
        if r["contentSha256"] != lead["bundle"]["providerContentSha256"]:
            sys.exit(f"receipt of {r['org']} does not match the bundle's providerContentSha256")
    m = audit["model"]
    data = {
        "generatedFrom": ["data/derived/*", "contracts/deployments/11155111.json", "out/demo/run.json",
                          "data/derived/sepolia_txlog.jsonl + sepolia_txs.json (judge cure arc)"],
        "cases": {n: case_view(n) for n in CASES},
        "join": {k: audit["join_v1"][k] for k in ("window", "n_candidates", "n_mars_target_sources", "radius_km",
                                                  "mars_as_of")},
        "policy": {**{k: POLICY[k] for k in ("start", "k", "n", "challengeWindow", "contestWindow", "linkWindow",
                                             "term", "minRateKgH")},
                   "tierMinKgH": TIER_MIN_KGH, "tierAmountsWei": [str(x) for x in TIER_AMOUNTS_WEI]},
        "model": {"decision": m["decision"],
                  "bss_test_a": m["test_a"]["model"]["bss_vs_B1"] if "model" in m["test_a"] else None,
                  "bss_test_b": m["test_b"]["model"]["bss_vs_B1"] if "model" in m.get("test_b", {}) else None},
        "bond": audit["bond_sizing"],
        "stake": audit["stake_band"],
        "sepolia": {"chainId": 11155111, "explorer": "https://sepolia.etherscan.io",
                    "rpc": "https://ethereum-sepolia-rpc.publicnode.com",
                    # keyless public endpoints the page tries in order, per transaction (publicnode prunes older
                    # receipts: result null); each sends Access-Control-Allow-Origin: *
                    "rpcFallbacks": ["https://sepolia.drpc.org", "https://1rpc.io/sepolia"],
                    "methaneCovenant": dep["methaneCovenant"], "keyRegistry": dep["keyRegistry"],
                    "seller": dep["seller"], "buyer": dep["buyer"],
                    "observers": {"OBS_A": dep["observerA"], "OBS_B": dep["observerB"], "OBS_C": dep["observerC"]},
                    "sandbox": dep["sandbox"],
                    # the judge sandbox's windows [SIM] (pipeline/policy.py SANDBOX_POLICY): the page's witness flow
                    "sandboxWindows": {k: policy_mod.SANDBOX_POLICY[k] for k in ("challengeWindow", "contestWindow")},
                    # the main deployment's run, then the refund side run by a judge test wallet in the judge sandbox
                    # [SIM] (pipeline/cure_arc.py: labels from the tracked txlog, status 1 each in sepolia_txs.json)
                    "run": {**run, "steps": run["steps"] + cure_arc.app_steps(cure_arc.arc())}},
        "demo": demo,
        "hybrid": {"chainId": local_dep["chainId"] if local_dep else 31337,
                   "verifyingContract": local_dep["methaneCovenant"] if local_dep else None,
                   "entries": hybrid, "registeredKeys": registered},
        "measurements": load(DERIVED / "measurements.json") if (DERIVED / "measurements.json").exists() else None,
        "adversarial": {k: v for k, v in load(DERIVED / "adversarial.json").items() if k != "slowDrift"},
        "abi": abi_subset(),
        # ---- v2 (spec-v2 §8): mock-backed until the pipeline's files land; each block says where it came from
        "ledger": build_ledger(lead["bundle"]["plumeId"]),
        "observerReplay": {"receipts": receipts, "minRateKgH": POLICY["minRateKgH"],
                           "covenantStart": load(local)["covenantStart"] if local.exists()
                           else (load(APP / "app.json").get("observerReplay") or {}).get("covenantStart")},
        "detectionList": detection_list(),
        "cleanPassDemo": clean_pass_demo(registered, lead["bundle"]["covenantId"]),
        "modeStrip": {"marsAsOf": audit["join_v1"]["mars_as_of"], "model": m["decision"]},
        "capReasons": cap_reasons(m),
        "cleanLook": clean_look_rules(),
        "demoClock": demo_clock(local),
        "replay": replay_view(ROOT / "out" / "demo" / "replay.json"),
        "exports": exports_view(),
        "portfolio": portfolio_view(),
        "verification": verification(),
    }
    data["mock"] = sorted(MOCK_USED)
    if data["hybrid"]["verifyingContract"] is None:
        data["hybrid"]["verifyingContract"] = load(APP / "app.json")["hybrid"]["verifyingContract"]
    (APP / "app.json").write_text(json.dumps(data, indent=1, sort_keys=True) + "\n")
    for f in ("TKM_S_276.annex_d.json", "TKM_S_276.annex_d.csv"):
        shutil.copy(DERIVED / "export" / f, APP / f)
    vec = APP / "vectors"
    vec.mkdir(exist_ok=True)
    for f in (ROOT / "pipeline" / "tests" / "vectors").glob("*.json"):
        shutil.copy(f, vec / f.name)
    print(f"app data -> {APP.relative_to(ROOT)} ({(APP / 'app.json').stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
