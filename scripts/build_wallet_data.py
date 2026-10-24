#!/usr/bin/env python3
"""Build app/data/wallet.json: everything app/wallet.js needs to let a judge's browser wallet act in the judge sandbox.

    pipeline/.venv/bin/python scripts/build_wallet_data.py           # write app/data/wallet.json (make wallet-data)
    pipeline/.venv/bin/python scripts/build_wallet_data.py --check   # exit 1 if the committed file is stale

Carbon Mapper sends no CORS header, so the browser cannot fetch the provider itself. The observer pipeline builds the
canonical bundles here, from the committed case files, and validates them with the same code the terminal observer
runs (`observer.bundle.validate`, `observer.cleanpass.validate`). The page then fills in the ids that only exist on
chain (your covenant, your claim), re-hashes (RFC 8785 + SHA-256), and your wallet signs the EIP-712 typed data.

Contents (all from committed inputs; nothing typed by hand):
  network      the Ethereum Sepolia judge sandbox [SIM] from contracts/deployments/11155111.json
  sandboxKeys  how the published [SIM] sandbox keys are derived (keccak256 of a public string) and which address each
               role must have; wallet.js derives them and refuses to use one whose address differs
  covenant     the sandbox covenant policy (pipeline/policy.py SANDBOX_POLICY) and the bond (bond_sizing.json / 100)
  detections   bundle templates: TKM_S_276's 2026-06-11 plume (the judge-attest evidence) and DZA_S_012's relapse
               plume (the cure's episode), with the facility's Merkle proof
  cleanPasses  the cure's clean-pass bundle template (DZA_S_012, first qualified scene after the relapse) and the
               sandbox pool's policy hash and on-chain PoolParams (POLICY_V2_SANDBOX)
  parity       Python's own outputs for fixed inputs: evidenceHash, Attestation digest, sceneHash, CleanPass digest,
               policy hash; the browser module must reproduce each byte for byte (scripts/tests/wallet.test.mjs)
  abi          KeyRegistry, MethaneCovenant and IncentivePool (+ the errors of its linked libraries), for encoding calls
               and decoding revert reasons
Deterministic: two runs on the same inputs write the same bytes.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

import policy as policy_mod  # noqa: E402
from canon import evidence_hash  # noqa: E402
from common import DERIVED  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_utils import to_checksum_address  # noqa: E402
from web3 import Web3  # noqa: E402
from observer import attest, cleanpass, sandbox  # noqa: E402
from observer.bundle import INSTRUMENTS, build_bundle, bundle_hash, iso_to_unix, validate  # noqa: E402
from observer.service import load_facility  # noqa: E402

OUT = ROOT / "app" / "data" / "wallet.json"
DEPLOYMENT = ROOT / "contracts" / "deployments" / "11155111.json"
ARTIFACTS = ROOT / "contracts" / "out"
POOL_LIBRARIES = ("PoolVerify", "PoolSetup", "PoolEpisodes", "PoolCorroboration", "PoolInstallments")
ZERO32 = "0x" + "00" * 32
LEAD_CASE, LEAD_PLUME = "TKM_S_276", "tan20260611t083132c23s4001-AB"  # judge-attest's evidence (judge.py PLUMES)
CURE_CASE = "DZA_S_012"  # judge-cure's case (judge.py CURE_CASE)
RPC = "https://ethereum-sepolia-rpc.publicnode.com"  # the public RPC the page already reads (app.json sepolia.rpc)
EXPLORER = "https://sepolia.etherscan.io"
# fixed inputs for the parity vector (any values do; these are not on chain)
PARITY = {"covenantId": "0x" + "c0" * 32, "claimId": "0x" + "c1" * 32, "nonce": 7, "expiry": 1760000000,
          "judge": "0x000000000000000000000000000000000000dEaD"}


def fail(msg: str):
    sys.exit(f"build_wallet_data: {msg}")


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def artifact_abi(name: str) -> list:
    p = ARTIFACTS / f"{name}.sol" / f"{name}.json"
    if not p.exists():
        fail(f"{rel(p)} missing: run `forge build` in contracts/ first (make test-contracts does)")
    return json.loads(p.read_text())["abi"]


def abis() -> dict:
    pool = artifact_abi("IncentivePool")
    have = {json.dumps(e, sort_keys=True) for e in pool if e["type"] == "error"}
    for lib in POOL_LIBRARIES:  # library errors bubble up through the pool's delegatecalls
        for e in artifact_abi(lib):
            if e["type"] == "error" and json.dumps(e, sort_keys=True) not in have:
                have.add(json.dumps(e, sort_keys=True))
                pool.append(e)
    return {"KeyRegistry": artifact_abi("KeyRegistry"), "MethaneCovenant": artifact_abi("MethaneCovenant"),
            "IncentivePool": pool}


# ------------------------------------------------------------------------------------------------ detections
def detection(case: str, plume_id: str) -> dict:
    facility, covered, case_file = load_facility(case)
    plume = next((p for p in case_file["cm_plumes_within_radius"] if p["plume_id"] == plume_id), None)
    if plume is None:
        fail(f"{plume_id} is not in the committed {case} snapshot")
    cm = case_file["cm_sources"][plume["source_name"]]
    b = build_bundle(dict(plume), covenant_id=ZERO32, source_id=facility["sourceId"],
                     source_name=facility["source_name"], content_sha256=cm["content_sha256"])
    # the same checks the terminal observer runs, against the sandbox covenant start; `now` = publication time
    now = max(b["detectedAt"], iso_to_unix(b["publishedAt"]))
    validate(b, facility=facility, covered_cm=covered, covenant_start=policy_mod.COVENANT_START, now=now,
             min_rate_kgh=policy_mod.SANDBOX_POLICY["minRateKgH"])
    return {"case": case, "plumeId": plume_id, "bundleTemplate": b, "instrumentId": INSTRUMENTS[b["instrument"]],
            "facility": {"sourceId": facility["sourceId"], "sourceName": facility["source_name"],
                         "proof": facility["proof"], "root": facility["root"], "lat": facility["lat"],
                         "lon": facility["lon"], "coveredCmSources": sorted(covered)},
            "receipt": {"mode": "snapshot", "url": "https://api.carbonmapper.org/api/v1/catalog/sources/"
                        + plume["source_name"], "retrievedAt": cm["fetchedAt"],
                        "contentSha256": cm["content_sha256"], "file": f"data/derived/cases/{case}.json"}}


def cure_plume_id() -> str:
    """judge.py cmd_cure: the relapse scene's strongest plume."""
    doc = cleanpass.load_case(CURE_CASE)
    relapse = doc["summary"]["firstPlumeAfterSince"]["sceneName"]
    _, _, case_file = load_facility(CURE_CASE)
    return max((p for p in case_file["cm_plumes_within_radius"]
                if p["plume_id"].rsplit("-", 1)[0] == relapse and p.get("emission_auto") is not None),
               key=lambda p: p["emission_auto"])["plume_id"]


