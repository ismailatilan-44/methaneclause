"""Walking skeleton, end to end on a local chain (spec §11 `make demo`). Run by scripts/demo.sh after Deploy.s.sol.

  seller [SIM] creates + funds a covenant over the committed facility set, start backdated to 2026-01-01 [SIM]
     (the contract records createdAt, so the chain itself shows the 2026-06-11 plume was public before creation)
  OBS_A and OBS_B fetch, validate, hash and sign; their evidenceHashes must be equal (independent fetches only with
     LIVE=1; the default snapshot mode reads one committed file)
  rejections (each a real transaction with receipt status 0; reason name read from an eth_call simulation of the
  same call; bond unchanged):
     tamper 2,548 -> 548                     -> InvalidSignature
     seller key signs with OBS_A             -> SelfAttestation
     OBS_C sees a re-quantified rate         -> AttestationMismatch (a batch with a dissenter reverts; with k=2 of 3
                                                a dissenter can be left out, which is the k-of-n trade-off)
     episode keyed by a CM twin source       -> NotCovered (only facilities are leaves)
     same attestation twice in one batch     -> Replay (per-key nonce bitmap)
  the real 2-of-3 proposal -> PROPOSED; seller mitigation claim -> state unchanged
  challenge window passes -> finalize (anyone) -> CONFIRMED -> buyer withdraws the rebate
  after payment (spec §9 1:45-2:05 rejections and the link window):
     identical proposal resubmitted            -> DuplicateEvidence (idempotent resubmission)
     OBS_B revoked by its org, then attests    -> RevokedKey
     2026-06-20 plume, 9 days after the paid episode -> LINKED: recorded, no second payout

Exits non-zero if any step does not behave as stated. Writes out/demo/run.json (gitignored).

Waiting (spec-v2 §8.9): every window is skipped by `advance_clock`, which only moves the chain clock forward
(`evm_setNextBlockTimestamp` + `evm_mine`; never backwards), prints "[SIM: chain clock advanced <n> days — <window>]"
and adds the skipped seconds to run.json["skippedSeconds"][<window>]. REALTIME=1 waits instead.
Determinism: on the pinned clock (scripts/demo.sh) the observers' nonces come from a generator seeded with the
deployment, and ML-DSA-44 signs in its deterministic variant (FIPS 204 allows both), so a re-run reproduces every
signature, hash and gas figure.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import sys
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3

from canon import evidence_hash
from common import DERIVED, ROOT
from datetime import datetime, timezone

from observer import attest, pq
from observer.attest import cleanpass_tuple, corroboration_tuple
from observer.chain import STATES, Chain
from observer.bundle import bundle_bytes, bundle_hash, iso_to_unix
from observer.service import Observer, anvil_key, load_facility, observer_key
from policy import COVENANT_START, POLICY, TIER_AMOUNTS_WEI

CASE = "TKM_S_276"
PLUME = "tan20260611t083132c23s4001-AB"
PLUME_LINKED = "tan20260620t083638c93s4001-AD"  # same CM source, 9 days later: inside linkWindow
OUT = ROOT / "out" / "demo"
# The case snapshot the covenant policy commits to (snapshotManifestHash): the exact bytes the live Sepolia v1 covenant
# committed to, frozen so its policyHash keeps reproducing while data/derived/cases/TKM_S_276.json evolves (the
# headline count moved to the facility-radius basis after that covenant was created; see sepolia_v1/README.md).
POLICY_SNAPSHOT = DERIVED / "cases" / "sepolia_v1" / f"{CASE}.json"

FALLBACK_BOND_WEI = 10**16  # 0.01 ETH, only if model/bond.py has not run

ok_count = 0
SKIPPED: dict[str, int] = {}  # window -> seconds the chain clock was advanced over it [SIM]


def _days(secs: int) -> str:
    """Days for a window of a day or more, minutes below (the judge sandbox's windows are 10 minutes)."""
    minutes = max(1, round(secs / 60))
    if minutes < 1440:
        return f"{minutes} min"
    n = f"{minutes / 1440:.2f}".rstrip("0").rstrip(".")
    return n + (" day" if n == "1" else " days")


MAX_LOOKBACK = 365 * 86400  # MethaneCovenant.MAX_LOOKBACK (365 days); test_realtime_cutoff_matches_contract pins it


def realtime_refusal(now: int) -> str | None:
    """REALTIME=1 runs v1 on the wall clock; the backdated covenant start must stay inside the contract's lookback."""
    cutoff = COVENANT_START + MAX_LOOKBACK
    if now <= cutoff:
        return None
    day = datetime.fromtimestamp(cutoff, timezone.utc).strftime("%Y-%m-%d")
    start = datetime.fromtimestamp(COVENANT_START, timezone.utc).strftime("%Y-%m-%d")
    return (f"REALTIME=1 runs on the wall clock, which is past {day} (the {start} covenant start + the contract's "
            "365-day lookback); run the default pinned clock instead (make demo)")


def advance_clock(ch, target: int, window: str, log=print) -> int:
    """Move the local chain clock forward to `target` to skip `window`; returns the seconds skipped.

    Forward only: a target at or before the chain's current time is a no-op, and the clock is never set back.
    REALTIME=1 waits the window out instead (mining an empty block every few seconds) and skips nothing."""
    if not ch.local:
        raise RuntimeError("the chain clock can only be advanced on Anvil; on a public chain the window is waited out")
    now = ch.now()
    if target <= now:
        return 0
    secs = target - now
    if os.environ.get("REALTIME") == "1":
        log(f"   [REALTIME: waiting {_days(secs)} for the {window}]")
        while ch.now() < target:
            time.sleep(min(5, max(1, target - ch.now())))
            ch.w3.provider.make_request("evm_mine", [])
        return 0
    ch.w3.provider.make_request("evm_setNextBlockTimestamp", [target])
    ch.w3.provider.make_request("evm_mine", [])
    if ch.now() < target:
        raise RuntimeError(f"chain clock did not advance to {target}")
    SKIPPED[window] = SKIPPED.get(window, 0) + secs
    log(f"   [SIM: chain clock advanced {_days(secs)} — {window}]")
    return secs


class Determinism:
    """Pinned-clock demo on Anvil only: the observers' nonces come from a generator seeded with the deployment and
    ML-DSA-44 signs in FIPS 204's deterministic variant, through the observer's opt-in parameters (`Observer.attest(nonce=,
    pq_deterministic=)`); the library defaults (random nonces, hedged ML-DSA) stay for every other caller."""

    def __init__(self, chain_id: int, seed: str, on: bool = True):
        if on and chain_id != 31337:
            raise RuntimeError("deterministic nonces and signatures are for the local Anvil demo only")
        self.on = on
        self.rng = random.Random(hashlib.sha256(seed.encode()).digest())

    def kw(self) -> dict:
        return {"nonce": self.rng.getrandbits(64), "pq_deterministic": True} if self.on else {}


def step(msg: str) -> None:
    print(f"\n== {msg}")


def check(cond: bool, msg: str) -> None:
    global ok_count
    if not cond:
        print(f"   FAIL  {msg}")
        sys.exit(1)
    ok_count += 1
    print(f"   ok    {msg}")


def file_sha256(p: Path) -> str:
    return "0x" + hashlib.sha256(p.read_bytes()).hexdigest()


def covenant_policy(facility: dict, buyer: str, core: dict = POLICY, extra: dict | None = None):
    """The main covenant's committed policy (spec §3): bond from model/bond.py, start backdated to 2026-01-01 [SIM].
    `core` replaces POLICY (testnet.py multiyear: POLICY_MULTIYEAR), `extra` adds committed fields.
    Returns (bond_wei, sizing, policy, policyHash, createCovenant params). Shared with observer/testnet.py."""
    ss_path = DERIVED / "sourceset_v2.json"
    sizing_path = DERIVED / "model" / "bond_sizing.json"
    if sizing_path.exists():  # bond = P90 annual rebate exposure of the facility set (model/bond.py, spec §5)
        sz = json.loads(sizing_path.read_text())
        bond = int(sz["recommended_bond_wei"])
        sizing = {"method": "P90 annual rebate exposure", "forecaster": sz["forecaster_used"],
                  "decision": sz["decision"], "file": "data/derived/model/bond_sizing.json",
                  "fileSha256": file_sha256(sizing_path)}
        model_hash = sz["modelHash"] if sz["forecaster_used"] == "model" else None
    else:
        bond, sizing, model_hash = FALLBACK_BOND_WEI, {"method": "fixed fallback"}, None
    policy = {**core, "bondWei": str(bond), "bondSizing": sizing, "modelHash": model_hash,
              "sourceSetRoot": facility["root"], "sourceSetSha256": file_sha256(ss_path),
              "joinVersionHash": file_sha256(DERIVED / "join_v1.json"),
              "snapshotManifestHash": file_sha256(POLICY_SNAPSHOT), **(extra or {})}
    policy_hash = evidence_hash(policy)
    tiers = list(TIER_AMOUNTS_WEI)
    params = (buyer, attest.b32(facility["root"]), attest.b32(policy_hash), core["term"], core["start"], core["k"],
              core["challengeWindow"], core["contestWindow"], core["linkWindow"], core["minRateKgH"], tiers)
    return bond, sizing, policy, policy_hash, params


def main() -> None:
    rpc = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
    live = os.environ.get("LIVE") == "1"
    ch = Chain(rpc)
    if ch.chain_id != 31337:
        sys.exit("demo.py replays chain time and uses Anvil test keys: Anvil (31337) only. Sepolia: scripts/judge.sh")
    seller_pk, buyer_pk, proposer_pk = anvil_key(1), anvil_key(2), anvil_key(6)
    seller, buyer, proposer = (Account.from_key(k).address for k in (seller_pk, buyer_pk, proposer_pk))
    facility, _, case_file = load_facility(CASE)
    plume = next(p for p in case_file["cm_plumes_within_radius"] if p["plume_id"] == PLUME)
    run: dict = {"chainId": ch.chain_id, "deployment": ch.dep, "case": CASE, "plume": PLUME,
                 "mode": "live" if live else "snapshot", "clock": {"pinned": bool(os.environ.get("DEMO_CLOCK_PINNED")),
                                                                    "genesis": ch.w3.eth.get_block(0)["timestamp"]}}
    det = Determinism(ch.chain_id, "MethaneClause demo [SIM] " + json.dumps(ch.dep, sort_keys=True),
                      on=run["clock"]["pinned"])

    print(f"   KeyRegistry {ch.reg.address}  MethaneCovenant {ch.cov.address}  (registry admin renounced: "
          f"{ch.reg.functions.admin().call() == '0x' + '0' * 40})")

    # -------------------------------------------------------------- covenant
    step(f"Covenant: seller [SIM] {seller[:10]}… -> buyer [SIM] {buyer[:10]}…, start backdated to 2026-01-01 [SIM]")
    bond, sizing, policy, policy_hash, params = covenant_policy(facility, buyer)
    tiers = list(TIER_AMOUNTS_WEI)
    rcpt, _ = ch.send(ch.cov.functions.createCovenant(params), seller_pk, bond, "createCovenant")
    check(rcpt.status == 1, f"createCovenant + bond {Web3.from_wei(bond, 'ether')} ETH "
          f"({sizing['method']}{', ' + sizing['forecaster'] if 'forecaster' in sizing else ''})")
    cid_int = ch.cov.functions.covenantCount().call()
    cid = "0x" + cid_int.to_bytes(32, "big").hex()
    c = ch.cov.functions.getCovenant(attest.b32(cid)).call()
    start, created, remaining0 = c[1], c[6], c[-4]
    published = iso_to_unix(plume["published_at"])
    print(f"   covenant {cid_int}, policyHash {policy_hash[:18]}…, facility root {facility['root'][:18]}…")
    check(start == COVENANT_START and start < published < created,
          f"start {start} (2026-01-01) < plume published {published} < createdAt {created}: backdating is on-chain"
          + ("  [SIM: chain clock pinned to a fixed genesis, one second per block; see scripts/demo.sh]"
             if os.environ.get("DEMO_CLOCK_PINNED") else ""))
    run.update(covenantId=cid, policy=policy, policyHash=policy_hash, covenantStart=start, createdAt=created)
    now = ch.now()

    # -------------------------------------------------------------- observers
    step(f"Observers fetch ({'LIVE Carbon Mapper API' if live else 'committed snapshot'}), validate, hash, sign "
         "[SIM: all three observer keys are the author's; k-of-n is a mechanism demo]")
    obs = {o: Observer(o, ch.chain_id, ch.cov.address, live=live) for o in ("OBS_A", "OBS_B", "OBS_C")}
    kw = {"covenant_id": cid, "covenant_start": start, "now": now, "min_rate_kgh": POLICY["minRateKgH"]}
    sa = obs["OBS_A"].attest(CASE, PLUME, **kw, **det.kw())
    sb = obs["OBS_B"].attest(CASE, PLUME, **kw, **det.kw())
    for s in (sa, sb):
        print(f"   {s['org']} {s['signer'][:10]}… evidenceHash {s['evidenceHash'][:18]}… "
              f"rate {s['bundle']['emissionKgH']} ± {s['bundle']['uncertaintyKgH']} kg/h")
    check(sa["evidenceHash"] == sb["evidenceHash"],
          "independent observer fetches built the identical canonical bundle" if live else
          "both observers built the identical canonical bundle [SIM snapshot: same committed file; LIVE=1 for "
          "independent fetches]")
    on_chain = ch.cov.functions.attestationDigest(attest.as_tuple(sa["attestation"])).call()
    check(bytes(on_chain) == attest.digest(sa["attestation"], ch.chain_id, ch.cov.address),
          "Python EIP-712 digest == contract attestationDigest")
    hybrid_ok = True
    run["registeredKeys"] = {}  # what the KeyRegistry holds, read on chain: the page verifies against this record
    for x in (sa, sb):
        k = ch.reg.functions.getKey(x["signer"]).call()  # (orgId, role, algId, validFrom, validUntil, pqHash, ...)
        # address from the deployment record (Deploy.s.sol), algId + pqPubKeyHash from the registry itself
        run["registeredKeys"][x["org"]] = {"address": ch.dep[f"observer{x['org'][-1]}"], "algId": k[2],
                                          "pqPubKeyHash": "0x" + bytes(k[5]).hex(), "registry": ch.reg.address,
                                          "readAtBlock": ch.w3.eth.block_number}
        m = x.get("mldsa44")  # the anchor is the registry's hash of the presented key; the bundle's pkHash is not used
        hybrid_ok &= bool(m) and k[2] == pq.ALG_HYBRID and pq.pk_hash(bytes.fromhex(m["publicKey"][2:])) == (
            "0x" + bytes(k[5]).hex()) and pq.verify(
            bytes.fromhex(m["publicKey"][2:]), bundle_bytes(x["bundle"]), bytes.fromhex(m["sig"][2:]))
    check(hybrid_ok, "hybrid: both keys registered as algId 2; ML-DSA-44 (FIPS 204) over the same canonical bytes "
          "verifies against the anchored SHA-256(pk)")
    run["hybrid"] = [{**{k: x[k] for k in ("org", "signer", "signature", "bundle", "evidenceHash", "mldsa44")},
                      "attestation": {k: ("0x" + v.hex() if isinstance(v, bytes) else v)
                                      for k, v in x["attestation"].items()}} for x in (sa, sb)]
    proof = [attest.b32(p) for p in facility["proof"]]
    sid = attest.b32(facility["sourceId"])

    def propose(atts: list[dict], sigs: list[str], ev: str, expect: str | None = None, label: str | None = None):
        fn = ch.cov.functions.proposeEpisode(attest.b32(cid), sid, proof, attest.b32(ev),
                                             [attest.as_tuple(a) for a in atts], [bytes.fromhex(s[2:]) for s in sigs])
        return ch.send(fn, proposer_pk, ch.dep["proposerBond"], label, expect)

    def remaining() -> int:
        return ch.cov.functions.getCovenant(attest.b32(cid)).call()[-4]

    def state() -> tuple:
        """Everything a rejected proposal must leave untouched: bond, episode count, OBS_A's nonce bit."""
        return (remaining(), ch.cov.functions.episodeCount().call(),
                ch.cov.functions.isNonceUsed(sa["signer"], sa["attestation"]["nonce"]).call())

    def unchanged(before: tuple, what: str) -> None:
        after = state()
        check(after == before, f"{what}: bond, episode count and nonce bitmap unchanged {before}")

    # -------------------------------------------------------------- rejection 1: tamper
    step("Tamper: edit the bundle 2,548 -> 548 kg/h, keep the observers' signatures")
    tb = copy.deepcopy(sa["bundle"])
    tb["emissionKgH"] = 548
    tev = bundle_hash(tb)
    print(f"   committed {sa['evidenceHash'][:18]}…  tampered {tev[:18]}…  (hash changed)")
    tatts = []
    for s in (sa, sb):
        a = dict(s["attestation"])
        a.update(evidenceHash=attest.b32(tev), emissionKgH=548)
        tatts.append(a)
    before = state()
    rcpt, why = propose(tatts, [sa["signature"], sb["signature"]], tev, expect="InvalidSignature")
    check(rcpt.status == 0 and why == "InvalidSignature", f"tampered episode reverted on-chain: {why}")
    unchanged(before, "after tamper")
    run["tamper"] = {"tx": rcpt.transactionHash.hex(), "reason": why, "evidenceHash": tev}

    # -------------------------------------------------------------- rejection 2: seller self-attestation
    step("Self-attestation: the seller's key signs the real bundle together with OBS_A")
    sat = dict(sb["attestation"], nonce=sb["attestation"]["nonce"] ^ 1)
    ssig = "0x" + attest.sign(sat, seller_pk, ch.chain_id, ch.cov.address).hex()
    before = state()
    rcpt, why = propose([sa["attestation"], sat], [sa["signature"], ssig], sa["evidenceHash"],
                        expect="SelfAttestation")
    check(rcpt.status == 0 and why == "SelfAttestation", f"seller attestation reverted: {why}")
    unchanged(before, "after self-attestation")

    # -------------------------------------------------------------- rejection 3: observers disagree
    step("Disagreement [SIM]: OBS_C's fetch shows the plume re-quantified (2,548 -> 2,610 kg/h)")
    sc = obs["OBS_C"].attest(CASE, PLUME, **kw, **det.kw(), override={"emission_auto": 2610.0})
    before = state()
    rcpt, why = propose([sa["attestation"], sc["attestation"]], [sa["signature"], sc["signature"]],
                        sa["evidenceHash"], expect="AttestationMismatch")
    check(rcpt.status == 0 and why == "AttestationMismatch", f"batch with a dissenting observer reverted: {why}")
    unchanged(before, "after disagreement")

    step("Episode keyed by a Carbon Mapper twin source instead of the facility")
    cm_sid = Web3.keccak(text=plume["source_name"])
    fn = ch.cov.functions.proposeEpisode(attest.b32(cid), cm_sid, proof, attest.b32(sa["evidenceHash"]),
                                         [attest.as_tuple(sa["attestation"]), attest.as_tuple(sb["attestation"])],
                                         [bytes.fromhex(x["signature"][2:]) for x in (sa, sb)])
    before = state()
    rcpt, why = ch.send(fn, proposer_pk, ch.dep["proposerBond"], expect_revert="NotCovered")
    check(rcpt.status == 0 and why == "NotCovered", f"{plume['source_name']} is not a leaf: {why}")
    unchanged(before, "after uncovered source")

    step("Replay: the same attestation twice in one batch")
    before = state()
    rcpt, why = propose([sa["attestation"], sa["attestation"]], [sa["signature"], sa["signature"]],
                        sa["evidenceHash"], expect="Replay")
    check(rcpt.status == 0 and why == "Replay", f"reused nonce reverted: {why}")
    unchanged(before, "after replay")
    check(remaining() == remaining0 and before[1] == 0, "five rejections: bond untouched, no episode exists")

    # -------------------------------------------------------------- the real proposal
    step("Real 2-of-3 proposal (OBS_A + OBS_B)")
    rcpt, _ = propose([sa["attestation"], sb["attestation"]], [sa["signature"], sb["signature"]],
                      sa["evidenceHash"], label="proposeEpisode")
    check(rcpt.status == 1, "proposeEpisode accepted")
    eid = ch.cov.functions.episodeCount().call()
    e = ch.cov.functions.getEpisode(eid).call()
    state, tier, rebate = STATES[e[13]], e[11], e[16]
    check(state == "PROPOSED", f"episode {eid} PROPOSED, tier {tier}, rebate held {Web3.from_wei(rebate, 'ether')} ETH")
    check(tier == 2 and rebate == tiers[1], "2,548 kg/h -> tier 2 (1.5-5 t/h)")
    run["proposal"] = {"tx": rcpt.transactionHash.hex(), "episodeId": eid, "evidenceHash": sa["evidenceHash"],
                       "bundle": sa["bundle"], "attestors": [sa["signer"], sb["signer"]]}

    step("Seller records a mitigation claim")
    rcpt, _ = ch.send(ch.cov.functions.recordMitigationClaim(attest.b32(cid), sid,
                                                            Web3.keccak(text="[SIM] valve replaced")), seller_pk)
    check(rcpt.status == 1 and STATES[ch.cov.functions.getEpisode(eid).call()[13]] == "PROPOSED",
          "claim recorded; episode state unchanged (non-detection is never repair)")

    step("Finalize before the window closes")
    rcpt, why = ch.send(ch.cov.functions.finalize(eid), buyer_pk, expect_revert="WindowOpen")
    check(rcpt.status == 0 and why == "WindowOpen", f"early finalize reverted: {why}")

    # -------------------------------------------------------------- window -> confirm -> pay
    advance_clock(ch, e[9] + 1, "challenge window")
    step(f"Challenge window passed (chain time {ch.now()}); anyone finalizes")
    rcpt, _ = ch.send(ch.cov.functions.finalize(eid), buyer_pk, label="finalize")
    check(rcpt.status == 1 and STATES[ch.cov.functions.getEpisode(eid).call()[13]] == "CONFIRMED",
          "episode CONFIRMED")
    due = ch.cov.functions.withdrawable(buyer).call()
    check(due == rebate, f"buyer can withdraw {Web3.from_wei(due, 'ether')} ETH")
    before = ch.w3.eth.get_balance(buyer)
    rcpt, _ = ch.send(ch.cov.functions.withdraw(), buyer_pk, label="withdraw")
    fee = rcpt.gasUsed * rcpt.effectiveGasPrice
    check(ch.w3.eth.get_balance(buyer) - before + fee == rebate, "buyer received the rebate")
    due_p = ch.cov.functions.withdrawable(proposer).call()
    rcpt, _ = ch.send(ch.cov.functions.withdraw(), proposer_pk)
    check(rcpt.status == 1 and due_p == ch.dep["proposerBond"], "proposer bond returned in full")

    c = ch.cov.functions.getCovenant(attest.b32(cid)).call()
    funded, rem, held, paid, refunded = c[-5:]
    check(funded == paid + held + rem + refunded, f"bond conservation: funded {funded} = paid {paid} + "
          f"held {held} + remaining {rem} + refunded {refunded}")
    check(ch.w3.eth.get_balance(ch.cov.address) == rem + held, "contract balance == remaining + held")
    paid_after = paid

    # -------------------------------------------------------------- replay / idempotent resubmission
    step("Resubmit the identical, already-confirmed proposal (RPC retry)")
    rcpt, why = propose([sa["attestation"], sb["attestation"]], [sa["signature"], sb["signature"]],
                        sa["evidenceHash"], expect="DuplicateEvidence")
    check(rcpt.status == 0 and why == "DuplicateEvidence" and ch.cov.functions.episodeCount().call() == eid,
          f"idempotent: {why}, episodeCount still {eid}")

    # -------------------------------------------------------------- revoked key, then a linked detection
    now = ch.now()
    kw["now"] = now
    step("OBS_B's org revokes its key; OBS_B then attests the 2026-06-20 plume")
    rcpt, _ = ch.send(ch.reg.functions.revokeKey(obs["OBS_B"].address), anvil_key(4))
    check(rcpt.status == 1, "key revoked on-chain by the org controller (no admin exists any more)")
    sa2 = obs["OBS_A"].attest(CASE, PLUME_LINKED, **kw, **det.kw())
    sb2 = obs["OBS_B"].attest(CASE, PLUME_LINKED, **kw, **det.kw())
    rcpt, why = propose([sa2["attestation"], sb2["attestation"]], [sa2["signature"], sb2["signature"]],
                        sa2["evidenceHash"], expect="RevokedKey")
    check(rcpt.status == 0 and why == "RevokedKey", f"revoked observer rejected: {why}")

    step(f"OBS_A + OBS_C attest the 2026-06-20 plume ({sa2['bundle']['emissionKgH']} kg/h), 9 days after the paid "
         "episode")
    sc2 = obs["OBS_C"].attest(CASE, PLUME_LINKED, **kw, **det.kw())
    check(sa2["evidenceHash"] == sc2["evidenceHash"], "OBS_A and OBS_C agree on the bundle")
    rcpt, _ = propose([sa2["attestation"], sc2["attestation"]], [sa2["signature"], sc2["signature"]],
                      sa2["evidenceHash"], label="proposeEpisode_linked")
    eid2 = ch.cov.functions.episodeCount().call()
    e2 = ch.cov.functions.getEpisode(eid2).call()
    check(rcpt.status == 1 and STATES[e2[13]] == "LINKED" and e2[15] == eid,
          f"episode {eid2} LINKED to episode {eid}: inside the 30-day link window, recorded, not paid")
    c = ch.cov.functions.getCovenant(attest.b32(cid)).call()
    check(c[-2] == paid_after and c[-3] == 0, "no second payout; nothing held")
    run["linked"] = {"tx": rcpt.transactionHash.hex(), "episodeId": eid2, "parent": eid,
                     "evidenceHash": sa2["evidenceHash"]}

    run.update(gas=ch.gas, observerReceipts=[r for o in obs.values() for r in o.receipts], checks=ok_count,
               skippedSeconds=dict(SKIPPED))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "run.json").write_text(json.dumps(run, indent=1, sort_keys=True, default=str) + "\n")
    # Committed, audited summary of the local loop. Gas is rounded to 1k: random nonces land in cold or warm bitmap
    # words, which moves proposeEpisode by a few dozen gas between runs.
    summary = {"chain": "anvil-31337", "checks": ok_count, "mode": run["mode"],
               "gas_rounded_1k": {k: round(v, -3) for k, v in ch.gas.items()},
               "bond_eth": float(Web3.from_wei(bond, "ether")), "rebate_eth": float(Web3.from_wei(rebate, "ether")),
               "evidenceHash": sa["evidenceHash"], "linkedEvidenceHash": sa2["evidenceHash"]}
    (DERIVED / "demo_run.json").write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n")
    print(f"\nDEMO OK: {ok_count} checks passed. gas {ch.gas}. Log: out/demo/run.json")


