"""D4 measurements -> data/derived/measurements.json (picked up by audit.py; README table quotes it).

Runs offline from tracked files only (the Chainlink price read is the one network call; --offline keeps the last one):
  data/derived/sepolia_txs.json              every Sepolia transaction our addresses sent, rebuilt from the chain by
                                             `make sepolia-txs` (pipeline/sepolia_txs.py): status, gas, label, revert
  data/derived/sepolia_txlog.jsonl           what our scripts logged as they sent (submit -> receipt seconds)
  data/derived/sepolia_latency_history.json  latency blocks whose raw samples are gone (figures copied verbatim)
  data/derived/demo_run.json                 the same functions on Anvil (Cancun gas schedule)

The only hand-written text is the cause noted for each unintended failure in CAUSES; status, gas and the revert reason
come from the chain. Latency cannot be rebuilt from the chain (it needs the submit time), so it is reported in dated
blocks: the 15 Oct block (its raw samples were not kept) and the samples logged in the tracked file
since; a block is never merged with another or estimated.

USD is a stated what-if: gas units x STATED_GWEI x ETH/USD read at run time from the Chainlink ETH/USD feed on
Ethereum mainnet (aggregator address, round and update time recorded, so anyone can check the price). Testnet ETH has
no value; what we actually paid per gas on Sepolia is reported separately.

    python measure.py [--offline]
    JUDGE_PK=0x.. python measure.py --sample 12 --rpc <sepolia rpc>   # add 12 zero-value self-transfers first
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone

from canon import canonical_bytes
from common import DERIVED, ROOT, now_utc, write_json

CHAIN = 11155111
STATED_GWEI = 1.0
MAINNET_RPC = "https://ethereum-rpc.publicnode.com"
CHAINLINK_ETH_USD = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"  # Chainlink ETH / USD aggregator, mainnet, 8 decimals
OUT = DERIVED / "measurements.json"
TXS = DERIVED / "sepolia_txs.json"
TXLOG = DERIVED / "sepolia_txlog.jsonl"
HISTORY = DERIVED / "sepolia_latency_history.json"
# Every block of HISTORY is one whose raw samples are gone; its label says so and nothing more.
HISTORY_LABEL = "measured {day}, raw samples not kept"
# Why each unintended failure happened (status and gas are read from the chain; only this text is written by hand).
CAUSES = {
    "0xcfeabcf63c9663279bb547bfc1c46d0778a2aaf13eee097ecbcd1d507f76018b":
        "judge test wallet's first sandbox-seller funding: fixed 21,000 gas, below Sepolia's fresh-account price "
        "(fixed: transfers now use eth_estimateGas)",
    "0xe3206db767a05bd4aea02fa470d1a721ed99830e3e47e4a8dc33f52bdf019569":
        "first deploy attempt, KeyRegistry create: out of gas on Foundry's local Cancun-schedule estimate "
        "(fixed: higher limit)",
}


def jsonl(path) -> list[dict]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def pct(xs: list[float], q: float) -> float:
    xs = sorted(xs)
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (i - lo), 2)


def epoch(iso: str) -> int:
    return int(datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())


def iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample(n: int, rpc: str) -> None:
    from eth_account import Account

    import txlog
    from observer.chain import Chain

    ch = Chain(rpc, sandbox=True)
    me = Account.from_key(os.environ["JUDGE_PK"]).address
    for _ in range(n):
        ch.transfer(os.environ["JUDGE_PK"], me, 0, "latency_sample")
    path = ROOT / "out" / "measure" / f"{ch.chain_id}.samples.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        for t in ch.txlog:
            f.write(json.dumps({"command": "sample", **t}) + "\n")
    txlog.append(ch.txlog, ch.chain_id, "sample")
    print(f"sampled {n} transactions")


def lead_bundle_bytes() -> int:
    from observer.bundle import build_bundle
    from observer.service import load_facility

    facility, _, cf = load_facility("TKM_S_276")
    plume = next(p for p in cf["cm_plumes_within_radius"] if p["plume_id"] == "tan20260611t083132c23s4001-AB")
    b = build_bundle(dict(plume), covenant_id="0x" + "00" * 31 + "01", source_id=facility["sourceId"],
                     source_name=facility["source_name"],
                     content_sha256=cf["cm_sources"][plume["source_name"]]["content_sha256"])
    return len(canonical_bytes(b))


def intended_revert(t: dict) -> bool:
    """A status-0 transaction that a contract refused with one of its named errors (the rejections shown on purpose).
    Out of gas or an unnamed failure is unintended."""
    return t["status"] == 0 and bool(t.get("revert")) and t["revert"] != "OutOfGas" and \
        not t["revert"].startswith("unnamed")


def gas_table(txs: list[dict], demo_gas: dict) -> dict:
    gas: dict[str, list[int]] = defaultdict(list)
    for t in txs:
        if t["label"] == "latency_sample":
            continue
        if t["status"] == 1:
            gas[t["label"]].append(t["gasUsed"])
        elif intended_revert(t):
            gas[f"{t['label']}_reverted"].append(t["gasUsed"])
    return {label: {"n": len(xs), "gas_sepolia_median": int(statistics.median(xs)),
                    "gas_anvil_cancun_rounded_1k": demo_gas.get(label)} for label, xs in sorted(gas.items())}


def counts(txs: list[dict]) -> dict:
    failed = [t for t in txs if t["status"] == 0 and not intended_revert(t)]
    return {
        "what": "every transaction our addresses sent on Sepolia, rebuilt from the chain (data/derived/sepolia_txs.json)",
        "logged": len(txs),
        "expected_reverts": sum(intended_revert(t) for t in txs),
        "unexpected_failures": len(failed),
        "unexpected_failures_detail": [{"tx": t["tx"], "label": t["label"], "revert": t["revert"],
                                        "cause": CAUSES.get(t["tx"], "no cause recorded")} for t in failed],
        "by_kind": {
            "deploy_scripts": sum(1 for t in txs if t["labelSource"].startswith("contracts/broadcast/")),
            "latency_samples": sum(1 for t in txs if t["label"] == "latency_sample"),
            "eth_transfers": sum(1 for t in txs if t["label"].startswith("transfer")),
            "protocol_calls": sum(1 for t in txs if not t["labelSource"].startswith("contracts/broadcast/")
                                  and t["label"] != "latency_sample" and not t["label"].startswith("transfer")),
        },
    }


# Labels the 15 Oct scripts used, mapped to the chain-derived labels of pipeline/sepolia_txs.py (names only).
OLD_LABELS = {"fund_bogus_proposer": "transfer_new_account", "fund_judge_controller_gas": "transfer_new_account",
              "fund_sandbox_seller": "transfer_new_account", "fund_sandbox_seller_gas": "transfer",
              "proposeEpisode_bogus": "proposeEpisode", "registerKey_judge": "registerKey"}


def reconcile(txs: list[dict], block: dict) -> dict:
    """The same counts as of an earlier measurement, per function, so the old figure can be checked against the chain.
    The old count was the scripts' own logs; deploy-script transactions were in it only when they failed."""
    cut = epoch(block["measuredAt"])
    before = [t for t in txs if t["timestamp"] <= cut]
    c = counts(before)
    old = block["transactionsAtThatTime"]
    scripted = [t for t in before if not t["labelSource"].startswith("contracts/broadcast/")]
    then_fn: Counter = Counter()
    for k, n in block["functionsAtThatTime"].items():
        if not k.startswith("deploy_"):
            then_fn[OLD_LABELS.get(k, k)] += n
    chain_fn: Counter = Counter()
    for t in scripted:
        if t["status"] == 1 and t["label"] != "latency_sample":
            chain_fn[t["label"]] += 1
        elif intended_revert(t):
            chain_fn[f"{t['label']}_reverted"] += 1
    # latency samples and failed transfers were logged but are not in the function table
    then_other, chain_other = old["logged"] - sum(then_fn.values()), len(scripted) - sum(chain_fn.values())
    diff = {k: chain_fn[k] - then_fn[k] for k in sorted(set(then_fn) | set(chain_fn)) if chain_fn[k] != then_fn[k]}
    if chain_other != then_other:
        diff["samples_and_failed_transfers"] = chain_other - then_other
    return {
        "asOf": block["measuredAt"],
        "then": {"logged": old["logged"], "expected_reverts": old["expected_reverts"],
                 "unexpected_failures": old["unexpected_failures"]},
        "chain": {"sent": len(before), "sent_outside_deploy_scripts": len(scripted),
                  "expected_reverts": c["expected_reverts"], "unexpected_failures": c["unexpected_failures"],
                  "failed_deploy_transactions": sum(1 for t in before if t["status"] == 0
                                                    and t["labelSource"].startswith("contracts/broadcast/"))},
        "not_in_then_logs": len(scripted) - old["logged"],
        "not_in_then_logs_by_function": diff,
        "every_logged_transaction_is_on_chain": all(v >= 0 for v in diff.values()),
        "deployer_funding_transfers": sum(1 for t in scripted if t["fromRole"] == "deployer"),
    }