def cure_clean_pass() -> dict:
    """judge.py cmd_cure: the first qualified scene after the relapse, as a bundle template (ids filled by the page)."""
    doc = cleanpass.load_case(CURE_CASE)
    relapse_t = iso_to_unix(doc["summary"]["firstPlumeAfterSince"]["sceneAtIso"])
    scene = next(x for x in doc["scenes"] if x["qualified"] and x["sceneAt"] > relapse_t)
    b = cleanpass.build_bundle(doc, cleanpass.find_scene(doc, scene["sceneProviderId"]), covenant_id=ZERO32,
                               claim_id=ZERO32)
    cleanpass.validate(b, cleanpass.claim_context(doc))  # rules 1-6, as the terminal observer checks them
    pol = policy_mod.POLICY_V2_SANDBOX
    hashes = json.loads((DERIVED / "policy_v2.json").read_text())["policies"]["POLICY_V2_SANDBOX"]
    t = policy_mod.pool_params(pol)
    params = dict(zip(policy_mod.POOL_PARAMS_FIELDS, t))
    params["installments"] = [{"afterPasses": a, "shareBp": s, "twoInstruments": two, "isFinal": fin}
                              for a, s, two, fin in t[9]]
    for k in ("passBond", "challengerBond", "observerFeeWei"):
        params[k] = str(params[k])
    params["unitTierAmounts"] = [str(x) for x in t[8]]
    if hashes.get("onchainParamsHash") != pol["onchainParamsHash"]:
        fail("data/derived/policy_v2.json is stale against pipeline/policy.py (POLICY_V2_SANDBOX)")
    return {"case": CURE_CASE, "sceneName": scene["sceneName"], "sceneProviderId": scene["sceneProviderId"],
            "bundleTemplate": b, "instrumentId": INSTRUMENTS[b["instrument"]],
            "pool": {"policy": "POLICY_V2_SANDBOX", "policyHash": hashes["policyHash"],
                     "onchainParamsHash": pol["onchainParamsHash"], "params": params,
                     "passBondWei": pol["pool"]["passBondWei"], "observerFeeWei": pol["pool"]["observerFeeWei"],
                     "firstInstallmentAfter": pol["pool"]["installments"][0][0]},
            "file": f"data/derived/cases/{CURE_CASE}.clean_passes.json"}


