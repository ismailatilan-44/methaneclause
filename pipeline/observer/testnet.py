"""The operator's run on the public testnet, against the MAIN deployment (private keys, `make testnet-run`).

Same loop as `make demo`, but nothing is simulated by moving the clock and no key is public:
  run       seller [SIM party, private key] creates + funds the covenant (start backdated to 2026-01-01 [SIM]);
            OBS_A and OBS_B fetch Carbon Mapper now and sign; four rejections are sent as real transactions and
            fail on chain (tamper -> InvalidSignature, seller key -> SelfAttestation, CM twin -> NotCovered,
            same attestation twice -> Replay), each with the bond unchanged; then the 2-of-3 proposal -> PROPOSED
  finalize  after the 1-day challenge window: anyone finalizes -> CONFIRMED; buyer and proposer withdraw
  multiyear seller [SIM] creates a second covenant over the same sources under POLICY_MULTIYEAR (three-year Term, start
            2026-01-01 [SIM]); the buyer [SIM] opens the main IncentivePool for it under POLICY_V2_MULTIYEAR; the term,
            policy hash and params hash are read back, with the last day an episode still reaches the final
            installment (pipeline/final_reach.py) -> data/derived/multiyear_run.json

Keys come from the environment (the Makefile loads the operator's .env; agents never read it):
SELLER_PK, BUYER_PK, PROPOSER_PK, OBSERVER_A_PK, OBSERVER_B_PK. All three observer orgs are the author's [SIM]: the
k-of-n is a mechanism demo, not independence. Writes data/derived/testnet_run.json (committed: tx links only).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys

from eth_account import Account
from web3 import Web3
from web3.logs import DISCARD

import txlog
from common import DERIVED
from observer import attest
from observer.bundle import bundle_hash
from observer.chain import Chain
from observer.demo import CASE, PLUME, covenant_policy
from observer.judge import utc
from observer.service import Observer, covenant_terms, load_facility

RUN = DERIVED / "testnet_run.json"
MULTIYEAR_RUN = DERIVED / "multiyear_run.json"


def env_key(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        sys.exit(f"{name} is not set (operator .env, see .env.example)")
    return v


def eth(wei: int) -> str:
    return f"{Web3.from_wei(wei, 'ether'):f}".rstrip("0").rstrip(".") + " ETH"


def check(cond: bool, msg: str) -> None:
    print(f"   {'ok   ' if cond else 'FAIL '} {msg}", flush=True)
    if not cond:
        sys.exit(1)


def run(ch: Chain) -> None:
    if ch.local and os.environ.get("TESTNET_ON_ANVIL") != "1":  # dry run only; the real run is public
        sys.exit("testnet.py is for a public testnet; on Anvil use `make demo`")
    seller_pk, proposer_pk = env_key("SELLER_PK"), env_key("PROPOSER_PK")
    buyer = Account.from_key(env_key("BUYER_PK")).address
    seller = Account.from_key(seller_pk).address
    if seller != ch.dep["seller"] or buyer != ch.dep["buyer"]:
        sys.exit("SELLER_PK / BUYER_PK do not match the registered seller / buyer in the deployment file")
    facility, _, case_file = load_facility(CASE)
    record: dict = {"network": ch.network, "chainId": ch.chain_id, "keyRegistry": ch.reg.address,
                    "methaneCovenant": ch.cov.address, "case": CASE, "plume": PLUME, "steps": []}

    def log(step: str, rcpt, **extra) -> None:
        record["steps"].append({"step": step, "status": rcpt.status, "tx": ch.tx_link(rcpt.transactionHash),
                                "gasUsed": rcpt.gasUsed, **extra})
        write_record(record)  # after every step: a later failure cannot lose what already reached the chain

    print(f"== Covenant on {ch.network}: seller [SIM] {seller} -> buyer [SIM] {buyer}")
    bond, sizing, policy, policy_hash, params = covenant_policy(facility, buyer)
    rcpt, _ = ch.send(ch.cov.functions.createCovenant(params), seller_pk, bond, "createCovenant")
    check(rcpt.status == 1, f"createCovenant + bond {eth(bond)} ({sizing['method']}): {ch.tx_link(rcpt.transactionHash)}")
    cid_b = ch.cov.events.CovenantCreated().process_receipt(rcpt, errors=DISCARD)[0]["args"]["covenantId"]
    cid = "0x" + bytes(cid_b).hex()
    c = ch.covenant(cid_b)
    check(c["start"] < c["createdAt"], f"start {utc(c['start'])} [SIM backdated] < createdAt {utc(c['createdAt'])}")
    log("createCovenant", rcpt, covenantId=cid, policyHash=policy_hash, bondWei=str(bond))
    record.update(covenantId=cid, policyHash=policy_hash, covenantStart=c["start"], createdAt=c["createdAt"])

    live = os.environ.get("LIVE", "1") == "1"
    print(f"\n== OBS_A and OBS_B fetch ({'Carbon Mapper API now' if live else 'committed snapshot'}), validate, sign")
    terms = covenant_terms(ch, cid)
    obs = {o: Observer(o, ch.chain_id, ch.cov.address, live=live) for o in ("OBS_A", "OBS_B")}
    sa, sb = (obs[o].attest(CASE, PLUME, covenant_id=cid, **terms) for o in ("OBS_A", "OBS_B"))
    check(sa["evidenceHash"] == sb["evidenceHash"], f"identical bundle {sa['evidenceHash']} "
          f"({sa['bundle']['emissionKgH']} kg/h); signers {sa['signer']}, {sb['signer']}")
    check([sa["signer"], sb["signer"]] == [ch.dep["observerA"], ch.dep["observerB"]],
          "signers are the registered OBS_A / OBS_B keys")
    record["evidenceHash"] = sa["evidenceHash"]
    record["observerReceipts"] = [s["receipt"] for s in (sa, sb)]
    write_record(record)
    sid = attest.b32(facility["sourceId"])
    proof = [attest.b32(p) for p in facility["proof"]]

    def propose_fn(atts, sigs, ev, source_id=sid):
        return ch.cov.functions.proposeEpisode(attest.b32(cid), source_id, proof, attest.b32(ev),
                                               [attest.as_tuple(a) for a in atts],
                                               [bytes.fromhex(s[2:]) if isinstance(s, str) else s for s in sigs])

    def snapshot() -> tuple:
        return (ch.covenant(cid_b)["remaining"], ch.read(ch.cov.functions.episodeCount()))

    def reject(name: str, fn, expect: str, what: str) -> None:
        before = snapshot()
        rcpt, why = ch.send(fn, proposer_pk, ch.dep["proposerBond"], f"reject_{name}", expect_revert=expect)
        check(rcpt.status == 0 and why == expect, f"{what}: reverted on chain, {why} — {ch.tx_link(rcpt.transactionHash)}")
        check(snapshot() == before, f"bond {eth(before[0])} and episode count {before[1]} unchanged")
        log(f"reject_{name}", rcpt, reason=why)

    print("\n== Four rejections, each a real transaction that fails on chain")
    tb = copy.deepcopy(sa["bundle"])
    tb["emissionKgH"] = tb["emissionKgH"] - 2000 if tb["emissionKgH"] > 2100 else tb["emissionKgH"] // 4
    tev = bundle_hash(tb)
    tatts = [dict(s["attestation"], evidenceHash=attest.b32(tev), emissionKgH=tb["emissionKgH"]) for s in (sa, sb)]
    reject("tamper", propose_fn(tatts, [sa["signature"], sb["signature"]], tev), "InvalidSignature",
           f"bundle edited {sa['bundle']['emissionKgH']} -> {tb['emissionKgH']} kg/h, signatures kept")
    sat = dict(sb["attestation"], nonce=sb["attestation"]["nonce"] ^ 1)
    ssig = attest.sign(sat, seller_pk, ch.chain_id, ch.cov.address)
    reject("self_attestation", propose_fn([sa["attestation"], sat], [sa["signature"], ssig], sa["evidenceHash"]),
           "SelfAttestation", "the seller's key signs as an observer")
    plume = next(p for p in case_file["cm_plumes_within_radius"] if p["plume_id"] == PLUME)
    reject("not_covered", propose_fn([sa["attestation"], sb["attestation"]], [sa["signature"], sb["signature"]],
                                     sa["evidenceHash"], Web3.keccak(text=plume["source_name"])),
           "NotCovered", f"episode keyed by the Carbon Mapper twin {plume['source_name']}, not the facility")
    reject("replay", propose_fn([sa["attestation"], sa["attestation"]], [sa["signature"], sa["signature"]],
                                sa["evidenceHash"]), "Replay", "the same attestation twice")

    print("\n== The real 2-of-3 proposal (OBS_A + OBS_B)")
    rcpt, _ = ch.send(propose_fn([sa["attestation"], sb["attestation"]], [sa["signature"], sb["signature"]],
                                 sa["evidenceHash"]), proposer_pk, ch.dep["proposerBond"], "proposeEpisode")
    check(rcpt.status == 1, f"proposeEpisode accepted: {ch.tx_link(rcpt.transactionHash)}")
    eid = ch.cov.events.EpisodeProposed().process_receipt(rcpt, errors=DISCARD)[0]["args"]["episodeId"]
    e = ch.episode(eid)
    check(e["stateName"] == "PROPOSED", f"episode {eid} PROPOSED, tier {e['tier']}, rebate held {eth(e['rebate'])}; "
          f"challenge window closes {utc(e['deadline'])}, then `make testnet-finalize`")
    log("proposeEpisode", rcpt, episodeId=eid, tier=e["tier"], rebateWei=str(e["rebate"]), deadline=e["deadline"])
    record["episodeId"] = eid
    save(record, ch)


def finalize(ch: Chain) -> None:
    record = json.loads(RUN.read_text())
    eid = record["episodeId"]
    e = ch.episode(eid)
    buyer_pk, proposer_pk = env_key("BUYER_PK"), env_key("PROPOSER_PK")
    if e["stateName"] == "PROPOSED":
        wait = e["deadline"] - ch.now()
        if wait > 0:
            sys.exit(f"challenge window still open until {utc(e['deadline'])} ({wait // 60} min)")
        rcpt, _ = ch.send(ch.cov.functions.finalize(eid), buyer_pk, 0, "finalize")
        check(rcpt.status == 1 and ch.episode(eid)["stateName"] == "CONFIRMED",
              f"episode {eid} CONFIRMED: {ch.tx_link(rcpt.transactionHash)}")
        record["steps"].append({"step": "finalize", "status": rcpt.status, "tx": ch.tx_link(rcpt.transactionHash),
                                "gasUsed": rcpt.gasUsed})
    for name, pk in (("buyer", buyer_pk), ("proposer", proposer_pk)):
        due = ch.read(ch.cov.functions.withdrawable(Account.from_key(pk).address))
        if due:
            rcpt, _ = ch.send(ch.cov.functions.withdraw(), pk, 0, "withdraw")
            check(rcpt.status == 1, f"{name} withdrew {eth(due)}: {ch.tx_link(rcpt.transactionHash)}")
            record["steps"].append({"step": f"withdraw_{name}", "status": rcpt.status, "amountWei": str(due),
                                    "tx": ch.tx_link(rcpt.transactionHash), "gasUsed": rcpt.gasUsed})
    c = ch.covenant(attest.b32(record["covenantId"]))
    check(c["funded"] == c["paid"] + c["held"] + c["remaining"] + c["refunded"],
          f"bond conservation: funded {eth(c['funded'])} = paid {eth(c['paid'])} + held {eth(c['held'])} + "
          f"remaining {eth(c['remaining'])}")
    save(record, ch)


def recover(ch: Chain) -> None:
    """Rebuild testnet_run.json from the chain alone (events + the proposer's transactions between the covenant and
    the proposal), e.g. after the run process died before saving. Revert reasons are re-derived by replaying each
    failed transaction with eth_call at the block before it."""
    w3 = ch.w3
    created = ch.cov.events.CovenantCreated().get_logs(from_block=w3.eth.block_number - 5_000)  # public RPCs prune older logs
    created = [c for c in created if c["args"]["seller"] == ch.dep["seller"]]
    if not created:
        sys.exit("no covenant by the registered seller")
    cev = created[0]
    cid = "0x" + bytes(cev["args"]["covenantId"]).hex()
    props = ch.cov.events.EpisodeProposed().get_logs(from_block=cev["blockNumber"],
                                                     argument_filters={"covenantId": cev["args"]["covenantId"]})
    pev = props[0]
    proposer = w3.eth.get_transaction(pev["transactionHash"])["from"]
    c = ch.covenant(cev["args"]["covenantId"])
    record = {"network": ch.network, "chainId": ch.chain_id, "keyRegistry": ch.reg.address,
              "methaneCovenant": ch.cov.address, "case": CASE, "plume": PLUME, "covenantId": cid,
              "policyHash": "0x" + bytes(cev["args"]["policyHash"]).hex(), "covenantStart": c["start"],
              "createdAt": c["createdAt"], "evidenceHash": "0x" + bytes(pev["args"]["evidenceHash"]).hex(),
              "episodeId": pev["args"]["episodeId"], "recoveredFromChain": True, "steps": []}
    rc = w3.eth.get_transaction_receipt(cev["transactionHash"])
    record["steps"].append({"step": "createCovenant", "status": rc.status, "tx": ch.tx_link(cev["transactionHash"]),
                            "gasUsed": rc.gasUsed, "covenantId": cid, "bondWei": str(c["funded"])})
    names = iter(["reject_tamper", "reject_self_attestation", "reject_not_covered", "reject_replay"])
    for bn in range(cev["blockNumber"], pev["blockNumber"] + 1):
        for tx in w3.eth.get_block(bn, full_transactions=True)["transactions"]:
            if tx["from"] != proposer or tx["to"] != ch.cov.address:
                continue
            r = w3.eth.get_transaction_receipt(tx["hash"])
            step = {"status": r.status, "tx": ch.tx_link(tx["hash"]), "gasUsed": r.gasUsed}
            if r.status == 0:
                try:
                    w3.eth.call({"from": tx["from"], "to": tx["to"], "data": tx["input"], "value": tx["value"]},
                                block_identifier=bn - 1)
                    reason = None
                except Exception as e:
                    data = getattr(e, "data", None) or (e.args[1] if len(e.args) > 1 else str(e))
                    reason = ch.errors.get(str(data)[:10], str(data)[:60])
                step.update(step=next(names), reason=reason)
            else:
                e = ch.episode(pev["args"]["episodeId"])
                step.update(step="proposeEpisode", episodeId=pev["args"]["episodeId"], tier=pev["args"]["tier"],
                            rebateWei=str(pev["args"]["rebate"]), deadline=e["deadline"])
            record["steps"].append(step)
    for s in record["steps"]:
        print(f"   {s['step']:24} status {s['status']}  {s.get('reason') or ''}  {s['tx']}")
    save(record, ch)


def multiyear(ch: Chain) -> None:
    import policy as policy_mod
    from final_reach import final_reach
    from observer.chain import abi as load_abi

    if ch.local and os.environ.get("TESTNET_ON_ANVIL") != "1":
        sys.exit("testnet.py is for a public testnet; dry run on Anvil with TESTNET_ON_ANVIL=1")
    seller_pk, buyer_pk = env_key("SELLER_PK"), env_key("BUYER_PK")
    seller, buyer = (Account.from_key(k).address for k in (seller_pk, buyer_pk))
    if seller != ch.dep["seller"] or buyer != ch.dep["buyer"]:
        sys.exit("SELLER_PK / BUYER_PK do not match the registered seller / buyer in the deployment file")
    if "incentivePool" not in ch.dep:
        sys.exit("no incentivePool in the deployment file (make deploy-pool-sepolia first)")
    out = MULTIYEAR_RUN if not ch.local else DERIVED.parent.parent / "out" / "testnet" / "multiyear_run.anvil.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    pol, core = policy_mod.POLICY_V2_MULTIYEAR, policy_mod.POLICY_MULTIYEAR
    pol_hash, reach = policy_mod.policy_hash(pol), final_reach(pol)
    record: dict = json.loads(out.read_text()) if out.exists() else {}
    if record.get("chainId") not in (None, ch.chain_id):
        sys.exit(f"{out.name} belongs to chain {record['chainId']}")
    record.update(network=ch.network, chainId=ch.chain_id, methaneCovenant=ch.cov.address,
                  incentivePool=ch.dep["incentivePool"], policy="POLICY_V2_MULTIYEAR", poolPolicyHash=pol_hash,
                  onchainParamsHash=pol["onchainParamsHash"], finalReach=reach)
    record.setdefault("steps", [])

    def log(step: str, rcpt, **extra) -> None:
        record["steps"].append({"step": step, "status": rcpt.status, "tx": ch.tx_link(rcpt.transactionHash),
                                "gasUsed": rcpt.gasUsed, **extra})
        out.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")

    done = {x["step"] for x in record["steps"] if x["status"] == 1}  # a re-run resumes after the last mined step
    facility, _, _ = load_facility(CASE)
    print(f"== Three-year covenant on {ch.network}: seller [SIM] {seller} -> buyer [SIM] {buyer}")
    if "createCovenant" not in done:
        bond, sizing, _, cpol_hash, params = covenant_policy(facility, buyer, core=core,
                                                             extra={"poolPolicyHash": pol_hash})
        # a run that died after the transaction mined but before the record was written: adopt that covenant (same
        # seller, same committed policy) instead of posting a second bond
        for i in range(ch.read(ch.cov.functions.covenantCount()), 0, -1):
            c = ch.covenant(i.to_bytes(32, "big"))
            if c["seller"] == seller and "0x" + bytes(c["policyHash"]).hex() == cpol_hash:
                sys.exit(f"covenant {i} already holds this policy but is not in {out.name}: add its covenantId and "
                         "createCovenant step there (tx from the explorer) and re-run")
        rcpt, _ = ch.send(ch.cov.functions.createCovenant(params), seller_pk, bond, "multiyear: createCovenant")
        if rcpt.status == 1:
            cid_b = ch.cov.events.CovenantCreated().process_receipt(rcpt, errors=DISCARD)[0]["args"]["covenantId"]
            record.update(covenantId="0x" + bytes(cid_b).hex(), policyHash=cpol_hash)
        log("createCovenant", rcpt, covenantId=record.get("covenantId"), policyHash=cpol_hash, bondWei=str(bond))
        check(rcpt.status == 1, f"createCovenant + bond {eth(bond)} ({sizing['method']}): "
              f"{ch.tx_link(rcpt.transactionHash)}")
    cid_b = attest.b32(record["covenantId"])
    c = ch.covenant(cid_b)
    check(c["end"] - c["start"] == core["term"] and c["start"] == core["start"],
          f"covenant {int(record['covenantId'], 16)}: term {(c['end'] - c['start']) // 86400} days, "
          f"{utc(c['start'])} [SIM backdated] -> {utc(c['end'])}")
    check("0x" + bytes(c["policyHash"]).hex() == record["policyHash"], f"policyHash on chain {record['policyHash']}")
    record.update(covenantStart=c["start"], covenantEnd=c["end"],
                  lastFinalDetection=c["start"] + reach["lastDetectionDay"] * 86400)

    pool = ch.w3.eth.contract(address=ch.dep["incentivePool"], abi=load_abi("IncentivePool"))
    for name in ("IncentivePool", "PoolVerify", "PoolSetup", "PoolEpisodes", "PoolCorroboration", "PoolInstallments"):
        for e in load_abi(name):
            if e["type"] == "error":
                ch.errors[Web3.to_hex(Web3.keccak(text=Chain._sig(e)))[:10]] = e["name"]
    fund_wei = int(os.environ.get("POOL_FUND_WEI", 10**15))  # 0.001 ETH [SIM], as the main pool (DeployPool.s.sol)
    if "fund" not in done:
        fn = pool.functions.fund(cid_b, attest.b32(pol_hash), 0, policy_mod.pool_params(pol))  # Mode.ONCHAIN
        if ch.read(pool.functions.getPool(cid_b))[0][0] != 0:  # PoolState.NONE: opened by a run that died unrecorded
            sys.exit(f"the pool for this covenant is already open but not in {out.name}: add the fund step and re-run")
        rcpt, _ = ch.send(fn, buyer_pk, fund_wei, "multiyear: fund pool")
        log("fund", rcpt, amountWei=str(fund_wei))
        check(rcpt.status == 1, f"buyer [SIM] opened the main IncentivePool for it with {eth(fund_wei)}: "
              f"{ch.tx_link(rcpt.transactionHash)}")
    pl, _ = ch.read(pool.functions.getPool(cid_b))
    check("0x" + bytes(pl[2]).hex() == pol_hash and "0x" + bytes(pl[3]).hex() == pol["onchainParamsHash"]
          and pl[1] == 0 and pl[6] == buyer,
          f"pool for covenant {int(record['covenantId'], 16)}: policyHash = POLICY_V2_MULTIYEAR, paramsHash = "
          f"onchainParamsHash {pol['onchainParamsHash'][:12]}… (the main pool's params), ONCHAIN, opened by the buyer")
    print(f"   final installment reachable for an episode up to {utc(record['lastFinalDetection'])} "
          f"(day {reach['lastDetectionDay']} of {core['term'] // 86400}; final_reach)")
    out.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")
    print(f"\nwrote {out.relative_to(DERIVED.parent.parent)}")


def sandbox_rekey(rpc: str, role: str) -> None:
    """Register the next generation of a published sandbox key after someone revoked it (observer/sandbox.py).
    The deployer is the controller of every sandbox org; it can add keys, never move funds."""
    from observer import sandbox

    ch = Chain(rpc, sandbox=True)
    deployer_pk = env_key("PRIVATE_KEY_DEPLOYER")
    if sandbox.current(ch.reg, role) is not None:
        print(f"   {role}: generation {sandbox.current(ch.reg, role)} is still valid; nothing to do")
        return
    g = 1
    while ch.read(ch.reg.functions.keyStatus(sandbox.address(role, g), 0))[1] != 0:
        g += 1
    fn = ch.reg.functions.registerKey(attest.b32(sandbox.ORGS[role]), sandbox.REGISTRY_ROLE[role],
                                      sandbox.address(role, g), 1, bytes(32))
    try:
        rcpt, _ = ch.send(fn, deployer_pk, 0, "sandbox_rekey")
    finally:
        write_txlog(ch)
    check(rcpt.status == 1, f"{role} generation {g} registered ({sandbox.address(role, g)}): "
          f"{ch.tx_link(rcpt.transactionHash)}")


def fund_judge(rpc: str, to: str, amount_eth: str) -> None:
    """Send test ETH from the deployer to a judge wallet (the key stays in the environment, not on a command line)."""
    ch = Chain(rpc)
    try:  # logged too: the 15 Oct count missed the deployer's funding transfers because this path wrote no log
        rcpt = ch.transfer(env_key("PRIVATE_KEY_DEPLOYER"), Web3.to_checksum_address(to),
                           Web3.to_wei(amount_eth, "ether"), "fund_judge")
    finally:
        write_txlog(ch)
    check(rcpt.status == 1, f"sent {amount_eth} ETH to {to}: {ch.tx_link(rcpt.transactionHash)}")


def write_record(record: dict) -> None:
    RUN.write_text(json.dumps(record, indent=1, sort_keys=True, default=str) + "\n")


def save(record: dict, ch: Chain) -> None:
    write_record(record)
    print(f"\nwrote {RUN.relative_to(DERIVED.parent.parent)}")


def write_txlog(ch: Chain) -> None:
    log = DERIVED.parent.parent / "out" / "testnet" / f"{ch.chain_id}.txlog.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a") as f:
        for t in ch.txlog:
            f.write(json.dumps({"command": "operator", **t}) + "\n")
    txlog.append(ch.txlog, ch.chain_id, "operator")  # tracked copy (data/derived/sepolia_txlog.jsonl)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["run", "finalize", "recover", "multiyear", "sandbox-rekey", "fund-judge"])
    ap.add_argument("--rpc", default=os.environ.get("RPC_URL"))
    ap.add_argument("--role", help="sandbox-rekey: SELLER, BUYER, OBS_1 or OBS_2")
    ap.add_argument("--to", help="fund-judge: recipient address")
    ap.add_argument("--amount", default="0.002", help="fund-judge: test ETH")
    a = ap.parse_args()
    if not a.rpc:
        sys.exit("RPC_URL not set")
    if a.command == "sandbox-rekey":
        return sandbox_rekey(a.rpc, a.role)
    if a.command == "fund-judge":
        return fund_judge(a.rpc, a.to, a.amount)
    ch = Chain(a.rpc)
    try:
        {"run": run, "finalize": finalize, "recover": recover, "multiyear": multiyear}[a.command](ch)
    finally:
        write_txlog(ch)


if __name__ == "__main__":
    main()