def deploy_runs(txs: list[dict]) -> dict:
    runs: dict[str, list[dict]] = defaultdict(list)
    for t in txs:
        if t["labelSource"].startswith("contracts/broadcast/"):
            runs[t["labelSource"]].append(t)
    return {f: {"script": f.split("/")[2], "firstBlockTime": iso(min(t["timestamp"] for t in ts)), "txs": len(ts),
                "failed": sum(t["status"] == 0 for t in ts), "gasUsed_total": sum(t["gasUsed"] for t in ts)}
            for f, ts in sorted(runs.items())}


def latency(txs: list[dict], log: list[dict], history: dict) -> tuple[dict, list[dict]]:
    """Headline block + every block. A logged sample counts only if its hash is on chain (sepolia_txs.json) and it was
    sent after the newest historical block (those samples are already in that block)."""
    on_chain = {t["tx"]: t for t in txs}
    hist = sorted(history["blocks"], key=lambda b: b["measuredAt"])
    after = epoch(hist[-1]["measuredAt"]) if hist else 0
    secs = [r["seconds"] for r in log if r.get("seconds") is not None and r["tx"] in on_chain
            and on_chain[r["tx"]]["timestamp"] > after]
    blocks = [{"label": HISTORY_LABEL.format(day=b["measuredAt"][:10])}
              | {k: b[k] for k in ("measuredAt", "n", "p50", "p90", "what")} | {"source": b["source"]} for b in hist]
    if secs:
        blocks.append({"label": f"logged since {hist[-1]['measuredAt'][:10]} in data/derived/sepolia_txlog.jsonl"
                       if hist else "logged in data/derived/sepolia_txlog.jsonl",
                       "measuredAt": None, "n": len(secs), "p50": pct(secs, 0.5), "p90": pct(secs, 0.9),
                       "what": "wall clock from eth_sendRawTransaction to receipt via a public RPC (12 s slots)"})
    head = max(blocks, key=lambda b: b["n"]) if blocks else {"n": 0, "p50": None, "p90": None, "what": "", "label": ""}
    return {k: head[k] for k in ("n", "p50", "p90", "what", "label")}, blocks