# ------------------------------------------------------------------------------------------------ parity vector
def parity(det: dict, cp: dict, dep: dict, cov: dict) -> dict:
    chain = 11155111
    b = {**det["bundleTemplate"], "covenantId": PARITY["covenantId"]}
    ev = bundle_hash(b)
    att = {"covenantId": attest.b32(PARITY["covenantId"]), "sourceId": attest.b32(b["sourceId"]),
           "evidenceHash": attest.b32(ev), "modelHash": bytes(32), "emissionKgH": b["emissionKgH"],
           "uncertaintyKgH": b["uncertaintyKgH"], "detectedAt": b["detectedAt"],
           "instrumentId": INSTRUMENTS[b["instrument"]], "providerId": attest.b32(b["providerId"]),
           "kind": attest.KIND_DETECT, "nonce": PARITY["nonce"], "expiry": PARITY["expiry"]}
    cb = {**cp["bundleTemplate"], "covenantId": PARITY["covenantId"], "claimId": PARITY["claimId"]}
    msg = cleanpass.message(cb, nonce=PARITY["nonce"], expiry=PARITY["expiry"])
    pol = {**policy_mod.SANDBOX_POLICY, "bondWei": cov["bondWei"], "judge": PARITY["judge"],
           "sourceSetRoot": det["facility"]["root"]}
    hexed = {k: ("0x" + v.hex() if isinstance(v, bytes) else v) for k, v in msg.items()}
    return {"inputs": {**PARITY, "chainId": chain, "covenant": dep["methaneCovenant"],
                       "pool": dep["incentivePool"]},
            "evidenceHash": ev,
            "attestationDigest": "0x" + attest.digest(att, chain, dep["methaneCovenant"]).hex(),
            "sceneHash": cleanpass.bundle_hash(cb),
            "cleanPassMessage": hexed,
            "cleanPassDigest": "0x" + attest.pool_digest("CleanPass", msg, chain, dep["incentivePool"]).hex(),
            "policyHash": evidence_hash(pol)}