# ============================================================================ DZA replay (spec §9)
# Runs first in `make demo`, on its own Anvil whose genesis is the replay covenant's start (2024-09-01 [SIM clock]), so
# v1's 33 checks keep their own chain and stay byte-identical (spec-v2 §10 alternative; recorded in the report). Two
# covenants over the same real DZA_S_012 data show both outcomes of the cancel beat:
#   A  real: the pipeline's no-look-ahead qualified passes before the 2026-02-01 relapse (the count is read from the case
#      file, never typed); the relapse cancels a claim with nothing pending (installments 1 and 2 released, the final
#      40 % never scheduled -> forfeited)
#   B  [SIM look-ahead]: the passes an observer would have counted with today's origin points (the pipeline's
#      `qualifiedAllOriginsToday`); the count reaches the final installment's threshold, MARS corroboration schedules it,
#      and the relapse cancels it while PENDING (its reserve is freed)
REPLAY_CASE = "DZA_S_012"
SUBMITTER_INDEX, SPONSOR_INDEX, DEPLOYER_INDEX, SURETY_INDEX = 1, 8, 0, 9  # seller [SIM] submits passes; sponsor [SIM]; Anvil deployer
POOL_LIBS_NOTE = "linked libraries deployed by this replay; addresses in out/demo/replay.json"