def chainlink_price() -> tuple[float | None, dict | None, str | None]:
    try:
        from web3 import Web3

        feed = Web3(Web3.HTTPProvider(MAINNET_RPC)).eth.contract(address=CHAINLINK_ETH_USD, abi=[{
            "name": "latestRoundData", "type": "function", "stateMutability": "view", "inputs": [],
            "outputs": [{"name": n, "type": t} for n, t in (("roundId", "uint80"), ("answer", "int256"),
                        ("startedAt", "uint256"), ("updatedAt", "uint256"), ("answeredInRound", "uint80"))]}])
        rid, answer, _, updated, _ = feed.functions.latestRoundData().call()
        return round(answer / 1e8, 2), {"roundId": str(rid), "updatedAt": updated}, None
    except Exception as e:  # recorded, not hidden
        return None, None, str(e)[:120]


def summarise(txs_doc: dict, log: list[dict], history: dict, demo_gas: dict, usd_assumption: dict,
              bundle_bytes: int, measured_at: str) -> dict:
    txs = txs_doc["transactions"]
    price = usd_assumption.get("eth_usd")
    funcs = gas_table(txs, demo_gas)
    for v in funcs.values():
        v["usd_at_stated_gwei"] = None if price is None else round(
            v["gas_sepolia_median"] * usd_assumption["stated_gwei"] * 1e-9 * price, 4)
    head, blocks = latency(txs, log, history)
    paid = sorted(t["effectiveGasPrice"] for t in txs)
    return {
        "chain": "ethereum-sepolia-11155111",
        "measuredAt": measured_at,
        "source": {"transactions": "data/derived/sepolia_txs.json", "fetchedAt": txs_doc["fetchedAt"],
                   "senders": len(txs_doc["senders"])},
        "functions": funcs,
        "deploy_runs": deploy_runs(txs),
        "latency_seconds": head,
        "latency_blocks": blocks,
        "transactions": counts(txs),
        "reconciliation": [reconcile(txs, b) for b in history["blocks"]],
        "bundle_bytes_jcs": bundle_bytes,
        "usd_assumption": usd_assumption,
        "paid_on_sepolia_gwei": {"min": round(paid[0] / 1e9, 9), "median": round(statistics.median(paid) / 1e9, 9),
                                 "max": round(paid[-1] / 1e9, 9)} if paid else None,
        "idempotent_resubmission": "evidenceSeen[evidenceHash] -> DuplicateEvidence (make demo step 'Resubmit')",
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sample", type=int, default=0)
    ap.add_argument("--rpc", default=os.environ.get("RPC_URL", "https://ethereum-sepolia-rpc.publicnode.com"))
    ap.add_argument("--offline", action="store_true", default=os.environ.get("OFFLINE") == "1",
                    help="no network: keep the ETH/USD read already in measurements.json")
    a = ap.parse_args()
    if a.sample:
        sample(a.sample, a.rpc)
    if not TXS.exists():
        raise SystemExit(f"{TXS.relative_to(ROOT)} missing: run `make sepolia-txs` (network, keyless)")
    measured_at = now_utc()
    if a.offline:  # the ETH/USD read is the last one, so is its time
        last = json.loads(OUT.read_text()) if OUT.exists() else {}
        prev = last.get("usd_assumption", {})
        usd = prev or {"stated_gwei": STATED_GWEI, "eth_usd": None, "eth_usd_round": None, "error": "offline"}
        measured_at = last.get("measuredAt", measured_at) if prev else measured_at
    else:
        price, rnd, err = chainlink_price()
        usd = {"stated_gwei": STATED_GWEI, "eth_usd": price, "eth_usd_round": rnd, "error": err,
               "eth_usd_source": f"Chainlink ETH/USD {CHAINLINK_ETH_USD} (Ethereum mainnet)"}
    out = summarise(json.loads(TXS.read_text()), jsonl(TXLOG), json.loads(HISTORY.read_text()),
                    json.loads((DERIVED / "demo_run.json").read_text())["gas_rounded_1k"], usd, lead_bundle_bytes(),
                    measured_at)
    write_json(OUT, out)
    print(json.dumps({k: out[k] for k in ("latency_blocks", "transactions", "reconciliation", "bundle_bytes_jcs")},
                     indent=1))
    for k, v in out["functions"].items():
        print(f"  {k:28} n={v['n']:2}  sepolia {v['gas_sepolia_median']:>10,}  anvil {v['gas_anvil_cancun_rounded_1k']}"
              f"  ${v['usd_at_stated_gwei']}")


if __name__ == "__main__":
    main()
