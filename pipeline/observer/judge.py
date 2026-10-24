"""Judge walk-through on a public testnet (scripts/judge.sh; also runs on Anvil for `make judge-local`).

Everything happens in the **judge sandbox [SIM]**: a separate KeyRegistry + MethaneCovenant pair whose seller, buyer
and two observer keys are published (observer/sandbox.py), so a fresh wallet can play every role. Your wallet
(JUDGE_PK) pays for everything and is the proposer and the challenger. The main deployment's keys stay private.

  attest     your wallet becomes a registered observer of org OBS_JUDGE (via its published controller key); your own
             sandbox covenant (you pay the seller's gas and post the bond; start backdated to 2026-01-01 [SIM]);
             your machine fetches Carbon Mapper now (LIVE=0: committed snapshot), builds the canonical bundle, and
             it is signed by YOUR key (org OBS_JUDGE) and by published sandbox observer 1 [SIM]: one of the two
             signatures in the quorum is yours
  propose    your wallet submits the episode (k signatures of the n sandbox observer orgs, both read from the chain
             and printed) -> PROPOSED, rebate held from the seller's bond
  tamper     fresh signatures, then 2,548 -> 548 kg/h in the bundle -> the chain reverts (InvalidSignature, status 0)
  challenge  a seeded bogus episode [SIM] (rate inflated at another facility, signed with the published keys) ->
             you recompute the hash from provider data, it differs -> challenge HASH_MISMATCH -> observers WITHDRAW
             -> VOID -> the bogus proposer's bond is credited to you -> you withdraw it
  finalize   after the 10-minute window: anyone confirms your episode -> CONFIRMED, rebate credited to the buyer
  cure       the two-sided clause with your key (spec-v2 §10): you propose the real DZA_S_012 2026-02-01 plume, it is
             confirmed after the 10-minute window, a refund claim opens on it in the sandbox pool, and you attest the
             real 2026-07-13 Tanager scene as a qualified clean pass (with published observer 1) -> counted after the
             clean window: 1 pass, the next installment at 3. Resumable: run it again after each window. (The
             release freeze is shown as a status-0 release in `make demo`'s DZA replay.)
  status     what is on chain for your run
  reset      forget the local run state; the next `attest` opens a fresh covenant

The sandbox is shared and its keys are public, so someone can race you (propose your evidence first, contest your
episode, revoke a published key). The scripts check for this and say what happened instead of crashing; `reset`
starts over with a new covenant. A contested sandbox episode needs k+1 = 3 confirming observer orgs (the
sandbox has 3 — OBS_1, OBS_2 and OBS_JUDGE — so all three must confirm; otherwise it expires and nobody is slashed).

State between commands: out/judge/<chainId>.json (gitignored).
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import secrets
import sys
from datetime import datetime, timezone

from eth_account import Account
from web3 import Web3
from web3.logs import DISCARD

import txlog
from canon import evidence_hash
from common import DERIVED, ROOT
from observer import attest, sandbox
from observer.bundle import bundle_hash
from observer.chain import Chain
from observer.service import Observer, anvil_key, covenant_terms, load_facility
from policy import COVENANT_START, SANDBOX_POLICY, SANDBOX_SCALE, SANDBOX_TIER_AMOUNTS_WEI

PLUMES = {"TKM_S_276": "tan20260611t083132c23s4001-AB"}  # the lead case; spec §9
SWEPT_EXIT = 3  # the cure stopped because the published BUYER key's transfer was swept (resume with judge-cure)
CURE_CASE = "DZA_S_012"  # the cure arc (spec-v2 §10): its relapse plume and its post-relapse qualified scene, from data
BOGUS_CASE, BOGUS_PLUME = "DZA_S_012", "tan20260201t113140c40s4001-C"
HASH_MISMATCH = 1
GAS_BUDGET_PER_TX = 3_000_000  # budget only (Sepolia prices new storage far above Anvil's Cancun schedule)
FAUCET = "https://cloud.google.com/application/web3/faucet/ethereum/sepolia"


def say(msg: str = "") -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    say(f"   FAIL  {msg}")
    sys.exit(1)


def ok(msg: str) -> None:
    say(f"   ok    {msg}")


def utc(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def eth(wei: int) -> str:
    return f"{Web3.from_wei(wei, 'ether'):f}".rstrip("0").rstrip(".") + " ETH"


def window_stop_message(window: str, deadline: int, wait: int, network: str, *, local: bool) -> str:
    """What the cure prints when it stops at a window. A public chain waits it out in real time; on Anvil
    (JUDGE_NO_SKIP=1, make judge-local) the command stops the same way and the local script then moves the clock."""
    if not local:
        return (f"   the {window} closes {utc(deadline)} ({wait} s from now; real time, nothing is skipped on "
                f"{network}). Run `make judge-cure` again after that.")
    return (f"   the {window} closes {utc(deadline)} ({wait} s from now). The cure stops here as on Sepolia, where "
            f"you wait the window out in real time; on {network}, `make judge-local` next moves the chain clock "
            f"past it [SIM] and runs `make judge-cure` again.")


def quorum_line(k: int, signer_orgs: list[str], registered_orgs: list[str], network: str) -> str:
    """The propose header: k from the covenant, n = observer orgs with a valid key in the sandbox registry now."""
    return (f"== Your wallet proposes the episode on {network}: the covenant's quorum is k = {k} distinct observer "
            f"orgs of the n = {len(registered_orgs)} registered in the sandbox ({', '.join(registered_orgs)}); "
            f"signed by {' + '.join(signer_orgs)}")


class Run:
    def __init__(self, rpc: str):
        self.ch = Chain(rpc, sandbox=True)
        self.state_path = ROOT / "out" / "judge" / f"{self.ch.chain_id}.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
        if self.state and self.state.get("sandbox") != self.ch.cov.address:
            self.state = {}  # the sandbox was redeployed: the old covenant lives in the old contract
        pk = os.environ.get("JUDGE_PK")
        if not pk:
            if not self.ch.local:
                fail("JUDGE_PK is not set. Create a fresh wallet with `cast wallet new`, get test ETH from "
                     f"{FAUCET}, then `export JUDGE_PK=0x...` (testnet only; never a key that holds real funds).")
            pk = anvil_key(7)
        self.pk = pk
        self.me = Account.from_key(pk).address
        self.live = os.environ.get("LIVE", "0" if self.ch.local else "1") == "1"
        self._obs: dict | None = None

    # ------------------------------------------------------------------ sandbox keys (generation-aware)
    def gen(self, role: str) -> int:
        g = sandbox.current(self.ch.reg, role)
        if g is None:
            fail(f"every published {role} key has been revoked by someone. The operator registers the next "
                 f"generation with `make sandbox-rekey ROLE={role}`; until then the sandbox cannot be used.")
        return g

    def published_observer(self, role: str) -> Observer:
        return Observer(sandbox.ORGS[role], self.ch.chain_id, self.ch.cov.address, live=self.live,
                        pk=sandbox.key(role, self.gen(role)))

    @property
    def obs(self) -> dict:
        """The quorum for YOUR episode: your own wallet (org OBS_JUDGE) + published sandbox observer 1."""
        if self._obs is None:
            self._obs = {"OBS_JUDGE": Observer(sandbox.JUDGE_ORG, self.ch.chain_id, self.ch.cov.address,
                                               live=self.live, pk=self.pk),
                         "OBS_1": self.published_observer("OBS_1")}
        return self._obs

    def registered_observer_orgs(self) -> list[str]:
        """Sandbox observer orgs that hold a valid OBSERVER key now: the published SANDBOX_OBS_* orgs (any generation)
        and OBS_JUDGE (your wallet's org, once `judge-attest` registered it)."""
        orgs = [sandbox.ORGS[r] for r in ("OBS_1", "OBS_2") if sandbox.current(self.ch.reg, r) is not None]
        org, role, valid = self.ch.read(self.ch.reg.functions.keyStatus(self.me, self.ch.now()))
        if role == 1 and valid and bytes(org).rstrip(b"\0") == sandbox.JUDGE_ORG.encode():
            orgs.append(sandbox.JUDGE_ORG)
        return orgs

    @property
    def bogus_obs(self) -> dict:
        """The seeded bogus episode is signed (and later withdrawn) by the two published sandbox observers."""
        return {o: self.published_observer(o) for o in ("OBS_1", "OBS_2")}

    def ensure_judge_observer(self) -> None:
        """Register your wallet as an OBSERVER key of org OBS_JUDGE, using the org's published controller key."""
        ch = self.ch
        org, role, valid = ch.read(ch.reg.functions.keyStatus(self.me, ch.now()))
        if role == 1 and valid and bytes(org).rstrip(b"\0") == sandbox.JUDGE_ORG.encode():
            ok(f"your wallet is a registered observer of org {sandbox.JUDGE_ORG}")
            return
        if role != 0:
            fail(f"your wallet {self.me} is already in this registry with role {role}, valid={valid} (someone may have "
                 "revoked it with the published controller key). Use a fresh wallet.")
        ctl_pk, ctl = sandbox.key(sandbox.JUDGE_CONTROLLER), sandbox.address(sandbox.JUDGE_CONTROLLER)
        fn = ch.reg.functions.registerKey(attest.b32(sandbox.JUDGE_ORG), 1, self.me, 1, bytes(32))
        try:
            gas = 2 * fn.estimate_gas({"from": ctl})
        except Exception:
            gas = GAS_BUDGET_PER_TX
        have = ch.w3.eth.get_balance(ctl)
        if have < gas * self.fee_per_gas():
            ch.transfer(self.pk, ctl, gas * self.fee_per_gas() - have, "fund_judge_controller_gas")
        rcpt, _ = ch.send(fn, ctl_pk, 0, "registerKey_judge")
        if rcpt.status != 1:
            fail(f"could not register your wallet as an observer: {self.link(rcpt)}")
        ok(f"your wallet {self.me[:10]}… is now a registered OBSERVER key of org {sandbox.JUDGE_ORG} "
           f"(registered with the org's published controller key [SIM]) — {self.link(rcpt)}")

    # ------------------------------------------------------------------ helpers
    def save(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=1, sort_keys=True) + "\n")

    def fee_per_gas(self) -> int:
        return self.ch._fees()["maxFeePerGas"]

    def link(self, rcpt) -> str:
        return self.ch.tx_link(rcpt.transactionHash)

    def cid(self) -> str:
        if "covenantId" not in self.state:
            fail("no sandbox covenant yet: run `make judge-attest` first")
        return self.state["covenantId"]

    def sign_both(self, case: str, plume: str, cid: str, override: dict | None = None, signers=None) -> list[dict]:
        terms = covenant_terms(self.ch, cid)
        signers = signers or self.obs
        for o in signers.values():
            o.seen_plumes.clear()  # the chain is the dedup across runs (evidenceSeen / LINKED)
        return [o.attest(case, plume, covenant_id=cid, override=override, **terms) for o in signers.values()]

    def propose_fn(self, cid: str, case: str, signed: list[dict], evidence: str | None = None):
        facility, _, _ = load_facility(case)
        return self.ch.cov.functions.proposeEpisode(
            attest.b32(cid), attest.b32(facility["sourceId"]), [attest.b32(p) for p in facility["proof"]],
            attest.b32(evidence or signed[0]["evidenceHash"]),
            [attest.as_tuple(s["attestation"]) for s in signed], [bytes.fromhex(s["signature"][2:]) for s in signed])

    def episode_id(self, rcpt) -> int:
        evs = self.ch.cov.events.EpisodeProposed().process_receipt(rcpt, errors=DISCARD) or \
            self.ch.cov.events.DetectionLinked().process_receipt(rcpt, errors=DISCARD)
        return evs[0]["args"]["episodeId"]

    # ------------------------------------------------------------------ commands
    def cmd_attest(self, source: str) -> None:
        if source not in PLUMES:
            fail(f"SOURCE must be one of {sorted(PLUMES)}")
        ch = self.ch
        say(f"== Judge sandbox [SIM] on {ch.network} — your wallet {self.me}")
        bal = ch.w3.eth.get_balance(self.me)
        bond = self.sandbox_bond()
        need = bond + 3 * ch.dep["proposerBond"] + ch.dep["challengerBond"] + 12 * GAS_BUDGET_PER_TX * self.fee_per_gas()
        say(f"   balance {eth(bal)}; the whole walk-through needs about {eth(need)} at the current gas price")
        if bal < need:
            fail(f"not enough test ETH. Faucet: {FAUCET}")

        say("\n== 1/3  Your wallet joins the sandbox as an observer (org OBS_JUDGE)")
        self.ensure_judge_observer()
        if "covenantId" not in self.state:
            say("\n== 2/3  Your sandbox covenant: seller [SIM] -> buyer [SIM], start backdated to 2026-01-01 [SIM]")
            g_seller, g_buyer = self.gen("SELLER"), self.gen("BUYER")
            seller_pk, seller = sandbox.key("SELLER", g_seller), sandbox.address("SELLER", g_seller)
            policy = {**SANDBOX_POLICY, "bondWei": str(bond), "judge": self.me,
                      "sourceSetRoot": load_facility(source)[0]["root"]}
            p = SANDBOX_POLICY
            params = (sandbox.address("BUYER", g_buyer), attest.b32(policy["sourceSetRoot"]),
                      attest.b32(evidence_hash(policy)), p["term"], COVENANT_START, p["k"], p["challengeWindow"],
                      p["contestWindow"], p["linkWindow"], p["minRateKgH"], list(SANDBOX_TIER_AMOUNTS_WEI))
            fn = ch.cov.functions.createCovenant(params)
            # The published seller key only ever holds gas money: the covenant is created with value 0 and your own
            # wallet posts the bond with fund(). A bot sweeping the seller key can steal gas dust, never a bond.
            try:  # some RPCs cap the estimate by the sender's balance, and the seller holds only gas dust
                gas = 2 * fn.estimate_gas({"from": seller})
            except Exception:
                gas = GAS_BUDGET_PER_TX
            have = ch.w3.eth.get_balance(seller)
            if have < gas * self.fee_per_gas():
                rcpt = ch.transfer(self.pk, seller, gas * self.fee_per_gas() - have, "fund_sandbox_seller_gas")
                if rcpt.status != 1:
                    fail("could not send gas money to the sandbox seller")
            rcpt, _ = ch.send(fn, seller_pk, 0, "createCovenant")
            if rcpt.status != 1:
                fail(f"createCovenant failed: {self.link(rcpt)}")
            ev = ch.cov.events.CovenantCreated().process_receipt(rcpt, errors=DISCARD)[0]["args"]
            cid = "0x" + bytes(ev["covenantId"]).hex()
            self.state = {"covenantId": cid, "judge": self.me, "createTx": self.link(rcpt), "sandbox": ch.cov.address}
            self.save()
            ok(f"covenant {int(cid, 16)} created by seller [SIM] {seller[:10]}…: {self.link(rcpt)}")
            rcpt, _ = ch.send(ch.cov.functions.fund(attest.b32(cid)), self.pk, bond, "fund")
            if rcpt.status != 1:
                fail(f"fund failed: {self.link(rcpt)}")
            c = ch.covenant(attest.b32(cid))
            ok(f"you posted the bond for the seller [SIM]: {eth(c['remaining'])} — {self.link(rcpt)}")
            ok(f"start {utc(c['start'])} (backdated) < createdAt {utc(c['createdAt'])}: the chain shows the plume "
               "was public before this covenant existed")
        cid = self.cid()

        say(f"\n== 3/3  Your machine fetches {'Carbon Mapper now (live API)' if self.live else 'the committed snapshot'}, "
            "validates, hashes (RFC 8785 + SHA-256); signed EIP-712 by YOUR key (OBS_JUDGE) and published observer 1 [SIM]")
        signed = self.sign_both(source, PLUMES[source], cid)
        for s in signed:
            a = s["attestation"]
            say(f"   {s['org']:14} signer {s['signer']}  nonce {a['nonce']}  expires {a['expiry']}")
            say(f"   {'':14} evidenceHash {s['evidenceHash']}  {s['bundle']['emissionKgH']} ± "
                f"{s['bundle']['uncertaintyKgH']} kg/h  ({s['receipt']['mode']} fetch {s['receipt']['retrievedAt']})")
        if signed[0]["evidenceHash"] != signed[1]["evidenceHash"]:
            fail("the two observers built different bundles")
        ok("you and observer 1 signed the identical canonical bundle; signatures are fresh (random nonce, 1-hour "
           "expiry). One signature in the quorum is yours; observer 1's key is published [SIM]")
        for s in signed:
            rec = attest.recover(s["attestation"], bytes.fromhex(s["signature"][2:]), ch.chain_id, ch.cov.address)
            if rec != s["signer"]:
                fail("signature does not recover to the observer")
        ok("each signature recovers to its observer's registered key")
        self.state.update(source=source, signed=[json.loads(_dump(s)) for s in signed])
        self.save()
        say("\nNext: make judge-propose")

    def cmd_propose(self) -> None:
        ch, cid = self.ch, self.cid()
        if "signed" not in self.state:
            fail("run `make judge-attest` first")
        signed = [_load(s) for s in self.state["signed"]]
        if min(s["attestation"]["expiry"] for s in signed) <= ch.now():
            fail("the attestations expired (1 hour); run `make judge-attest` again")
        say(quorum_line(ch.covenant(attest.b32(cid))["k"], [o.org for o in self.obs.values()],
                        self.registered_observer_orgs(), ch.network))
        if ch.read(ch.cov.functions.evidenceSeen(attest.b32(signed[0]["evidenceHash"]))):
            fail("this exact evidence was already proposed on your covenant — by someone else, since the sandbox keys "
                 "are public. The chain would answer DuplicateEvidence. `make judge-reset` starts a fresh covenant.")
        before = ch.covenant(attest.b32(cid))["remaining"]
        rcpt, _ = ch.send(self.propose_fn(cid, self.state["source"], signed), self.pk, ch.dep["proposerBond"],
                          "proposeEpisode")
        if rcpt.status != 1:
            fail(f"proposeEpisode reverted: {self.link(rcpt)}")
        eid = self.episode_id(rcpt)
        e = ch.episode(eid)
        if e["stateName"] == "LINKED":
            fail(f"episode {eid} was LINKED to episode {e['parentEpisode']}: someone proposed a detection at this "
                 "facility on your covenant first (public sandbox keys). Recorded, not paid; your bond is credited "
                 f"back. `make judge-reset` starts a fresh covenant. {self.link(rcpt)}")
        ok(f"episode {eid} {e['stateName']}, tier {e['tier']}, rebate held {eth(e['rebate'])} "
           f"(bond {eth(before)} -> {eth(ch.covenant(attest.b32(cid))['remaining'])})")
        ok(f"tx {self.link(rcpt)}")
        say(f"   challenge window closes {utc(e['deadline'])} (10 minutes); then `make judge-finalize`")
        self.state.update(episodeId=eid, proposeTx=self.link(rcpt))
        self.save()
        say("\nNext: make judge-tamper")

    def cmd_tamper(self) -> None:
        ch, cid = self.ch, self.cid()
        source = self.state.get("source", "TKM_S_276")
        say("== Tamper: observers sign fresh, then the bundle is edited 2,548 -> 548 kg/h; signatures kept")
        signed = self.sign_both(source, PLUMES[source], cid)
        tb = copy.deepcopy(signed[0]["bundle"])
        tb["emissionKgH"] = tb["emissionKgH"] - 2000 if tb["emissionKgH"] > 2100 else tb["emissionKgH"] // 4
        tev = bundle_hash(tb)
        say(f"   signed   evidenceHash {signed[0]['evidenceHash']}  ({signed[0]['bundle']['emissionKgH']} kg/h)")
        say(f"   tampered evidenceHash {tev}  ({tb['emissionKgH']} kg/h)")
        for s in signed:
            s["attestation"].update(evidenceHash=attest.b32(tev), emissionKgH=tb["emissionKgH"])
        before = (ch.covenant(attest.b32(cid))["remaining"], ch.read(ch.cov.functions.episodeCount()))
        rcpt, why = ch.send(self.propose_fn(cid, source, signed, tev), self.pk, ch.dep["proposerBond"],
                            expect_revert="InvalidSignature")
        after = (ch.covenant(attest.b32(cid))["remaining"], ch.read(ch.cov.functions.episodeCount()))
        if rcpt.status != 0 or why != "InvalidSignature":
            fail(f"expected a reverted transaction with InvalidSignature, got status {rcpt.status} {why}")
        ok(f"reverted on chain (receipt status 0): {why} — {self.link(rcpt)}")
        if after != before:
            fail("state moved after a reverted transaction")
        ok(f"bond unchanged ({eth(after[0])}), no episode created, your proposer bond was not taken")
        self.state["tamperTx"] = self.link(rcpt)
        self.save()
        say("\nNext: make judge-challenge")

    def cmd_challenge(self) -> None:
        ch, cid = self.ch, self.cid()
        say(f"== Seeded bogus episode [SIM]: {BOGUS_CASE} rate inflated, signed with the published sandbox keys")
        bogus_pk = "0x" + secrets.token_hex(32)
        bogus = Account.from_key(bogus_pk).address
        rate = 6_000 + secrets.randbelow(3_000)  # differs per run, so each run's bogus evidence is new
        signed = self.sign_both(BOGUS_CASE, BOGUS_PLUME, cid, override={"emission_auto": float(rate)},
                                signers=self.bogus_obs)
        fn = self.propose_fn(cid, BOGUS_CASE, signed)
        gas = fn.estimate_gas({"from": self.me, "value": ch.dep["proposerBond"]})  # the proposer is not checked
        ch.transfer(self.pk, bogus, ch.dep["proposerBond"] + 2 * gas * self.fee_per_gas(), "fund_bogus_proposer")
        rcpt, _ = ch.send(fn, bogus_pk, ch.dep["proposerBond"], "proposeEpisode_bogus")
        if rcpt.status != 1:
            fail(f"could not seed the bogus episode: {self.link(rcpt)}")
        eid = self.episode_id(rcpt)
        e = ch.episode(eid)
        ok(f"bogus episode {eid} {e['stateName']} by {bogus[:10]}…: {e['emissionKgH']} kg/h, tier {e['tier']}, "
           f"rebate held {eth(e['rebate'])} — {self.link(rcpt)}")

        say("\n== You check it: rebuild the bundle from provider data and compare hashes")
        honest = self.sign_both(BOGUS_CASE, BOGUS_PLUME, cid, signers={"YOU": self.obs["OBS_JUDGE"]})[0]
        on_chain = "0x" + bytes(e["evidenceHash"]).hex()
        say(f"   on chain   {on_chain}  ({e['emissionKgH']} kg/h)")
        say(f"   recomputed {honest['evidenceHash']}  ({honest['bundle']['emissionKgH']} kg/h, "
            f"{honest['receipt']['mode']} fetch)")
        if honest["evidenceHash"] == on_chain:
            fail("hashes match; nothing to challenge")
        ok("hashes differ: HASH_MISMATCH")

        if ch.episode(eid)["stateName"] != "PROPOSED":
            fail(f"episode {eid} is already {ch.episode(eid)['stateName']}: someone acted on it first (public sandbox "
                 "keys). Run `make judge-challenge` again for a new bogus episode.")
        say("\n== Your wallet challenges with reason HASH_MISMATCH (bond "
            f"{eth(ch.dep['challengerBond'])})")
        rcpt, _ = ch.send(ch.cov.functions.challenge(eid, HASH_MISMATCH), self.pk, ch.dep["challengerBond"],
                          "challenge")
        if rcpt.status != 1 or ch.episode(eid)["stateName"] != "CHALLENGED":
            fail(f"challenge failed: {self.link(rcpt)}")
        ok(f"episode {eid} CHALLENGED — {self.link(rcpt)}")

        say("\n== The two observers who signed it re-check and sign WITHDRAW for that evidenceHash; you submit it")
        terms = covenant_terms(ch, cid)
        wd = []
        for o, s in zip(self.bogus_obs.values(), signed):
            a = dict(s["attestation"], kind=attest.KIND_WITHDRAW, nonce=secrets.randbits(64),
                     expiry=terms["now"] + 3600)
            wd.append((a, attest.sign(a, o._pk, ch.chain_id, ch.cov.address)))
        mine_before = ch.read(ch.cov.functions.withdrawable(self.me))
        rcpt, _ = ch.send(ch.cov.functions.submitResolution(eid, [attest.as_tuple(a) for a, _ in wd],
                                                            [s for _, s in wd]), self.pk, 0, "submitResolution")
        e = ch.episode(eid)
        if rcpt.status != 1 or e["stateName"] != "VOID":
            fail(f"resolution failed: {self.link(rcpt)}")
        credited = ch.read(ch.cov.functions.withdrawable(self.me)) - mine_before
        ok(f"episode {eid} VOID; credited to you {eth(credited)} = the bogus proposer's bond "
           f"{eth(ch.dep['proposerBond'])} + your challenger bond back — {self.link(rcpt)}")
        if credited != ch.dep["proposerBond"] + ch.dep["challengerBond"]:
            fail("unexpected credit")
        rcpt, _ = ch.send(ch.cov.functions.withdraw(), self.pk, 0, "withdraw")
        paid = [ev["args"]["amount"] for ev in ch.cov.events.Withdrawn().process_receipt(rcpt, errors=DISCARD)
                if ev["args"]["account"] == self.me]
        if rcpt.status != 1 or sum(paid) < credited:
            fail(f"withdraw failed: {self.link(rcpt)}")
        ok(f"withdrawn to your wallet — {self.link(rcpt)}")
        c = ch.covenant(attest.b32(cid))
        ok(f"held rebate returned to the bond: remaining {eth(c['remaining'])}, held {eth(c['held'])}")
        self.state.update(bogusEpisodeId=eid, challengeTx=self.link(rcpt))
        self.save()

    def cmd_finalize(self) -> None:
        ch = self.ch
        eid = self.state.get("episodeId") or fail("run `make judge-propose` first")
        e = ch.episode(eid)
        if e["stateName"] == "CONTESTED":
            k, orgs = ch.covenant(attest.b32(self.cid()))["k"], self.registered_observer_orgs()
            say(f"   episode {eid} was CONTESTED with the published seller key. Confirming a contested episode needs "
                f"k+1 = {k + 1} of the n = {len(orgs)} sandbox observer orgs; this walk-through does not gather "
                "them, so it EXPIRES after the window: rebate back to the bond, nobody slashed. That is the "
                "contract working as designed.")
            return
        if e["stateName"] != "PROPOSED":
            say(f"   episode {eid} is {e['stateName']}")
            return
        wait = e["deadline"] - ch.now()
        if wait > 0:
            if ch.local:
                from observer.demo import advance_clock
                skipped = advance_clock(ch, e["deadline"] + 1, "challenge window", log=say)
                # out/judge/run.json survives `judge reset` (which clears the covenant state): skippedSeconds per window
                run_path = self.state_path.parent / "run.json"
                run = json.loads(run_path.read_text()) if run_path.exists() else {"chainId": ch.chain_id}
                sk = run.setdefault("skippedSeconds", {})
                sk["challenge window"] = sk.get("challenge window", 0) + skipped
                run_path.write_text(json.dumps(run, indent=1, sort_keys=True) + "\n")
            else:
                fail(f"challenge window still open for {wait} s; try again after that")
        rcpt, _ = ch.send(ch.cov.functions.finalize(eid), self.pk, 0, "finalize")
        e = ch.episode(eid)
        if rcpt.status != 1 or e["stateName"] != "CONFIRMED":
            fail(f"finalize failed: {self.link(rcpt)}")
        buyer = ch.covenant(attest.b32(self.cid()))["buyer"]
        ok(f"episode {eid} CONFIRMED; buyer [SIM] credited {eth(ch.read(ch.cov.functions.withdrawable(buyer)))}; "
           f"your proposer bond is back in withdrawable — {self.link(rcpt)}")

    # ------------------------------------------------------------------ cure (spec-v2 §10 judge-cure)
    def pool(self):
        """The sandbox IncentivePool: from the deployment record (the sandbox deploy) or, on Anvil, the one
        `make judge-local` deployed (out/judge/pool-<chainId>.json)."""
        from observer.chain import abi as load_abi

        addr = self.ch.dep.get("incentivePool")
        local = ROOT / "out" / "judge" / f"pool-{self.ch.chain_id}.json"
        if not addr and local.exists():
            addr = json.loads(local.read_text())["IncentivePool"]
        if not addr:
            fail("the sandbox has no IncentivePool yet (pending the sandbox redeploy); `make judge-local` runs the cure "
                 "on Anvil")
        pool = self.ch.w3.eth.contract(address=addr, abi=load_abi("IncentivePool"))
        names = ["IncentivePool"] + (list(json.loads(local.read_text())) if local.exists() else [])
        for n in names:
            try:
                for e in load_abi(n):
                    if e["type"] == "error":
                        self.ch.errors[Web3.to_hex(Web3.keccak(text=Chain._sig(e)))[:10]] = e["name"]
            except FileNotFoundError:
                pass
        return pool

    def wait_or_skip(self, deadline: int, window: str) -> None:
        """Anvil: move the clock forward over the window [SIM]; a public chain: say when to come back."""
        wait = deadline - self.ch.now()
        if wait <= 0:
            return
        # JUDGE_NO_SKIP=1 (Anvil only, used by make judge-local) behaves like a public chain: exit and resume
        if not self.ch.local or os.environ.get("JUDGE_NO_SKIP") == "1":
            say(window_stop_message(window, deadline, wait, self.ch.network, local=self.ch.local))
            sys.exit(0)
        from observer.demo import advance_clock
        skipped = advance_clock(self.ch, deadline, window, log=say)
        run_path = self.state_path.parent / "run.json"
        run = json.loads(run_path.read_text()) if run_path.exists() else {"chainId": self.ch.chain_id}
        sk = run.setdefault("skippedSeconds", {})
        sk[window] = sk.get(window, 0) + skipped
        run_path.write_text(json.dumps(run, indent=1, sort_keys=True) + "\n")

    def require_no_live_episode(self, cid: str, case: str) -> None:
        """The seeded bogus episode of `judge-challenge` sits at the same facility: it must be settled (VOID) before
        the real one is proposed, or the real one would be LINKED to it or refused. Refuses while it is live."""
        ch = self.ch
        sid = attest.b32(load_facility(case)[0]["sourceId"])
        prev = ch.read(ch.cov.functions.latestEpisode(attest.b32(cid), sid))
        if prev and ch.episode(prev)["stateName"] in ("PROPOSED", "CHALLENGED", "CONTESTED"):
            fail(f"episode {prev} at {case} is still {ch.episode(prev)['stateName']} on your covenant (the seeded "
                 "bogus episode of `make judge-challenge`, or someone else's): finish `make judge-challenge` (it ends "
                 "VOID) or wait for it to settle, then run `make judge-cure` again")
        if prev:
            ok(f"the facility's latest episode on your covenant ({prev}) is {ch.episode(prev)['stateName']}: nothing "
               "live to collide with")

    def cmd_cure(self) -> None:
        import policy as policy_mod
        from observer import cleanpass
        from observer.attest import cleanpass_tuple
        from observer.chain import _named

        ch, cid = self.ch, self.cid()
        cure = self.state.setdefault("cure", {})
        pool = self.pool()
        POL = policy_mod.POLICY_V2_SANDBOX
        pol = json.loads((DERIVED / "policy_v2.json").read_text())["policies"]["POLICY_V2_SANDBOX"]
        doc = cleanpass.load_case(CURE_CASE)
        relapse_name = doc["summary"]["firstPlumeAfterSince"]["sceneName"]
        _, _, case_file = load_facility(CURE_CASE)
        plume = max((p for p in case_file["cm_plumes_within_radius"]
                     if p["plume_id"].rsplit("-", 1)[0] == relapse_name and p.get("emission_auto") is not None),
                    key=lambda p: p["emission_auto"])
        relapse_t = int(datetime.fromisoformat(doc["summary"]["firstPlumeAfterSince"]["sceneAtIso"]
                                               .replace("Z", "+00:00")).timestamp())
        scene = next(x for x in doc["scenes"] if x["qualified"] and x["sceneAt"] > relapse_t)
        say(f"== Cure [SIM parties] on {ch.network}: {CURE_CASE}'s real relapse plume {plume['plume_id']} and its real "
            f"post-relapse qualified scene {scene['sceneName']}, signed by YOUR key (OBS_JUDGE) + published observer 1")

        if "episodeId" not in cure:
            say("\n== 1/4  You propose the relapse detection as an episode on your covenant")
            self.require_no_live_episode(cid, CURE_CASE)
            signed = self.sign_both(CURE_CASE, plume["plume_id"], cid)
            rcpt, _ = ch.send(self.propose_fn(cid, CURE_CASE, signed), self.pk, ch.dep["proposerBond"],
                              "proposeEpisode_cure")
            if rcpt.status != 1:
                fail(f"proposeEpisode reverted: {self.link(rcpt)}")
            eid = self.episode_id(rcpt)
            e = ch.episode(eid)
            if e["stateName"] != "PROPOSED":
                fail(f"episode {eid} is {e['stateName']} (someone proposed at this facility first: public sandbox "
                     f"keys); `make judge-reset` starts a fresh covenant. {self.link(rcpt)}")
            cure.update(episodeId=eid, proposeTx=self.link(rcpt))
            self.save()
            ok(f"episode {eid} PROPOSED: {e['emissionKgH']} kg/h, tier {e['tier']} — {self.link(rcpt)}")
        eid = cure["episodeId"]
        e = ch.episode(eid)
        if e["stateName"] == "PROPOSED":
            self.wait_or_skip(e["deadline"] + 1, "challenge window")
            rcpt, _ = ch.send(ch.cov.functions.finalize(eid), self.pk, 0, "finalize_cure")
            if rcpt.status != 1 or ch.episode(eid)["stateName"] != "CONFIRMED":
                fail(f"finalize failed: {self.link(rcpt)}")
            ok(f"episode {eid} CONFIRMED — {self.link(rcpt)}")
        elif e["stateName"] != "CONFIRMED":
            fail(f"episode {eid} is {e['stateName']}; `make judge-reset` starts over")

        if "claimId" not in cure:
            say("\n== 2/4  The refund pool: opened by the covenant's buyer [SIM] (published key), then your claim")
            pl, _ = pool.functions.getPool(attest.b32(cid)).call()
            if pl[0] == 0:  # NONE: the first fund must come from a covenant party
                e = ch.episode(eid)
                value = e["rebate"] + int(POL["pool"]["observerFeeWei"])
                g = self.gen("BUYER")
                bpk, baddr = sandbox.key("BUYER", g), sandbox.address("BUYER", g)
                fn = pool.functions.fund(attest.b32(cid), attest.b32(pol["policyHash"]), 0, policy_mod.pool_params(POL))
                # the published buyer key gets exactly the pool value plus gas money from your wallet
                sent = value + GAS_BUDGET_PER_TX * self.fee_per_gas()
                ch.transfer(self.pk, baddr, sent, "fund_sandbox_buyer")
                simulated = ch.local and os.environ.get("JUDGE_SIMULATE_SWEEP") == "1"  # judge-local's test
                if simulated:
                    left = ch.w3.eth.get_balance(baddr) - 21_000 * self.fee_per_gas()
                    ch.transfer(bpk, self.me, left, "simulated_sweep_of_published_buyer_key")
                if ch.w3.eth.get_balance(baddr) < value:
                    msg = (f"the {eth(sent)} you just sent to the published BUYER key {baddr} was taken before it could "
                           "open the pool: anyone holding a published sandbox key can sweep it. You lost only that "
                           "amount (pool value + gas money). Run `make judge-cure` again; it resumes here.")
                    if simulated:
                        say(f"   stopped as expected [SIM sweep]: {msg}")
                        sys.exit(SWEPT_EXIT)
                    fail(msg)
                rcpt, _ = ch.send(fn, bpk, value, "fund_pool_cure")
                if rcpt.status != 1:
                    fail(f"the buyer [SIM] could not open the pool: {self.link(rcpt)}")
                ok(f"pool opened by the buyer [SIM] with the episode's rebate + one observer fee "
                   f"({eth(value)}) — {self.link(rcpt)}")
            rcpt, _ = ch.send(pool.functions.openClaim(eid), self.pk, 0, "openClaim_cure")
            if rcpt.status != 1:
                fail(f"openClaim failed: {self.link(rcpt)}")
            ev = pool.events.ClaimOpened().process_receipt(rcpt, errors=DISCARD)[0]["args"]
            cure.update(claimId="0x" + bytes(ev["claimId"]).hex(), claimTx=self.link(rcpt))
            self.save()
            c = _named(pool.functions.getClaim(ev["claimId"]).abi["outputs"], pool.functions.getClaim(ev["claimId"]).call())
            ok(f"claim opened; the clean run counts after {utc(c['since'])} (detection + link window); cap "
               f"{eth(c['cap'])} — {self.link(rcpt)}")
        claim = cure["claimId"]

        if "passId" not in cure:
            say(f"\n== 3/4  You attest the real {scene['sceneName']} scene as a qualified clean pass")
            outs = []
            for org, pk in (("OBS_JUDGE", self.pk), ("OBS_1", sandbox.key("OBS_1", self.gen("OBS_1")))):
                outs.append(cleanpass.CleanPassObserver(org, ch.chain_id, pool.address, pk=pk, live=False).attest(
                    CURE_CASE, scene["sceneProviderId"], covenant_id=cid, claim_id=claim, now=ch.now()))
            rcpt, _ = ch.send(pool.functions.attestCleanPass(attest.b32(claim), [cleanpass_tuple(o["message"]) for o in outs],
                                                             [bytes.fromhex(o["signature"][2:]) for o in outs]),
                              self.pk, int(POL["pool"]["passBondWei"]), "attestCleanPass_cure")
            if rcpt.status != 1:
                fail(f"attestCleanPass reverted: {self.link(rcpt)}")
            ev = pool.events.CleanPassAttested().process_receipt(rcpt, errors=DISCARD)[0]["args"]
            cure.update(passId=ev["passId"], passTx=self.link(rcpt))
            self.save()
            ok(f"pass {ev['passId']} PENDING, signed by you and observer 1 [SIM]; open to challenge for the clean "
               f"window — {self.link(rcpt)}")
        pid = cure["passId"]
        ps = _named(pool.functions.getPass(pid).abi["outputs"], pool.functions.getPass(pid).call())
        if ps["state"] == 1:  # PENDING
            self.wait_or_skip(ps["window"], "clean window")
            say("\n== 4/4  Anyone counts the pass after the clean window")
            rcpt, _ = ch.send(pool.functions.countPass(pid), self.pk, 0, "countPass_cure")
            ev = pool.events.CleanPassCounted().process_receipt(rcpt, errors=DISCARD)
            if rcpt.status != 1 or not ev:
                fail(f"countPass failed: {self.link(rcpt)}")
            nxt = POL["pool"]["installments"][0][0]
            ok(f"counted: {ev[0]['args']['counted']} qualified clean pass, u(N) {ev[0]['args']['upperBoundBp'] / 100:g} %; "
               f"the next installment needs {nxt} — {self.link(rcpt)}")
            cure.update(countTx=self.link(rcpt), counted=ev[0]["args"]["counted"])
            self.save()
        elif ps["state"] == 3:
            ok(f"pass {pid} already counted")
        else:
            fail(f"pass {pid} is in state {ps['state']} (challenged or void: public sandbox keys); see `make judge-status`")
        say("\nThe cure arc ran with your key: a confirmed relapse, a refund claim and a counted qualified clean pass.")

    def cmd_reset(self) -> None:
        self.state = {}
        self.save()
        say("local run state cleared; `make judge-attest` opens a fresh sandbox covenant")

    def cmd_status(self) -> None:
        say(json.dumps({"network": self.ch.network, "sandboxCovenantContract": self.ch.cov.address,
                        "judge": self.me, **{k: v for k, v in self.state.items() if k != "signed"}}, indent=1))

    def sandbox_bond(self) -> int:
        sz = DERIVED / "model" / "bond_sizing.json"
        return int(json.loads(sz.read_text())["recommended_bond_wei"]) // SANDBOX_SCALE