def _artifact(name: str) -> dict:
    return json.loads((ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json").read_text())


def _create(ch, pk: str, data: str, label: str) -> str:
    acct = Account.from_key(pk)
    tx = {"from": acct.address, "nonce": ch.nonce(acct.address), "chainId": ch.chain_id, "value": 0, "data": data}
    tx["gas"] = ch.w3.eth.estimate_gas({k: tx[k] for k in ("from", "data", "value")}) * 6 // 5
    rcpt, _ = ch._sign_send(tx, acct, label, None)
    if rcpt.status != 1 or not rcpt.contractAddress:
        raise RuntimeError(f"{label} failed")
    return rcpt.contractAddress


def deploy_pool(ch, pk: str) -> dict:
    """IncentivePool(covenant) with its linked libraries, from the forge artifacts: each library is deployed and its
    address written over the artifact's link placeholders. Returns {"IncentivePool": addr, <library>: addr, ...}."""
    from eth_abi import encode

    art = _artifact("IncentivePool")
    code = art["bytecode"]["object"].removeprefix("0x")
    out: dict[str, str] = {}
    for libs in art["bytecode"]["linkReferences"].values():
        for lib, places in sorted(libs.items()):
            la = _artifact(lib)
            if la["bytecode"]["linkReferences"]:
                raise RuntimeError(f"{lib} links further libraries; not supported here")
            out[lib] = _create(ch, pk, la["bytecode"]["object"], f"deploy {lib}")
            for pl in places:
                a, n = pl["start"] * 2, pl["length"] * 2
                code = code[:a] + out[lib][2:].lower() + code[a + n:]
    if "__$" in code:
        raise RuntimeError("unlinked library placeholder left in IncentivePool")
    out["IncentivePool"] = _create(ch, pk, "0x" + code + encode(["address"], [ch.cov.address]).hex(),
                                   "deploy IncentivePool")
    return out


def _events(contract, name: str, rcpt) -> list[dict]:
    from web3.logs import DISCARD

    return [dict(e["args"]) for e in getattr(contract.events, name)().process_receipt(rcpt, errors=DISCARD)]


def _pct(bp: int) -> str:
    return f"{bp / 100:g} %"


def replay_main() -> None:
    """The DZA replay; writes out/demo/replay.json. Every step is a real transaction; every rejection has receipt status 0."""
    import heapq

    import policy as policy_mod
    from observer import cleanpass
    from observer.chain import _named, abi as load_abi

    global ok_count
    ok_count = 0
    SKIPPED.clear()
    rpc = os.environ.get("RPC_URL", "http://127.0.0.1:8545")
    ch = Chain(rpc)
    if ch.chain_id != 31337:
        sys.exit("the DZA replay moves the chain clock and uses Anvil test keys: Anvil (31337) only")
    pinned = bool(os.environ.get("DEMO_CLOCK_PINNED"))
    det = Determinism(ch.chain_id, "MethaneClause DZA replay [SIM] " + json.dumps(ch.dep, sort_keys=True), on=pinned)
    POL = policy_mod.POLICY_V2_REPLAY
    core, pp = POL["core"], POL["pool"]
    pol_v2 = json.loads((DERIVED / "policy_v2.json").read_text())["policies"]["POLICY_V2_REPLAY"]
    seller_pk, buyer_pk, proposer_pk = anvil_key(SUBMITTER_INDEX), anvil_key(2), anvil_key(6)
    sponsor_pk = anvil_key(SPONSOR_INDEX)
    seller, buyer, sponsor = (Account.from_key(k).address for k in (seller_pk, buyer_pk, sponsor_pk))
    doc = cleanpass.load_case(REPLAY_CASE)
    summ = doc["summary"]
    facility, _, case_file = load_facility(REPLAY_CASE)
    run: dict = {"chainId": ch.chain_id, "case": REPLAY_CASE, "policy": "POLICY_V2_REPLAY",
                 "policyHash": pol_v2["policyHash"], "onchainParamsHash": pol_v2["onchainParamsHash"],
                 "clock": {"pinned": pinned, "genesis": ch.w3.eth.get_block(0)["timestamp"]}, "steps": []}

    def note(msg: str) -> None:
        print(msg)
        run["steps"].append({"t": ch.now(), "note": msg})

    # ---------------------------------------------------------------- the data the replay uses (the pipeline's files)
    plume_id = doc["episode"]["plumeId"]
    relapse_name = summ["firstPlumeAfterSince"]["sceneName"]
    relapse_t = cleanpass.clean_passes.ts(summ["firstPlumeAfterSince"]["sceneAtIso"])
    relapse_plume = max((p for p in case_file["cm_plumes_within_radius"]
                         if p["plume_id"].rsplit("-", 1)[0] == relapse_name and p.get("emission_auto") is not None),
                        key=lambda p: p["emission_auto"])
    before = [x for x in doc["scenes"] if doc["since"] < x["sceneAt"] < relapse_t]
    passes_a = [x for x in before if x["qualified"]]
    passes_b = [x for x in before if x["qualifiedAllOriginsToday"]]
    after = [x for x in doc["scenes"] if x["qualified"] and x["sceneAt"] > relapse_t]
    bad = [x for x in before if x["refusal"] in ("CLOUD_FLAG", "CLOUD_FLAG_MISMATCH") and x["notCloudy"] is False][-1]
    lag, clean_w = pp["publicationLag"], pp["cleanWindow"]

    def attestable(x: dict) -> int:  # the pipeline's one formula (publication lag run and scene record published)
        from cleanpass_rules import attestable_from
        return attestable_from(x["sceneAt"], x["publishedAt"], lag)

    step(f"DZA replay [SIM clock: Anvil genesis {datetime.fromtimestamp(run['clock']['genesis'], timezone.utc):%Y-%m-%d}]"
         f" — the pipeline's case file {REPLAY_CASE}: episode {plume_id}, relapse scene {relapse_name}")
    check(len(passes_a) == summ["qualifiedBeforeFirstPlume"],
          f"A: {len(passes_a)} qualified clean passes before the relapse = the case file's count "
          f"({summ['statusLabel']})")
    check(len(passes_b) == summ["qualifiedBeforeFirstPlumeAllOriginsToday"],
          f"B [SIM look-ahead]: {len(passes_b)} passes would be counted if today's origin points had been known = the "
          f"case file's qualifiedBeforeFirstPlumeAllOriginsToday")

    # ---------------------------------------------------------------- deploy the pool with its libraries
    step("Deploy IncentivePool and its linked libraries on the replay chain (the v1 core is the same Deploy.s.sol)")
    addrs = deploy_pool(ch, anvil_key(DEPLOYER_INDEX))
    run["pool"] = addrs
    pool = ch.w3.eth.contract(address=addrs["IncentivePool"], abi=load_abi("IncentivePool"))
    for name in ["IncentivePool"] + [k for k in addrs if k != "IncentivePool"]:
        for e in load_abi(name):
            if e["type"] == "error":
                ch.errors[Web3.to_hex(Web3.keccak(text=Chain._sig(e)))[:10]] = e["name"]
    check(pool.functions.covenant().call() == ch.cov.address and pool.functions.registry().call() == ch.reg.address,
          f"pool {addrs['IncentivePool'][:10]}… reads the core covenant and trusts exactly its registry; libraries "
          + ", ".join(f"{k} {v[:10]}…" for k, v in addrs.items() if k != "IncentivePool"))
    step("SuretyBond: a surety [SIM] posts covenant A's bond; settlement and refunds are assigned to it on chain")
    from eth_abi import encode as abi_encode
    surety_pk = anvil_key(SURETY_INDEX)
    surety = Account.from_key(surety_pk).address
    sb_art = _artifact("SuretyBond")
    bond_addr = _create(ch, anvil_key(DEPLOYER_INDEX), sb_art["bytecode"]["object"] + abi_encode(
        ["address", "address", "address", "address"], [ch.cov.address, pool.address, seller, surety]).hex(),
        "deploy SuretyBond")
    sbond = ch.w3.eth.contract(address=bond_addr, abi=load_abi("SuretyBond"))
    for e in load_abi("SuretyBond"):
        if e["type"] == "error":
            ch.errors[Web3.to_hex(Web3.keccak(text=Chain._sig(e)))[:10]] = e["name"]
    seller_org = ch.reg.functions.orgOf(seller).call()
    rcpt, _ = ch.send(ch.reg.functions.registerKey(seller_org, 2, bond_addr, 1, bytes(32)), seller_pk, 0,
                      "replay A: registerKey (SuretyBond as the seller org's SELLER key)")
    check(rcpt.status == 1 and sbond.functions.operator().call() == seller and sbond.functions.funder().call() == surety
          and ch.reg.functions.orgOf(bond_addr).call() == seller_org,
          f"SuretyBond {bond_addr[:10]}…: operator = the seller [SIM], funder = the surety [SIM] {surety[:10]}…; "
          "registered by the seller org's controller as that org's SELLER key (it never signs)")
    run["suretyBond"] = {"address": bond_addr, "operator": seller, "funder": surety}

    claim_t = lambda cid: _named(pool.functions.getClaim(cid).abi["outputs"], pool.functions.getClaim(cid).call())  # noqa: E731
    inst_t = lambda iid: _named(pool.functions.getInstallment(iid).abi["outputs"], pool.functions.getInstallment(iid).call())  # noqa: E731

    # ---------------------------------------------------------------- covenants (A real, B [SIM look-ahead])
    tiers = [int(x) for x in core["rebateSchedule"]["tierAmountsWei"]]
    bond, sizing, _, _, _ = covenant_policy(facility, buyer)
    variants = {}
    for key, label, scenes in (("A", "real: no-look-ahead passes", passes_a),
                               ("B", "[SIM look-ahead]: today's origin points", passes_b)):
        cpol = {**core, "bondWei": str(bond), "bondSizing": sizing, "poolPolicyHash": pol_v2["policyHash"],
                "sourceSetRoot": facility["root"], "variant": key}
        cph = evidence_hash(cpol)
        params = (buyer, attest.b32(facility["root"]), attest.b32(cph), core["term"], core["start"], core["k"],
                  core["challengeWindow"], core["contestWindow"], core["linkWindow"], core["minRateKgH"], tiers)
        if key == "A":  # through the SuretyBond: the operator creates, the surety funds the core directly
            rcpt, _ = ch.send(sbond.functions.createCovenant(params), seller_pk, 0, "replay A: createCovenant (SuretyBond)")
            made = _events(sbond, "CovenantCreatedThroughBond", rcpt)
            cid = "0x" + bytes(made[0]["covenantId"]).hex()
            rcpt2, _ = ch.send(ch.cov.functions.fund(attest.b32(cid)), surety_pk, bond, "replay A: fund (surety)")
            c = ch.covenant(attest.b32(cid))
            check(rcpt.status == 1 and rcpt2.status == 1 and c["seller"] == bond_addr and c["remaining"] == bond,
                  f"A: covenant {int(cid, 16)} created by the operator through the SuretyBond (its seller is the bond "
                  f"contract); the surety [SIM] posted the {Web3.from_wei(bond, 'ether')} ETH bond on the core")
            r0, why = ch.send(sbond.functions.withdraw(), seller_pk, 0, "replay A: operator tries SuretyBond.withdraw",
                              expect_revert="NotFunder")
            check(r0.status == 0 and why == "NotFunder",
                  f"the operator cannot move the surety's money: SuretyBond.withdraw reverts on chain ({why})")
        else:
            rcpt, _ = ch.send(ch.cov.functions.createCovenant(params), seller_pk, bond, f"replay {key}: createCovenant")
            cid = "0x" + ch.cov.functions.covenantCount().call().to_bytes(32, "big").hex()
        c = ch.covenant(attest.b32(cid))
        check(rcpt.status == 1 and c["start"] == core["start"],
              f"{key} ({label}): covenant {int(cid, 16)} start {core['start']} = 2024-09-01 [SIM clock], bond "
              f"{Web3.from_wei(bond, 'ether')} ETH, term {core['term'] // 86400} days")
        variants[key] = {"key": key, "tag": "A" if key == "A" else "B [SIM look-ahead]", "label": label, "cid": cid, "scenes": scenes, "inst": {}, "obs": {
            o: Observer(o, ch.chain_id, ch.cov.address) for o in ("OBS_A", "OBS_B")}}

    # ---------------------------------------------------------------- scheduler: the clock only moves forward
    queue: list = []
    seq = [0]

    def at(t: int, window: str, fn, *args) -> None:
        seq[0] += 1
        heapq.heappush(queue, (t, seq[0], window, fn, args))

    proof = [attest.b32(x) for x in facility["proof"]]
    sid = attest.b32(facility["sourceId"])
    trace_shown = [False]

    def propose_detection(v: dict, pid: str, what: str) -> int:
        kw = {"covenant_id": v["cid"], "covenant_start": core["start"], "now": ch.now(),
              "min_rate_kgh": core["minRateKgH"]}
        sa, sb = (v["obs"][o].attest(REPLAY_CASE, pid, **kw, **det.kw()) for o in ("OBS_A", "OBS_B"))
        fn = ch.cov.functions.proposeEpisode(attest.b32(v["cid"]), sid, proof, attest.b32(sa["evidenceHash"]),
                                             [attest.as_tuple(sa["attestation"]), attest.as_tuple(sb["attestation"])],
                                             [bytes.fromhex(x["signature"][2:]) for x in (sa, sb)])
        rcpt, _ = ch.send(fn, proposer_pk, ch.dep["proposerBond"], f"replay {v['key']}: proposeEpisode ({what})")
        eid = ch.cov.functions.episodeCount().call()
        e = ch.episode(eid)
        check(rcpt.status == 1 and e["stateName"] == "PROPOSED",
              f"{v['tag']}: {what} {pid} {sa['bundle']['emissionKgH']} kg/h -> episode {eid} PROPOSED, tier {e['tier']}")
        at(e["deadline"] + 1, "challenge window", finalize_episode, v, eid, what)
        return eid

    def finalize_episode(v: dict, eid: int, what: str) -> None:
        rcpt, _ = ch.send(ch.cov.functions.finalize(eid), buyer_pk, 0, f"replay {v['key']}: finalize ({what})")
        e = ch.episode(eid)
        check(rcpt.status == 1 and e["stateName"] == "CONFIRMED",
              f"{v['tag']}: episode {eid} CONFIRMED; rebate {Web3.from_wei(e['rebate'], 'ether')} ETH to the buyer [SIM]")
        if what == "episode":
            v["eid"] = eid
            open_pool_and_claim(v, e)
        else:
            cancel(v, eid)

    def open_pool_and_claim(v: dict, e: dict) -> None:
        cap = e["rebate"]
        # observer fees: the pool pays observerFeeWei per counted pass, split among the k signing orgs' controllers;
        # the sponsor funds it for every pass this claim (and, for A, the post-relapse claim) will count [SIM]
        passes = len(v["scenes"]) + (len(after) if v["key"] == "A" else 0)
        topup = int(pp["observerFeeWei"]) * passes
        rcpt, _ = ch.send(pool.functions.fund(attest.b32(v["cid"]), attest.b32(pol_v2["policyHash"]), 0,
                                              policy_mod.pool_params(POL)), buyer_pk, cap,
                          f"replay {v['key']}: fund (buyer opens)")
        funded = _events(pool, "PoolFunded", rcpt)
        check(rcpt.status == 1 and funded and "0x" + bytes(funded[0]["paramsHash"]).hex() == pol_v2["onchainParamsHash"],
              f"{v['tag']}: pool opened by the buyer [SIM] with the episode's rebate {Web3.from_wei(cap, 'ether')} ETH; "
              f"paramsHash = POLICY_V2_REPLAY onchainParamsHash")
        rcpt, _ = ch.send(pool.functions.fund(attest.b32(v["cid"]), attest.b32(pol_v2["policyHash"]), 0,
                                              policy_mod.pool_params(POL)), sponsor_pk, topup,
                          f"replay {v['key']}: fund (sponsor top-up)")
        check(rcpt.status == 1 and pool.functions.contributions(attest.b32(v["cid"]), sponsor).call() == topup,
              f"{v['tag']}: sponsor [SIM] top-up {Web3.from_wei(topup, 'ether')} ETH = observer fee x {passes} passes "
              f"(the pool splits each pass's fee among the {core['k']} signing orgs)")
        v["openWei"], v["topUpWei"] = cap, topup
        rcpt, _ = ch.send(pool.functions.openClaim(v["eid"]), sponsor_pk, 0, f"replay {v['key']}: openClaim")
        v["claim"] = "0x" + bytes(_events(pool, "ClaimOpened", rcpt)[0]["claimId"]).hex()
        c = claim_t(attest.b32(v["claim"]))
        v["cap"] = c["cap"]
        check(rcpt.status == 1 and c["since"] == doc["since"],
              f"{v['tag']}: claim {v['claim'][:12]}… since {c['since']} = detection + 30-day link window "
              f"(= the case file's since); cap {Web3.from_wei(c['cap'], 'ether')} ETH")
        for x in v["scenes"]:
            at(attestable(x) + 1, "publication lag", attest_pass, v, x)
        if v["key"] == "A":
            at(attestable(bad) + 1, "publication lag", bad_pass, v)

    def signed_pass(v: dict, x: dict, sim_bundle: dict | None = None, kind: int | None = None) -> tuple:
        """k = 2 orgs sign; the real path goes through the pipeline's observer (five steps, refusals), [SIM] paths sign a
        bundle the observer would refuse."""
        if sim_bundle is None:
            import contextlib
            import io

            outs = []
            for o in ("OBS_A", "OBS_B"):
                trace = io.StringIO()
                with contextlib.redirect_stdout(trace):  # the observer's five-step trace: printed in full once
                    outs.append(cleanpass.CleanPassObserver(o, ch.chain_id, pool.address).attest(
                        REPLAY_CASE, x["sceneProviderId"], covenant_id=v["cid"], claim_id=v["claim"], now=ch.now(),
                        **det.kw()))
                if not trace_shown[0]:
                    print(trace.getvalue().rstrip())
                    trace_shown[0] = o == "OBS_B"
            return [o["message"] for o in outs], [o["signature"] for o in outs]
        msgs, sigs = [], []
        for o in ("OBS_A", "OBS_B"):
            m = cleanpass.message(sim_bundle, nonce=det.kw().get("nonce", int.from_bytes(os.urandom(8), "big")),
                                  expiry=ch.now() + 3600)
            if kind is not None:
                m["kind"] = kind
            msgs.append(m)
            sigs.append("0x" + attest.pool_sign("CleanPass", m, observer_key(o, ch.chain_id), ch.chain_id,
                                                pool.address).hex())
        return msgs, sigs

    def attest_pass(v: dict, x: dict) -> None:
        if v["key"] == "B" and not x["qualified"]:
            b, _, _ = cleanpass.bundle_for(REPLAY_CASE, x["sceneProviderId"], covenant_id=v["cid"], claim_id=v["claim"])
            msgs, sigs = signed_pass(v, x, sim_bundle=b)
            tag = " [SIM look-ahead: signed although the observer refuses it today, " + x["refusal"] + "]"
        else:
            msgs, sigs = signed_pass(v, x)
            tag = ""
        rcpt, _ = ch.send(pool.functions.attestCleanPass(attest.b32(v["claim"]),
                                                         [cleanpass_tuple(m) for m in msgs],
                                                         [bytes.fromhex(s_[2:]) for s_ in sigs]),
                          seller_pk, int(pp["passBondWei"]), f"replay {v['key']}: attestCleanPass")
        pid = _events(pool, "CleanPassAttested", rcpt)[0]["passId"]
        check(rcpt.status == 1, f"{v['tag']}: pass {pid} attested — {x['sceneName']} ({x['instrument']}), "
                                f"OBS_A + OBS_B{tag}")
        at(ch.now() + clean_w + 1, "clean window", count_pass, v, pid)

    def count_pass(v: dict, pid: int) -> None:
        rcpt, _ = ch.send(pool.functions.countPass(pid), seller_pk, 0, f"replay {v['key']}: countPass")
        cnt = _events(pool, "CleanPassCounted", rcpt)
        fees = _events(pool, "ObserverFeePaid", rcpt)
        what = ("qualified clean passes" if v["key"] == "A" else "passes counted [SIM look-ahead]")
        v["fees"] = v.get("fees", 0) + sum(f["amount"] for f in fees)
        check(rcpt.status == 1 and cnt, f"{v['tag']}: pass {pid} counted -> {cnt[0]['counted']} {what}, u(N) "
                                        f"{cnt[0]['upperBoundBp'] / 100:g} %; observer fee split among "
                                        f"{len(fees)} org controllers")
        schedule_installments(v, rcpt)
        if cnt[0]["counted"] == len(v["scenes"]) and not v.get("corroborated") and not v.get("newClaim"):
            v["corroborated"] = True  # every pass of the run is counted: MARS against the negative, once
            at(ch.now() + 1, "after the last count", corroborate, v)

    def schedule_installments(v: dict, rcpt) -> None:
        for it in _events(pool, "InstallmentScheduled", rcpt):
            v["inst"][it["index"]] = it["installmentId"]
            note(f"   installment {it['index'] + 1} scheduled: {Web3.from_wei(it['amount'], 'ether')} ETH, "
                 f"claimable after the hold window ({datetime.fromtimestamp(it['claimableAt'], timezone.utc):%Y-%m-%d})")
            at(it["claimableAt"] + 1, "hold window", release_inst, v, it["installmentId"])

    def release_inst(v: dict, iid: int) -> None:
        it = inst_t(iid)
        if it["state"] != 2:  # PENDING
            note(f"   {v['tag']}: installment {it['index'] + 1} is no longer pending (state {it['state']}): "
                 "nothing to release")
            return
        rcpt, _ = ch.send(pool.functions.release(iid), seller_pk, 0, f"replay {v['key']}: release")
        check(rcpt.status == 1 and inst_t(iid)["state"] == 3,
              f"{v['tag']}: installment {it['index'] + 1} RELEASED, {Web3.from_wei(it['amount'], 'ether')} ETH "
              "credited to the seller [SIM]")

    def corroborate(v: dict) -> None:
        cnt = claim_t(attest.b32(v["claim"]))["counted"]
        window_to = max(x["sceneAt"] for x in v["scenes"])
        c = cleanpass.build_corroboration(doc, claim_id=v["claim"], window_to=window_to)
        outs = [cleanpass.sign_corroboration(c, org=o, chain_id=ch.chain_id, pool=pool.address,
                                             nonce=det.kw().get("nonce"), now=ch.now(), expiry=ch.now() + 3600)
                for o in ("OBS_A", "OBS_B")]
        rcpt, _ = ch.send(pool.functions.attestCorroboration(
            attest.b32(v["claim"]), [corroboration_tuple(o["message"]) for o in outs],
            [bytes.fromhex(o["signature"][2:]) for o in outs]), seller_pk, 0, f"replay {v['key']}: attestCorroboration")
        a3b = any(e.get("name") == "finalizeCorroboration" for e in pool.abi)  # A3b: pending, effective after cleanWindow
        check(rcpt.status == 1, f"{v['tag']}: MARS corroboration over {cnt} counted passes — UNEP MARS snapshot shows "
                                f"{c['marsPlumesInWindow']} plumes at the facility in the window (other satellites)"
                                + ("; pending and challengeable for the clean window" if a3b else ""))
        schedule_installments(v, rcpt)
        if a3b:
            at(ch.now() + clean_w + 1, "clean window", finalize_corroboration, v)

    def finalize_corroboration(v: dict) -> None:
        rcpt, _ = ch.send(pool.functions.finalizeCorroboration(attest.b32(v["claim"])), seller_pk, 0,
                          f"replay {v['key']}: finalizeCorroboration")
        check(rcpt.status == 1, f"{v['tag']}: corroboration effective after the clean window")
        schedule_installments(v, rcpt)

    def bad_pass(v: dict) -> None:
        b, _, _ = cleanpass.bundle_for(REPLAY_CASE, bad["sceneProviderId"], covenant_id=v["cid"], claim_id=v["claim"])
        b = {**b, "notCloudy": True}  # [SIM]: the provider's flag is false
        msgs, sigs = signed_pass(v, bad, sim_bundle=b)
        counted0 = claim_t(attest.b32(v["claim"]))["counted"]
        rcpt, _ = ch.send(pool.functions.attestCleanPass(attest.b32(v["claim"]), [cleanpass_tuple(m) for m in msgs],
                                                         [bytes.fromhex(s_[2:]) for s_ in sigs]),
                          seller_pk, int(pp["passBondWei"]), f"replay {v['key']}: attestCleanPass (seeded bad pass)")
        pid = _events(pool, "CleanPassAttested", rcpt)[0]["passId"]
        check(rcpt.status == 1, f"{v['tag']}: seeded bad pass [SIM] {pid} on {bad['sceneName']}: observers signed "
                                "notCloudy = true although Carbon Mapper's flag is false — the signatures are valid, "
                                "so the chain accepts it as PENDING")
        rcpt, _ = ch.send(pool.functions.challengePass(pid, pool.functions.REASON_CLOUD_FLAG().call()), buyer_pk,
                          int(pp["challengerBondWei"]), f"replay {v['key']}: challengePass CLOUD_FLAG")
        check(rcpt.status == 1, f"{v['tag']}: the buyer [SIM] challenges it CLOUD_FLAG inside the clean window: "
                                "re-checkable against the scene record (not_cloudy = false)")
        msgs, sigs = signed_pass(v, bad, sim_bundle=b, kind=attest.KIND_WITHDRAW_CLEAN)
        due0 = pool.functions.withdrawable(buyer).call()
        rcpt, _ = ch.send(pool.functions.resolvePass(pid, [cleanpass_tuple(m) for m in msgs],
                                                     [bytes.fromhex(s_[2:]) for s_ in sigs]),
                          buyer_pk, 0, f"replay {v['key']}: resolvePass WITHDRAW_CLEAN")
        ps = _named(pool.functions.getPass(pid).abi["outputs"], pool.functions.getPass(pid).call())
        credited = pool.functions.withdrawable(buyer).call() - due0
        check(credited == int(pp["passBondWei"]) + int(pp["challengerBondWei"]),
              f"{v['tag']}: the challenger [SIM] is credited {Web3.from_wei(credited, 'ether')} ETH = the pass bond + "
              "its own challenger bond back")
        check(rcpt.status == 1 and ps["state"] == 4 and claim_t(attest.b32(v["claim"]))["counted"] == counted0,
              f"{v['tag']}: observers sign WITHDRAW_CLEAN -> pass {pid} VOID, both bonds to the challenger, count "
              f"unchanged at {counted0}")

    def relapse_published(v: dict) -> None:
        v["eid2"] = propose_detection(v, relapse_plume["plume_id"], "relapse")
        if v["key"] == "A":
            fn = pool.functions.relapse(attest.b32(v["claim"]), v["eid2"])
            rcpt, why = ch.send(fn, sponsor_pk, 0, "replay A: relapse while the episode is PROPOSED",
                                expect_revert="SourceUnderReview")
            check(rcpt.status == 0 and why == "SourceUnderReview",
                  f"A: cancelling on a live episode reverts on chain: {why} (it may still end VOID); the claim is "
                  "frozen while the relapse is under review")
            note("   A: no installment is pending while the relapse is live (1 and 2 were released before it); the "
                 "release freeze shows on B's pending final below")
        elif 2 in v["inst"] and inst_t(v["inst"][2])["state"] == 2:
            fn = pool.functions.release(v["inst"][2])
            why = ch.revert_reason(fn, seller)
            if why in ("SourceUnderReview", "RelapsePending"):
                rcpt, why = ch.send(fn, seller_pk, 0, "replay B: release while the relapse is PROPOSED",
                                    expect_revert=why)
                check(rcpt.status == 0 and why == "SourceUnderReview",
                      f"{v['tag']}: releasing the pending final while the relapse is under review reverts on chain: "
                      f"{why} — the claim is frozen")
            else:
                note(f"   {v['tag']}: release of the pending final would revert {why} today (the contract checks "
                     "claimableAt before the source-under-review rule); the freeze appears here as a status-0 release "
                     "once release checks the source-under-review rule before claimableAt")

    def cancel(v: dict, eid2: int) -> None:
        c0 = claim_t(attest.b32(v["claim"]))
        rcpt, _ = ch.send(pool.functions.relapse(attest.b32(v["claim"]), eid2), sponsor_pk, 0,
                          f"replay {v['key']}: relapse (cancel)")
        ev = _events(pool, "ClaimCancelled", rcpt)
        c = claim_t(attest.b32(v["claim"]))
        shares = {i: r[1] for i, r in enumerate(pp["installments"])}
        paid_bp = c["paid"] * 10_000 // v["cap"]
        check(rcpt.status == 1 and ev and c["state"] == 2,
              f"{v['tag']}: relapse() cancels the claim (CONFIRMED episode {eid2}); released installments stay "
              f"released: paid {_pct(paid_bp)} of the cap, forfeited {_pct(10_000 - paid_bp)}")
        released_bp = sum(pp["installments"][inst_t(i)["index"]][1] for i in v["inst"].values() if inst_t(i)["state"] == 3)
        check(paid_bp == released_bp and c["paid"] == v["cap"] * released_bp // 10_000,
              f"{v['tag']}: paid {_pct(paid_bp)} = the shares of the released installments ({_pct(released_bp)})")
        freed = ev[0]["releasedReserve"]
        if v["key"] == "A":
            check(freed == 0 and 2 not in v["inst"],
                  f"A: nothing was pending — the final {_pct(shares[2])} was never scheduled ({c0['counted']} passes "
                  f"< {pp['installments'][2][0]}); the relapse frees no reserve")
            v["outcome"] = {"counted": c0["counted"], "paidBp": paid_bp, "forfeitedBp": 10_000 - paid_bp,
                            "releasedReserveWei": str(freed), "pendingCancelled": False}
            at(ch.now() + 1, "after the cancel", new_claim, v, eid2)
        else:
            fin = inst_t(v["inst"][2]) if 2 in v["inst"] else None
            check(fin is not None and fin["state"] == 4 and freed == fin["amount"],
                  f"B [SIM look-ahead]: the final installment was PENDING ({Web3.from_wei(freed, 'ether')} ETH reserved) and is "
                  "VOID now; its reserve is freed for the sponsors")
            v["outcome"] = {"counted": c0["counted"], "paidBp": paid_bp, "forfeitedBp": 10_000 - paid_bp,
                            "releasedReserveWei": str(freed), "pendingCancelled": True}

    def new_claim(v: dict, eid2: int) -> None:
        v["firstClaim"] = v["claim"]
        rcpt, _ = ch.send(pool.functions.openClaim(eid2), sponsor_pk, 0, "replay A: openClaim (relapse episode)")
        v["claim"] = "0x" + bytes(_events(pool, "ClaimOpened", rcpt)[0]["claimId"]).hex()
        c = claim_t(attest.b32(v["claim"]))
        check(rcpt.status == 1, f"A: a new claim opens on the relapse episode, since "
                                f"{datetime.fromtimestamp(c['since'], timezone.utc):%Y-%m-%d}, cap "
                                f"{Web3.from_wei(c['cap'], 'ether')} ETH")
        v["scenes"], v["newClaim"] = after, True
        for x in after:
            at(attestable(x) + 1, "publication lag", attest_pass, v, x)

    for v in variants.values():
        at(iso_to_unix(next(p for p in case_file["cm_plumes_within_radius"] if p["plume_id"] == plume_id)
                       ["published_at"]) + 1, "publication of the detection", lambda v_: (
            v_.update(eid=propose_detection(v_, plume_id, "episode"))), v)
        at(iso_to_unix(relapse_plume["published_at"]) + 1, "publication of the relapse", relapse_published, v)

    while queue:
        t, _, window, fn, args = heapq.heappop(queue)
        advance_clock(ch, t, window)
        fn(*args)

    a_v, b_v = variants["A"], variants["B"]
    for v in variants.values():
        n_counted = len(v["scenes"]) if v["key"] == "B" else summ["qualifiedBeforeFirstPlume"] + len(after)
        check(v.get("fees") == int(pp["observerFeeWei"]) * n_counted,
              f"{v['tag']}: observer fees paid = fee x {n_counted} counted passes "
              f"({Web3.from_wei(v.get('fees', 0), 'ether')} ETH, split among the signing orgs' controllers)")
    final = claim_t(attest.b32(a_v["claim"]))
    check(final["counted"] == len(after), f"A: the new claim counted {final['counted']} qualified clean pass after the "
                                          "relapse (the case file's post-relapse pass)")
    step("Settlement through the SuretyBond: the core's remaining bond and the pool's cure refunds go to the surety")
    a_cov = ch.covenant(attest.b32(a_v["cid"]))
    advance_clock(ch, a_cov["end"] + core["challengeWindow"] + 1, "covenant term")
    rcpt, _ = ch.send(ch.cov.functions.settle(attest.b32(a_v["cid"])), sponsor_pk, 0, "replay A: settle")
    settled = _events(ch.cov, "CovenantSettled", rcpt)
    refund = settled[0]["refund"] if settled else None
    check(rcpt.status == 1 and refund == a_cov["remaining"],
          f"A: covenant settled after its term; the remaining bond {Web3.from_wei(refund or 0, 'ether')} ETH is "
          "credited to its seller, the SuretyBond")
    pool_due = pool.functions.withdrawable(bond_addr).call()
    rcpt, _ = ch.send(sbond.functions.pull(), sponsor_pk, 0, "replay A: SuretyBond.pull")
    pulled = _events(sbond, "Pulled", rcpt)
    paid_a = v_paid = variants["A"]["cap"] * a_v["outcome"]["paidBp"] // 10_000
    check(rcpt.status == 1 and pulled and pulled[0]["fromCovenant"] == refund and pulled[0]["fromPool"] == pool_due
          and pool_due >= v_paid,
          f"anyone pulls the bond contract's credits home: {Web3.from_wei(refund, 'ether')} ETH from the core + "
          f"{Web3.from_wei(pool_due, 'ether')} ETH from the pool (the released cure refunds, "
          f"{Web3.from_wei(paid_a, 'ether')} ETH)")
    before = ch.w3.eth.get_balance(surety)
    rcpt, _ = ch.send(sbond.functions.withdraw(), surety_pk, 0, "replay A: SuretyBond.withdraw (funder)")
    gained = ch.w3.eth.get_balance(surety) - before + rcpt.gasUsed * rcpt.effectiveGasPrice
    check(rcpt.status == 1 and gained == refund + pool_due,
          f"the surety [SIM] withdraws {Web3.from_wei(gained, 'ether')} ETH: settlement and refunds landed with the funder")
    run["suretyBond"].update(settleRefundWei=str(refund), pulledFromPoolWei=str(pool_due), withdrawnWei=str(gained))

    for who, pk in (("seller [SIM]", seller_pk), ("buyer [SIM]", buyer_pk)):
        due = pool.functions.withdrawable(Account.from_key(pk).address).call()
        if due:
            rcpt, _ = ch.send(pool.functions.withdraw(), pk, 0, f"replay: pool withdraw ({who})")
            check(rcpt.status == 1, f"{who} withdraws {Web3.from_wei(due, 'ether')} ETH from the pool (pull payment)")

    for v in variants.values():  # the sponsor's ledger, read back from the pool once settlement is done
        pl = pool.functions.getPool(attest.b32(v["cid"])).call()[0]  # Pool: state, mode, policyHash, paramsHash, balance, reserved, opener
        claims = [claim_t(attest.b32(c)) for c in (v.get("firstClaim"), v["claim"]) if c]
        v["sponsorLedger"] = {
            "openedByBuyerWei": str(v["openWei"]), "sponsorTopUpWei": str(v["topUpWei"]),
            "buyerContributionWei": str(pool.functions.contributions(attest.b32(v["cid"]), buyer).call()),
            "sponsorContributionWei": str(pool.functions.contributions(attest.b32(v["cid"]), sponsor).call()),
            "balanceWei": str(pl[4]), "reservedWei": str(pl[5]), "poolState": int(pl[0]),
            "releasedWei": str(sum(c["paid"] for c in claims)),
            "forfeitedWei": str(claims[0]["cap"] - claims[0]["paid"]), "observerFeesWei": str(v["fees"]),
            "sponsorWithdrawn": False}
        check(pl[4] >= pl[5] and int(v["sponsorLedger"]["sponsorContributionWei"]) == v["topUpWei"],
              f"{v['tag']}: pool read back — balance {Web3.from_wei(pl[4], 'ether')} ETH, reserved "
              f"{Web3.from_wei(pl[5], 'ether')} ETH, released {Web3.from_wei(sum(c['paid'] for c in claims), 'ether')} ETH, "
              f"observer fees {Web3.from_wei(v['fees'], 'ether')} ETH; the sponsor's top-up stays on record until "
              "sponsorWithdraw after the pool closes")

    statuses = [x["status"] for x in ch.txlog]
    run.update(variants={k: {kk: vv for kk, vv in v.items() if kk not in ("obs", "scenes")} | {
        "scenes": [x["sceneName"] for x in v["scenes"]]} for k, v in variants.items()},
        skippedSeconds=dict(SKIPPED), checks=ok_count, txs=len(statuses), status0=statuses.count(0),
        txlog=[{k: t[k] for k in t if k != "seconds"} for t in ch.txlog], gas=ch.gas)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "replay.json").write_text(json.dumps(run, indent=1, sort_keys=True, default=str) + "\n")
    print(f"\nREPLAY OK: {ok_count} checks, {len(statuses)} transactions ({statuses.count(0)} status 0), "
          f"{_days(sum(SKIPPED.values()))} skipped [SIM clock]. A paid {_pct(a_v['outcome']['paidBp'])} / forfeited "
          f"{_pct(a_v['outcome']['forfeitedBp'])}; B [SIM look-ahead] cancelled a pending final. Log: out/demo/replay.json")


# ============================================================================ TKM verdict-only covenant (spec-v2 §5, C4)
# Runs after v1's 33 checks on the same chain: a Türkiye-type importer [SIM] cannot be paid in crypto-assets (CBRT
# 2021), so the covenant has tier amounts 0 and no bond: CONFIRMED *is* the verdict, settled off chain under the supply
# contract by a credit note (the pipeline's export/credit_note.py). Its VERDICT_ONLY pool holds no ETH; amounts are policy
# units. Supersession of a credit note is an off-chain export step; no on-chain supersession is shown.
def verdict_main() -> None:
    import policy as policy_mod
    from export import credit_note as cn
    from observer.chain import _named, abi as load_abi

    global ok_count
    ok_count = 0
    SKIPPED.clear()
    ch = Chain(os.environ.get("RPC_URL", "http://127.0.0.1:8545"))
    if ch.chain_id != 31337:
        sys.exit("the verdict-only run moves the chain clock and uses Anvil test keys: Anvil (31337) only")
    det = Determinism(ch.chain_id, "MethaneClause verdict-only [SIM] " + json.dumps(ch.dep, sort_keys=True),
                      on=bool(os.environ.get("DEMO_CLOCK_PINNED")))
    POL = policy_mod.POLICY_V2_VERDICT_ONLY
    pol_v2 = json.loads((DERIVED / "policy_v2.json").read_text())["policies"]["POLICY_V2_VERDICT_ONLY"]
    seller_pk, buyer_pk, proposer_pk = anvil_key(1), anvil_key(2), anvil_key(6)
    buyer = Account.from_key(buyer_pk).address
    facility, _, case_file = load_facility(CASE)
    out: dict = {"chainId": ch.chain_id, "case": CASE, "policy": "POLICY_V2_VERDICT_ONLY",
                 "poolPolicyHash": pol_v2["policyHash"], "steps": []}

    step("TKM verdict-only covenant [SIM: Türkiye-type importer]: tier amounts 0, no bond — the verdict settles off "
         "chain by credit note")
    addrs = deploy_pool(ch, anvil_key(DEPLOYER_INDEX))
    pool = ch.w3.eth.contract(address=addrs["IncentivePool"], abi=load_abi("IncentivePool"))
    for name in addrs:
        for e in load_abi(name):
            if e["type"] == "error":
                ch.errors[Web3.to_hex(Web3.keccak(text=Chain._sig(e)))[:10]] = e["name"]
    out["pool"] = addrs
    core = POL["core"]
    cpol = {**core, "rebateSchedule": {**core["rebateSchedule"], "tierAmountsWei": ["0", "0", "0"]},
            "settlement": "VERDICT_ONLY", "poolPolicyHash": pol_v2["policyHash"], "sourceSetRoot": facility["root"]}
    params = (buyer, attest.b32(facility["root"]), attest.b32(evidence_hash(cpol)), core["term"], COVENANT_START,
              core["k"], core["challengeWindow"], core["contestWindow"], core["linkWindow"], core["minRateKgH"],
              [0, 0, 0])
    rcpt, _ = ch.send(ch.cov.functions.createCovenant(params), seller_pk, 0, "verdict: createCovenant")
    cid = "0x" + ch.cov.functions.covenantCount().call().to_bytes(32, "big").hex()
    c = ch.covenant(attest.b32(cid))
    check(rcpt.status == 1 and c["remaining"] == 0 and list(c["tierAmounts"]) == [0, 0, 0],
          f"covenant {int(cid, 16)}: tier amounts [0, 0, 0], no bond posted — accepted by the unchanged core")

    step("VERDICT_ONLY pool: opened by the buyer [SIM] with no ETH; it refuses ETH")
    fund = pool.functions.fund(attest.b32(cid), attest.b32(pol_v2["policyHash"]), 1, policy_mod.pool_params(POL))
    rcpt, why = ch.send(fund, buyer_pk, 1, "verdict: fund with ETH", expect_revert="BadParams")
    check(rcpt.status == 0 and why == "BadParams", f"a verdict-only pool funded with ETH reverts on chain: {why}")
    rcpt, _ = ch.send(fund, buyer_pk, 0, "verdict: fund (buyer opens, no ETH)")
    ev = _events(pool, "PoolFunded", rcpt)
    check(rcpt.status == 1 and ev and "0x" + bytes(ev[0]["paramsHash"]).hex() == pol_v2["onchainParamsHash"],
          "pool opened in VERDICT_ONLY mode; paramsHash = POLICY_V2_VERDICT_ONLY onchainParamsHash")

    step("Observers attest the 2026-06-11 plume (OBS_A + OBS_C; OBS_B's key was revoked in the v1 run)")
    kw = {"covenant_id": cid, "covenant_start": COVENANT_START, "now": ch.now(), "min_rate_kgh": core["minRateKgH"]}
    obs = [Observer(o, ch.chain_id, ch.cov.address) for o in ("OBS_A", "OBS_C")]
    sa, sc = (o.attest(CASE, PLUME, **kw, **det.kw()) for o in obs)
    fn = ch.cov.functions.proposeEpisode(attest.b32(cid), attest.b32(facility["sourceId"]),
                                         [attest.b32(x) for x in facility["proof"]], attest.b32(sa["evidenceHash"]),
                                         [attest.as_tuple(x["attestation"]) for x in (sa, sc)],
                                         [bytes.fromhex(x["signature"][2:]) for x in (sa, sc)])
    rcpt, _ = ch.send(fn, proposer_pk, ch.dep["proposerBond"], "verdict: proposeEpisode")
    eid = ch.cov.functions.episodeCount().call()
    e = ch.episode(eid)
    check(rcpt.status == 1 and e["stateName"] == "PROPOSED" and e["rebate"] == 0,
          f"episode {eid} PROPOSED, tier {e['tier']}, rebate held 0 (nothing to hold)")
    advance_clock(ch, e["deadline"] + 1, "challenge window")
    due0 = ch.cov.functions.withdrawable(buyer).call()
    rcpt, _ = ch.send(ch.cov.functions.finalize(eid), buyer_pk, 0, "verdict: finalize")
    e = ch.episode(eid)
    check(rcpt.status == 1 and e["stateName"] == "CONFIRMED" and ch.cov.functions.withdrawable(buyer).call() == due0,
          f"episode {eid} CONFIRMED — the verdict; no ETH moves to the buyer")
    finalize_tx = "0x" + bytes(rcpt.transactionHash).hex()
    finalize_time = ch.w3.eth.get_block(rcpt.blockNumber)["timestamp"]  # the verdict's time: the receipt the note cites

    step("The seller [SIM] records its Annex C claim by hash (the pipeline's export): recorded, changes no state")
    from export import annex_c
    form = annex_c.from_json((DERIVED / "export" / f"{CASE}.annex_c.example.json").read_text())
    claim_hash = annex_c.claim_hash(form)
    rcpt, _ = ch.send(ch.cov.functions.recordMitigationClaim(attest.b32(cid), attest.b32(facility["sourceId"]),
                                                             attest.b32(claim_hash)), seller_pk, 0,
                      "verdict: recordMitigationClaim (Annex C)")
    logged = _events(ch.cov, "MitigationClaimRecorded", rcpt)
    on_chain = "0x" + bytes(logged[0]["claimHash"]).hex() if logged else None
    check(rcpt.status == 1 and on_chain == claim_hash and ch.episode(eid)["stateName"] == "CONFIRMED",
          f"Annex C claimHash {claim_hash[:18]}… recorded by the seller [SIM]; MitigationClaimRecorded read back from "
          "the chain carries the same hash; the verdict stays CONFIRMED (a claim is not evidence that the emission "
          "ceased)")
    out["annexC"] = {"claimHash": claim_hash, "tx": "0x" + bytes(rcpt.transactionHash).hex()}

    rcpt, _ = ch.send(pool.functions.openClaim(eid), buyer_pk, 0, "verdict: openClaim")
    claim = "0x" + bytes(_events(pool, "ClaimOpened", rcpt)[0]["claimId"]).hex()
    cl = _named(pool.functions.getClaim(attest.b32(claim)).abi["outputs"], pool.functions.getClaim(attest.b32(claim)).call())
    check(rcpt.status == 1 and cl["cap"] == POL["pool"]["unitTierAmounts"][e["tier"] - 1],
          f"refund claim opened in policy units: cap {cl['cap']} (x100) for tier {e['tier']}; TKM has no attestable "
          "clean pass, so nothing is scheduled")

    step("Credit note (the pipeline's export): the verdict settles under the supply contract, not on chain")
    verdict = {"chain": "Anvil (local) [SIM]", "chainId": ch.chain_id, "covenantContract": ch.cov.address,
               "covenantId": cid, "episodeId": eid, "state": e["stateName"], "tier": e["tier"], "tx": finalize_tx,
               "blockTime": finalize_time}
    note = cn.rebate_note(verdict, case=CASE)
    reg = cn.register(CASE, [note])
    text, csv_text = json.dumps(reg, indent=1, ensure_ascii=False) + "\n", cn.to_csv(reg)
    check(note["amount"]["units"] == cl["cap"],
          f"credit note {note['amount']['display']} (seller [SIM] -> buyer [SIM]) = the claim's cap; ref "
          f"{note['ref'][:18]}… derived off chain from (chainId, covenant, episode)")
    check(cn.from_json(text) == reg and cn.from_csv(csv_text) == reg,
          "register round trip: JSON -> import and CSV -> import give the same register")
    edited = csv_text.replace(f",{note['amount']['units']},{note['amount']['display']},", ",30,0.30 policy units,", 1)
    try:
        cn.from_csv(edited)
        refused = False
    except ValueError:
        refused = True
    check(edited != csv_text and refused, "an edit to the CSV's visible amount is refused on import")
    check(note["supersession"] is None and note["status"] == "due",
          "the note is due; supersession would be an off-chain export step (no on-chain event exists for it)")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{CASE}.credit_note.json").write_text(text)
    (OUT / f"{CASE}.credit_note.csv").write_text(csv_text)
    statuses = [x["status"] for x in ch.txlog]
    out.update(covenantId=cid, episodeId=eid, claimId=claim, finalizeTx=finalize_tx, finalizeTime=finalize_time,
               units=note["amount"]["units"],
               noteRef=note["ref"], noteHash=note["noteHash"], skippedSeconds=dict(SKIPPED), checks=ok_count,
               txs=len(statuses), status0=statuses.count(0),
               txlog=[{k: t[k] for k in t if k != "seconds"} for t in ch.txlog])
    (OUT / "verdict.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(f"\nVERDICT-ONLY OK: {ok_count} checks, {len(statuses)} transactions ({statuses.count(0)} status 0); credit "
          f"note {note['amount']['display']} -> out/demo/{CASE}.credit_note.{{json,csv}}")


# ============================================================================ portfolio covenant (spec-v2 §7, C4)
# One covenant over the pipeline's portfolio set v3 (every join candidate + the three cases, one Merkle root); bond and pool
# from model/bond_sizing_v3.json [SIM, testnet-sized]. Mechanics only: the set's stake band and backtest totals are
# not quoted here while their adversarial review runs.
def portfolio_main() -> None:
    import policy as policy_mod
    from observer.chain import _named, abi as load_abi

    global ok_count
    ok_count = 0
    SKIPPED.clear()
    ch = Chain(os.environ.get("RPC_URL", "http://127.0.0.1:8545"))
    if ch.chain_id != 31337:
        sys.exit("the portfolio run moves the chain clock and uses Anvil test keys: Anvil (31337) only")
    det = Determinism(ch.chain_id, "MethaneClause portfolio [SIM] " + json.dumps(ch.dep, sort_keys=True),
                      on=bool(os.environ.get("DEMO_CLOCK_PINNED")))
    ss_path, sz_path = DERIVED / "sourceset_v3.json", DERIVED / "model" / "bond_sizing_v3.json"
    ss, sz = json.loads(ss_path.read_text()), json.loads(sz_path.read_text())
    leaves = {x["source_name"]: x for x in ss["sources"]}
    pol_v2 = json.loads((DERIVED / "policy_v2.json").read_text())["policies"]["POLICY_V2"]
    POL = policy_mod.POLICY_V2
    seller_pk, buyer_pk, proposer_pk = anvil_key(1), anvil_key(2), anvil_key(6)
    buyer = Account.from_key(buyer_pk).address
    facility_v2, _, _ = load_facility(CASE)
    out: dict = {"chainId": ch.chain_id, "sourceset": "data/derived/sourceset_v3.json", "root": ss["root"], "n": ss["n"]}

    step(f"Portfolio covenant [SIM]: one covenant over {ss['n']} facilities (portfolio set v3, root {ss['root'][:12]}…), "
         "bond and pool from the set's sizing")
    check(sz["sourceset_root"] == ss["root"] and len(leaves) == ss["n"],
          f"the sizing file was computed for this set: {sz['sourceset']} root {ss['root'][:12]}…, {ss['n']} leaves")
    bond = int(sz["recommended_bond_wei"])
    core = POL["core"]
    cpol = {**core, "bondWei": str(bond), "bondSizing": {"file": "data/derived/model/bond_sizing_v3.json",
            "fileSha256": file_sha256(sz_path), "method": sz["method"], "forecaster": sz["forecaster_used"]},
            "sourceSetRoot": ss["root"], "sourceSetSha256": file_sha256(ss_path), "poolPolicyHash": pol_v2["policyHash"]}
    tiers = [int(x) for x in core["rebateSchedule"]["tierAmountsWei"]]
    params = (buyer, attest.b32(ss["root"]), attest.b32(evidence_hash(cpol)), core["term"], COVENANT_START, core["k"],
              core["challengeWindow"], core["contestWindow"], core["linkWindow"], core["minRateKgH"], tiers)
    rcpt, _ = ch.send(ch.cov.functions.createCovenant(params), seller_pk, bond, "portfolio: createCovenant")
    cid = "0x" + ch.cov.functions.covenantCount().call().to_bytes(32, "big").hex()
    c = ch.covenant(attest.b32(cid))
    check(rcpt.status == 1 and "0x" + bytes(c["sourceSetRoot"]).hex() == ss["root"] and c["remaining"] == bond,
          f"covenant {int(cid, 16)}: the set's root on chain, bond {Web3.from_wei(bond, 'ether')} ETH "
          "(P90 annual rebate exposure of the set, testnet-sized [SIM])")

    pool_addr = json.loads((OUT / "verdict.json").read_text())["pool"]["IncentivePool"]
    pool = ch.w3.eth.contract(address=pool_addr, abi=load_abi("IncentivePool"))
    for name in json.loads((OUT / "verdict.json").read_text())["pool"]:
        for e in load_abi(name):
            if e["type"] == "error":
                ch.errors[Web3.to_hex(Web3.keccak(text=Chain._sig(e)))[:10]] = e["name"]
    rcpt, _ = ch.send(pool.functions.fund(attest.b32(cid), attest.b32(pol_v2["policyHash"]), 0,
                                          policy_mod.pool_params(POL)), buyer_pk, bond, "portfolio: fund pool")
    pl, _ = pool.functions.getPool(attest.b32(cid)).call()
    check(rcpt.status == 1 and pl[4] == bond,
          f"refund pool opened by the buyer [SIM] with the same {Web3.from_wei(bond, 'ether')} ETH (a refund is capped "
          "at the rebate its episode cost)")

    step(f"An episode at one facility of the set: {CASE}, proven by its leaf's Merkle path")
    kw = {"covenant_id": cid, "covenant_start": COVENANT_START, "now": ch.now(), "min_rate_kgh": core["minRateKgH"]}
    obs = [Observer(o, ch.chain_id, ch.cov.address) for o in ("OBS_A", "OBS_C")]
    sa, sc = (o.attest(CASE, PLUME, **kw, **det.kw()) for o in obs)

    def propose(proof: list[str], label: str, expect: str | None = None):
        fn = ch.cov.functions.proposeEpisode(attest.b32(cid), attest.b32(leaves[CASE]["sourceId"]),
                                             [attest.b32(x) for x in proof], attest.b32(sa["evidenceHash"]),
                                             [attest.as_tuple(x["attestation"]) for x in (sa, sc)],
                                             [bytes.fromhex(x["signature"][2:]) for x in (sa, sc)])
        return ch.send(fn, proposer_pk, ch.dep["proposerBond"], label, expect)

    rcpt, why = propose(facility_v2["proof"], "portfolio: proposeEpisode with the v2 proof", expect="NotCovered")
    check(rcpt.status == 0 and why == "NotCovered",
          f"the {CASE} proof of the 3-facility set does not open the {ss['n']}-facility root: {why} (status 0)")
    rcpt, _ = propose(leaves[CASE]["proof"], "portfolio: proposeEpisode")
    eid = ch.cov.functions.episodeCount().call()
    e = ch.episode(eid)
    check(rcpt.status == 1 and e["stateName"] == "PROPOSED",
          f"with its {len(leaves[CASE]['proof'])}-step path in portfolio set v3: episode {eid} PROPOSED, tier {e['tier']}, "
          f"rebate held {Web3.from_wei(e['rebate'], 'ether')} ETH from the shared bond")
    advance_clock(ch, e["deadline"] + 1, "challenge window")
    rcpt, _ = ch.send(ch.cov.functions.finalize(eid), buyer_pk, 0, "portfolio: finalize")
    check(rcpt.status == 1 and ch.episode(eid)["stateName"] == "CONFIRMED",
          f"episode {eid} CONFIRMED; the rest of the bond still covers the other {ss['n'] - 1} facilities "
          f"({Web3.from_wei(ch.covenant(attest.b32(cid))['remaining'], 'ether')} ETH remaining)")
    rcpt, _ = ch.send(pool.functions.openClaim(eid), buyer_pk, 0, "portfolio: openClaim")
    claim = "0x" + bytes(_events(pool, "ClaimOpened", rcpt)[0]["claimId"]).hex()
    cl = _named(pool.functions.getClaim(attest.b32(claim)).abi["outputs"], pool.functions.getClaim(attest.b32(claim)).call())
    check(rcpt.status == 1 and cl["cap"] == e["rebate"],
          f"the facility's refund claim opens in the portfolio pool, cap = its episode's rebate "
          f"({Web3.from_wei(cl['cap'], 'ether')} ETH)")
    statuses = [x["status"] for x in ch.txlog]
    pool_wei = pool.functions.getPool(attest.b32(cid)).call()[0][4]  # Pool.balance, read back from the chain
    out.update(covenantId=cid, bondWei=str(ch.covenant(attest.b32(cid))["funded"]), poolWei=str(pool_wei),
               episodeId=eid, claimId=claim,
               proofSteps=len(leaves[CASE]["proof"]), skippedSeconds=dict(SKIPPED), checks=ok_count,
               txs=len(statuses), status0=statuses.count(0),
               txlog=[{k: t[k] for k in t if k != "seconds"} for t in ch.txlog])
    (OUT / "portfolio.json").write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + "\n")
    print(f"\nPORTFOLIO OK: {ok_count} checks, {len(statuses)} transactions ({statuses.count(0)} status 0); one covenant, "
          f"{ss['n']} facilities. Log: out/demo/portfolio.json")


if __name__ == "__main__":
    {"replay": replay_main, "verdict": verdict_main, "portfolio": portfolio_main}.get(sys.argv[1] if sys.argv[1:] else "", main)()