def build() -> dict:
    full = json.loads(DEPLOYMENT.read_text())
    dep = full["sandbox"]
    for k in ("keyRegistry", "methaneCovenant", "incentivePool", "judgeController", "independentController"):
        if not dep.get(k):
            fail(f"{rel(DEPLOYMENT)} has no sandbox.{k}")
    keys = {"JUDGE_CONTROLLER": dep["judgeController"], "INDEPENDENT_CONTROLLER": dep["independentController"],
            "SELLER": dep["seller"], "BUYER": dep["buyer"], "OBS_1": dep["observer1"], "OBS_2": dep["observer2"]}
    for role, addr in keys.items():  # the derivation here must give the recorded addresses
        derived = Account.from_key(Web3.keccak(text=sandbox.PREFIX + role)).address
        if derived != to_checksum_address(addr):
            fail(f"published key {role} derives to {derived}, the record says {addr}")
    sizing = DERIVED / "model" / "bond_sizing.json"
    bond = int(json.loads(sizing.read_text())["recommended_bond_wei"]) // policy_mod.SANDBOX_SCALE
    p = policy_mod.SANDBOX_POLICY
    cov = {"policy": p, "bondWei": str(bond), "start": policy_mod.COVENANT_START,
           "params": {"term": p["term"], "start": policy_mod.COVENANT_START, "k": p["k"],
                      "challengeWindow": p["challengeWindow"], "contestWindow": p["contestWindow"],
                      "linkWindow": p["linkWindow"], "minRateKgH": p["minRateKgH"],
                      "tierAmounts": [str(x) for x in policy_mod.SANDBOX_TIER_AMOUNTS_WEI]},
           "policyHashRule": "SHA-256 of RFC 8785 of {...policy, bondWei, judge: <your checksummed address>, "
                             "sourceSetRoot} (pipeline/observer/judge.py cmd_attest)"}
    lead = detection(LEAD_CASE, LEAD_PLUME)
    cure = detection(CURE_CASE, cure_plume_id())
    cp = cure_clean_pass()
    inputs = [DEPLOYMENT, sizing, DERIVED / "policy_v2.json", DERIVED / "sourceset_v2.json",
              DERIVED / "cases" / f"{LEAD_CASE}.json", DERIVED / "cases" / f"{CURE_CASE}.json",
              DERIVED / "cases" / f"{CURE_CASE}.clean_passes.json"]
    return {
        "schema": "methaneclause.wallet/1",
        "generatedFrom": [{"path": rel(x), "sha256": sha256_file(x)} for x in inputs],
        "network": {"chainId": 11155111, "name": "Ethereum Sepolia", "explorer": EXPLORER, "rpc": RPC,
                    "sandbox": {k: dep[k] for k in ("keyRegistry", "methaneCovenant", "incentivePool",
                                                    "proposerBond", "challengerBond")}},
        "sandboxKeys": {
            "prefix": sandbox.PREFIX, "derivation": "privateKey = keccak256(utf8(prefix + role)); generation n > 1: "
                                                    "role + '#' + n (pipeline/observer/sandbox.py)",
            "why": "judge sandbox [SIM]: these keys are published on purpose so a fresh wallet can play every role; "
                   "they protect nothing and live in their own registry, never the main deployment's",
            "addresses": keys,
            "orgs": {"JUDGE_CONTROLLER": sandbox.JUDGE_ORG, "INDEPENDENT_CONTROLLER": "OBS_INDEPENDENT",
                     **sandbox.ORGS},
            "registryRole": {"OBSERVER": 1, "SELLER": 2, "BUYER": 3}, "maxGeneration": sandbox.MAX_GENERATION},
        "covenant": cov,
        "detections": {LEAD_CASE: lead, CURE_CASE: cure},
        "cleanPasses": {CURE_CASE: cp},
        "parity": parity(lead, cp, dep, cov),
        "abi": abis(),
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> None:
    text = render(build())
    if "--check" in sys.argv[1:]:
        if not OUT.exists() or OUT.read_text() != text:
            fail(f"{rel(OUT)} is stale: run `make wallet-data`")
        print(f"build_wallet_data: {rel(OUT)} is up to date")
        return
    OUT.write_text(text)
    print(f"build_wallet_data: wrote {rel(OUT)} ({len(text.encode()):,} B, {datetime.now():%H:%M:%S})")


if __name__ == "__main__":
    main()
