"""Gas against facility-set size (A6, scaling), measured with real transactions on a local Anvil -> data/derived/scale.json.

One covenant per facility set, each over the set's Merkle root (leaves sorted by source_name, the encoding of
pipeline/merkle.py and contracts/src/lib/SigLib.sol); in each, OBS_A and OBS_C sign the TKM_S_276 2026-06-11 plume and
an episode is proposed with TKM_S_276's Merkle path. Per set:

  createCovenant                    the set enters the chain as one 32-byte root, whatever its size
  proposeEpisode with the old proof a path from another set: status 0, `NotCovered`, nothing written (fail closed)
  proposeEpisode with its own proof the path has ceil(log2 n) or fewer steps; this is the only part that grows

Sets: v2 (3 facilities, data/derived/sourceset_v2.json) · v3 (the portfolio, data/derived/sourceset_v3.json) · the 1,122
MARS targets of the frozen join (rule 1 of pipeline/join/CRITERION.md, read from data/derived/mars_sources.csv) · those
1,122 plus synthetic leaves up to 10,000 [SIM padding]. The v2 and v3 roots are recomputed and must equal the committed
files. A warm-up covenant and episode run first and are not in the table: the first covenant and the first episode on a
fresh chain also pay for initialising the contract's counters.

The chain runs on Anvil's public test keys and a pinned clock (genesis 2026-10-14, one second per block); the observers'
nonces come from a seeded generator, so a re-run reproduces every gas figure. Anvil is Foundry's local node; gas is
Foundry's schedule (Cancun), which prices new state far below what Ethereum Sepolia charged us (data/derived/measurements.json).

    python -m observer.scale [--port 8961]        (make scale; starts and stops its own Anvil)
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from canon import evidence_hash
from common import DERIVED, ROOT, write_json
from merkle import hx, leaf, proof, root, source_id, verify

OUT = DERIVED / "scale.json"
CASE = "TKM_S_276"
PLUME = "tan20260611t083132c23s4001-AB"
PAD_TO = 10_000
PAD_PREFIX = "SIM_PAD_"
GENESIS = 1_792_000_000  # 2026-10-14 00:00 UTC, the same pinned clock as scripts/demo.sh


def mars_targets() -> list[str]:
    from join.join import mars_target

    return sorted(mars_target(pd.read_csv(DERIVED / "mars_sources.csv"))["source_name"])


def build_sets() -> list[dict]:
    """The four facility sets, each {key, n, label, real, names (sorted), root, sourceIds}; offline, no chain."""
    v2, v3 = (json.loads((DERIVED / f"sourceset_{v}.json").read_text()) for v in ("v2", "v3"))
    targets = mars_targets()
    pad = [f"{PAD_PREFIX}{i:05d}" for i in range(PAD_TO - len(targets))]
    if set(pad) & set(targets):
        raise SystemExit("a padding name collides with a MARS source name")
    specs = [
        ("v2", [s["source_name"] for s in v2["sources"]], True, "data/derived/sourceset_v2.json", v2["root"]),
        ("v3", [s["source_name"] for s in v3["sources"]], True, "data/derived/sourceset_v3.json", v3["root"]),
        ("mars_targets", targets, True, "the 1,122 MARS targets (data/derived/mars_sources.csv, join rule 1)", None),
        ("padded", targets + pad, False, "the MARS targets + synthetic leaves [SIM padding]", None),
    ]
    out = []
    for key, names, real, label, committed in specs:
        names = sorted(names)
        if CASE not in names:
            raise SystemExit(f"{CASE} is not in set {key}")
        sids = [source_id(n) for n in names]
        rt = hx(root(sids))
        if committed and rt != committed:
            raise SystemExit(f"set {key}: recomputed root {rt} differs from the committed {committed}")
        sid = source_id(CASE)
        prf = proof(sids, sid)
        if not verify(prf, bytes.fromhex(rt[2:]), leaf(sid)):
            raise SystemExit(f"set {key}: {CASE}'s proof does not verify")
        out.append({"key": key, "n": len(names), "real": real, "label": label, "root": rt,
                    "proof": [hx(p) for p in prf], "maxProofSteps": math.ceil(math.log2(len(names)))})
    if [s["n"] for s in out] != sorted(s["n"] for s in out) or out[-1]["n"] != PAD_TO:
        raise SystemExit("the sets must grow in size up to the padded set")
    return out


def _anvil_env() -> dict:
    from observer import pq

    env = {**os.environ, "PATH": f"{Path.home()}/.foundry/bin:" + os.environ["PATH"]}
    for org in ("OBS_A", "OBS_B", "OBS_C"):
        env[f"OBSERVER_{org[-1]}_PQ_HASH"] = pq.pk_hash(pq.keypair(pq.seed_for(org, 31337))[0])
    return env


def start_chain(port: int) -> subprocess.Popen:
    env = _anvil_env()
    if not shutil.which("anvil", path=env["PATH"]):
        raise SystemExit("anvil not found (Foundry, ~/.foundry/bin)")
    from web3 import Web3

    rpc = f"http://127.0.0.1:{port}"
    try:
        Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 1})).eth.chain_id
        raise SystemExit(f"port {port} is busy; stop the other node or pass --port")
    except SystemExit:
        raise
    except Exception:
        pass
    log = ROOT / "out" / "scale"
    log.mkdir(parents=True, exist_ok=True)
    p = subprocess.Popen(["anvil", "--port", str(port), "--timestamp", str(GENESIS), "--silent"], env=env,
                         stdout=open(log / "anvil.log", "w"), stderr=subprocess.STDOUT)
    for _ in range(100):
        try:
            Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 1})).eth.chain_id
            break
        except Exception:
            time.sleep(0.1)
    else:
        p.terminate()
        raise SystemExit("anvil did not start")
    subprocess.run(["cast", "rpc", "anvil_setBlockTimestampInterval", "1", "--rpc-url", rpc], env=env, check=True,
                   capture_output=True)
    r = subprocess.run(["forge", "script", "script/Deploy.s.sol", "--rpc-url", rpc, "--broadcast", "--slow", "-q"],
                       cwd=ROOT / "contracts", env=env, capture_output=True, text=True)
    if r.returncode != 0:
        p.terminate()
        raise SystemExit(f"Deploy.s.sol failed:\n{r.stdout}\n{r.stderr}")
    return p


def measure(rpc: str, sets: list[dict]) -> dict:
    import policy as policy_mod
    from observer import attest
    from observer.chain import Chain
    from observer.demo import Determinism
    from observer.service import Observer, anvil_key
    from policy import COVENANT_START

    ch = Chain(rpc)
    if ch.chain_id != 31337:
        raise SystemExit("the scale run uses Anvil's public test keys: Anvil (31337) only")
    det = Determinism(ch.chain_id, "MethaneClause scale [SIM] " + json.dumps(ch.dep, sort_keys=True))
    core = policy_mod.POLICY_V2["core"]
    tiers = [int(x) for x in core["rebateSchedule"]["tierAmountsWei"]]
    bond = int(json.loads((DERIVED / "model" / "bond_sizing_v3.json").read_text())["recommended_bond_wei"])
    seller_pk, buyer_pk, proposer_pk = anvil_key(1), anvil_key(2), anvil_key(6)
    from eth_account import Account

    buyer = Account.from_key(buyer_pk).address
    sid = attest.b32(hx(source_id(CASE)))

    def covenant(s: dict, label: str) -> tuple[str, object]:
        cpol = {**core, "bondWei": str(bond), "sourceSetRoot": s["root"], "scaleRun": s["key"]}
        params = (buyer, attest.b32(s["root"]), attest.b32(evidence_hash(cpol)), core["term"], COVENANT_START,
                  core["k"], core["challengeWindow"], core["contestWindow"], core["linkWindow"], core["minRateKgH"],
                  tiers)
        rcpt, _ = ch.send(ch.cov.functions.createCovenant(params), seller_pk, bond, label)
        if rcpt.status != 1:
            raise SystemExit(f"createCovenant failed for set {s['key']}")
        cid = "0x" + ch.cov.functions.covenantCount().call().to_bytes(32, "big").hex()
        if "0x" + bytes(ch.covenant(attest.b32(cid))["sourceSetRoot"]).hex() != s["root"]:
            raise SystemExit(f"set {s['key']}: the root on chain is not the set's root")
        return cid, rcpt

    def propose(cid: str, prf: list[str], label: str, expect: str | None = None):
        kw = {"covenant_id": cid, "covenant_start": COVENANT_START, "now": ch.now(), "min_rate_kgh": core["minRateKgH"]}
        # fresh observers per proposal: one observer process refuses to sign the same plume twice (DUPLICATE), and
        # here each covenant is a separate clause over the same real plume
        obs = [Observer(o, ch.chain_id, ch.cov.address) for o in ("OBS_A", "OBS_C")]
        signed = [o.attest(CASE, PLUME, **kw, **det.kw()) for o in obs]
        fn = ch.cov.functions.proposeEpisode(attest.b32(cid), sid, [attest.b32(x) for x in prf],
                                             attest.b32(signed[0]["evidenceHash"]),
                                             [attest.as_tuple(x["attestation"]) for x in signed],
                                             [bytes.fromhex(x["signature"][2:]) for x in signed])
        return ch.send(fn, proposer_pk, ch.dep["proposerBond"], label, expect)

    warm_cid, warm_c = covenant(sets[0], "warm-up createCovenant")
    warm_p, _ = propose(warm_cid, sets[0]["proof"], "warm-up proposeEpisode")
    if warm_p.status != 1:
        raise SystemExit("warm-up proposeEpisode failed")
    rows = []
    for i, s in enumerate(sets):
        cid, rc = covenant(s, f"createCovenant n={s['n']}")
        other = sets[i - 1] if i else sets[1]  # a path from a different set (v2 takes v3's)
        before = (ch.cov.functions.episodeCount().call(), ch.covenant(attest.b32(cid))["remaining"])
        bad, why = propose(cid, other["proof"], f"proposeEpisode n={s['n']} with the {other['key']} proof",
                           expect="NotCovered")
        after = (ch.cov.functions.episodeCount().call(), ch.covenant(attest.b32(cid))["remaining"])
        if bad.status != 0 or why != "NotCovered" or before != after:
            raise SystemExit(f"set {s['key']}: a proof from set {other['key']} was not rejected cleanly ({why})")
        rp, _ = propose(cid, s["proof"], f"proposeEpisode n={s['n']}")
        eid = ch.cov.functions.episodeCount().call()
        if rp.status != 1 or ch.episode(eid)["stateName"] != "PROPOSED":
            raise SystemExit(f"set {s['key']}: proposeEpisode with its own proof failed")
        rows.append({"set": s["key"], "n": s["n"], "real": s["real"], "label": s["label"], "root": s["root"],
                     "proofSteps": len(s["proof"]), "maxProofSteps": s["maxProofSteps"],
                     "createCovenantGas": rc.gasUsed, "proposeEpisodeGas": rp.gasUsed,
                     "proposeEpisodeCalldataBytes": len(bytes(ch.w3.eth.get_transaction(rp.transactionHash)["input"])),
                     "wrongProof": {"from": other["key"], "status": bad.status, "revert": why,
                                    "episodeCountAndBondUnchanged": True}})
    return {"warmUp": {"createCovenantGas": warm_c.gasUsed, "proposeEpisodeGas": warm_p.gasUsed},
            "rows": rows, "transactions": len(ch.txlog), "status0": sum(t["status"] == 0 for t in ch.txlog),
            "foundry": subprocess.run(["anvil", "--version"], env=_anvil_env(), capture_output=True,
                                      text=True).stdout.strip().splitlines()[0]}


def fit(rows: list[dict]) -> dict:
    """Least-squares slope of proposeEpisode gas on proof steps, and the spread of createCovenant gas."""
    xs, ys = [r["proofSteps"] for r in rows], [r["proposeEpisodeGas"] for r in rows]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    cc = [r["createCovenantGas"] for r in rows]
    return {"proposeEpisodeGasPerProofStep": round(slope), "createCovenantGasMin": min(cc),
            "createCovenantGasMax": max(cc)}


def sentence(d: dict) -> str:
    """The one sentence README / PDF / FOOTPRINT quote, rendered only from scale.json."""
    r, f = d["rows"], d["fit"]
    lo, hi = r[0], r[-1]
    cc = (f"{f['createCovenantGasMin']:,} gas" if f["createCovenantGasMin"] == f["createCovenantGasMax"]
          else f"{f['createCovenantGasMin']:,}–{f['createCovenantGasMax']:,} gas")
    return (f"Gas against facility-set size (Anvil, real transactions, `make scale` → "
            f"[`data/derived/scale.json`](data/derived/scale.json)): `createCovenant` costs {cc} for "
            f"{lo['n']:,} to {hi['n']:,} facilities (the set is one 32-byte root); `proposeEpisode` grows only with the "
            f"Merkle path, from {lo['proposeEpisodeGas']:,} gas ({lo['proofSteps']} steps, {lo['n']:,} facilities) to "
            f"{hi['proposeEpisodeGas']:,} ({hi['proofSteps']} steps, {hi['n']:,} [SIM padding]), about "
            f"{f['proposeEpisodeGasPerProofStep']:,} gas per step; a path has at most ⌈log2 n⌉ steps "
            f"({hi['maxProofSteps']} at {hi['n']:,}).")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--port", type=int, default=int(os.environ.get("ANVIL_PORT", "8961")))
    a = ap.parse_args()
    sets = build_sets()
    p = start_chain(a.port)
    try:
        m = measure(f"http://127.0.0.1:{a.port}", sets)
    finally:
        p.terminate()
        p.wait()
    rows = m["rows"]
    if max(x["createCovenantGas"] for x in rows) - min(x["createCovenantGas"] for x in rows) > 1_000:
        raise SystemExit("createCovenant gas moved by more than 1,000 with the set size: not flat")
    if [x["proposeEpisodeGas"] for x in rows] != sorted(x["proposeEpisodeGas"] for x in rows):
        raise SystemExit("proposeEpisode gas does not grow with the proof length")
    d = {"generatedBy": "pipeline/observer/scale.py (make scale)",
         "chain": f"local Anvil 31337, genesis {GENESIS} (pinned clock), Deploy.s.sol; {m['foundry']}",
         "facility": CASE, "plume": PLUME, "observers": ["OBS_A", "OBS_C"],
         "note": "Gas is Foundry's local schedule (Cancun); Ethereum Sepolia charged far more for new state "
                 "(data/derived/measurements.json). The padded set is the 1,122 MARS targets plus synthetic leaves "
                 f"named {PAD_PREFIX}<i> [SIM padding]. The warm-up pair pays one-time counter initialisation and is "
                 "not in rows.",
         "warmUp": m["warmUp"], "rows": rows, "fit": fit(rows),
         "transactions": m["transactions"], "status0": m["status0"]}
    d["sentence"] = sentence(d)
    write_json(OUT, d)
    print(f"wrote {OUT.relative_to(ROOT)}: {m['transactions']} transactions ({m['status0']} status 0)")
    for x in rows:
        print(f"  n={x['n']:>6,}  proof {x['proofSteps']:>2} steps  createCovenant {x['createCovenantGas']:>9,}  "
              f"proposeEpisode {x['proposeEpisodeGas']:>9,}")
    print(d["sentence"])


if __name__ == "__main__":
    sys.exit(main())