def _dump(signed: dict) -> str:
    att = {k: ("0x" + v.hex() if isinstance(v, bytes) else v) for k, v in signed["attestation"].items()}
    return json.dumps({**signed, "attestation": att})


def _load(d: dict) -> dict:
    byte_fields = {"covenantId", "sourceId", "evidenceHash", "modelHash", "providerId"}
    att = {k: (bytes.fromhex(v[2:]) if k in byte_fields else v) for k, v in d["attestation"].items()}
    return {**d, "attestation": att}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["attest", "propose", "tamper", "challenge", "finalize", "cure", "status", "reset"])
    ap.add_argument("--source", default="TKM_S_276")
    ap.add_argument("--rpc", default=os.environ.get("RPC_URL", "http://127.0.0.1:8545"))
    a = ap.parse_args()
    run = Run(a.rpc)
    try:
        getattr(run, f"cmd_{a.command}")(*([a.source] if a.command == "attest" else []))
    except SystemExit:
        raise
    except Exception as e:  # a revert during gas estimation, or an RPC refusal: say it plainly, no traceback
        data = getattr(e, "data", None) or (e.args[1] if len(e.args) > 1 else "")
        name = run.ch.errors.get(str(data)[:10]) if data else None
        fail(f"the chain refused the call: {name or str(e)[:160]}. The sandbox is shared and its keys are public, so "
             "someone may have acted first; `make judge-status` shows your run, `make judge-reset` starts over.")
    finally:  # keep the transaction log even when a step fails; Sepolia rows also go to the tracked
        log = ROOT / "out" / "judge" / f"{run.ch.chain_id}.txlog.jsonl"  # data/derived/sepolia_txlog.jsonl
        log.parent.mkdir(parents=True, exist_ok=True)                    # (pipeline/txlog.py), which measure.py reads
        with log.open("a") as f:
            for t in run.ch.txlog:
                f.write(json.dumps({"command": a.command, **t}) + "\n")
        txlog.append(run.ch.txlog, run.ch.chain_id, f"judge:{a.command}")


if __name__ == "__main__":
    main()
