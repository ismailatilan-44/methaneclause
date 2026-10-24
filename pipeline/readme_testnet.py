"""Render the README's public-testnet block from committed run files, so no address, link or number is typed by hand.

Rewrites the text between `<!-- testnet:begin -->` and `<!-- testnet:end -->` in README.md from
contracts/deployments/11155111.json, the DeployPool broadcasts, data/derived/sepolia_txs.json, testnet_run.json,
measurements.json and, for the refund side run by a judge test wallet, pipeline/cure_arc.py. Run by `make measure`.
The contract table carries the IncentivePool and the buyer's opening `fund` (its deploy transaction is checked against
the chain record, and shown on the verified address page); the gas line shows the rebate calls; every function,
deployment and setup row is in docs/FOOTPRINT.md (scripts/build_app_data.py renders it from the same measurements.json).
The run's rejections share one table row, each linked by its revert reason.
"""
from __future__ import annotations

import json
import re

import cure_arc
import policy
from common import DERIVED, ROOT

CHAIN = 11155111
SCAN = "https://sepolia.etherscan.io"
LABELS = {
    "createCovenant": "Seller [SIM] creates the covenant, posts the bond; start backdated to 2026-01-01 [SIM]",
    "reject_tamper": "rate edited 2,548 → 548 kg/h, signatures kept",
    "reject_self_attestation": "the seller's key as an observer",
    "reject_not_covered": "a Carbon Mapper twin's id",
    "reject_replay": "the same attestation twice",
    "proposeEpisode": "OBS_A + OBS_B sign the bundle → 2-of-3 episode",
    "finalize": "Challenge window over → anyone finalizes",
    "withdraw_buyer": "Buyer [SIM] withdraws the rebate",
    "withdraw_proposer": "Proposer withdraws its bond",
}
# The rebate calls only (README 25 KB budget); every function, deployment and setup row is in docs/FOOTPRINT.md
# (generated from the same measurements.json by scripts/build_app_data.py).
FUNCS = ["createCovenant", "proposeEpisode", "finalize"]


def short(url: str) -> str:
    h = url.rsplit("/", 1)[-1]
    return f"[`{h[:8]}…`]({url})"


def addr(a: str) -> str:
    return f"[`{a[:6]}…{a[-4:]}`]({SCAN}/address/{a}#code)"


def pool_txs(dep: dict) -> dict:
    """The pools' deploy transactions (from the DeployPool broadcasts, by created address) and the buyer's opening
    `fund` of the main pool, each confirmed with status 1 in data/derived/sepolia_txs.json (rebuilt from the chain);
    exit 1 if the record and the chain record disagree."""
    txs = json.loads((DERIVED / "sepolia_txs.json").read_text())["transactions"]
    status = {t["tx"]: t["status"] for t in txs}
    created = {}
    for f in sorted((ROOT / "contracts" / "broadcast" / "DeployPool.s.sol" / str(CHAIN)).glob("*-latest.json")):
        for t in json.loads(f.read_text())["transactions"]:
            if t.get("transactionType") == "CREATE" and t.get("contractName") == "IncentivePool":
                created[t["contractAddress"].lower()] = t["hash"]
    out = {}
    for key, a in (("main", dep["incentivePool"]), ("sandbox", dep["sandbox"]["incentivePool"])):
        h = created.get(a.lower())
        if not h or status.get(h) != 1:
            raise SystemExit(f"no confirmed deploy transaction for the {key} IncentivePool {a} in the chain record")
        out[key] = h
    my = DERIVED / "multiyear_run.json"  # the pool opened again for the three-year covenant (testnet.py multiyear)
    later = {s["tx"].rsplit("/", 1)[-1].lower() for s in json.loads(my.read_text())["steps"]} if my.exists() else set()
    fund = [t for t in txs if t["label"] == "pool_fund" and t["to"] == dep["incentivePool"].lower() and t["status"] == 1
            and t["tx"].lower() not in later]
    if len(fund) != 1:
        raise SystemExit("the chain record has no single confirmed pool_fund opening the main IncentivePool")
    out["fund"] = fund[0]
    return out


def render() -> str:
    dep = json.loads((ROOT / "contracts" / "deployments" / f"{CHAIN}.json").read_text())
    run = json.loads((DERIVED / "testnet_run.json").read_text())
    m = json.loads((DERIVED / "measurements.json").read_text())
    sb = dep["sandbox"]
    pt = pool_txs(dep)
    tx_url = f"{SCAN}/tx/"
    out = ["| Contract | Main (private keys) | Judge sandbox [SIM] (published keys) |", "|---|---|---|",
           f"| `MethaneCovenant` | {addr(dep['methaneCovenant'])} | {addr(sb['methaneCovenant'])} |",
           f"| `KeyRegistry` (admin renounced) | {addr(dep['keyRegistry'])} | {addr(sb['keyRegistry'])} |",
           f"| `IncentivePool` + {len(dep['incentivePoolLibraries'])} libraries | {addr(dep['incentivePool'])}, "
           f"buyer [SIM] funds {int(pt['fund']['valueWei']) / 1e18:g} ETH "
           f"{short(tx_url + pt['fund']['tx'])} | {addr(sb['incentivePool'])} |",
           "",
           "**The operator's run, main deployment** (`make testnet-run`, `testnet-finalize`; record: "
           "[`data/derived/testnet_run.json`](data/derived/testnet_run.json)"
           f"{', rebuilt from the chain' if run.get('recoveredFromChain') else ''}"
           "):", "",
           "| Step | Result (status 0: bond, episode count unchanged) | Transaction |", "|---|---|---|"]
    rejected = [s for s in run["steps"] if s["status"] == 0]
    for s in run["steps"]:
        if s["step"] == "withdraw_proposer":  # kept in testnet_run.json; omitted here for the README size budget
            continue
        if s["status"] == 0:
            if s is rejected[0]:  # one row for every rejection, in run order, each linked by its reason (25 KB budget)
                what = "; ".join(LABELS.get(x["step"], x["step"]) for x in rejected)
                links = " ".join(f"[`{x.get('reason')}`]({x['tx']})" for x in rejected)
                out.append(f"| Refused: {what} | status 0 | {links} |")
            continue
        elif s["step"] == "proposeEpisode":
            res = f"episode {s.get('episodeId')} PROPOSED, tier {s.get('tier')}"
        elif s["step"] == "finalize":
            res = "CONFIRMED"
        elif s["step"].startswith("withdraw"):
            res = f"{int(s['amountWei']) / 1e18:g} ETH paid out" if "amountWei" in s else "paid out"
        else:
            res = f"bond {int(s['bondWei']) / 1e18:g} ETH" if "bondWei" in s else "ok"
        out.append(f"| {LABELS.get(s['step'], s['step'])} | {res} | {short(s['tx'])} |")
    lat, tx, usd = m["latency_seconds"], m["transactions"], m["usd_assumption"]
    if not run.get("recoveredFromChain") and run.get("observerReceipts"):
        modes = sorted({r["mode"] for r in run["observerReceipts"]})
        out += ["", f"Observer fetch mode in this run: {', '.join(modes)} (receipts in the record)."]
    out += ["", cure_arc.readme_line(cure_arc.arc(), policy.POLICY_V2_SANDBOX["pool"]["installments"][0][0])]
    since = [b for b in m.get("latency_blocks", []) if b["n"] and b["label"] != lat.get("label")]
    when = f", {lat['label'].split(',')[0]}" if lat.get("label") else ""
    if "raw samples" in lat.get("label", ""):  # the dated block's samples are gone; measurements.json says so
        when += ", raw samples not kept"
    more = "".join(f"; {b['n']} logged since: p50 {b['p50']} s" for b in since)
    out += ["", f"**Measured** (`make measure` → [`data/derived/measurements.json`](data/derived/measurements.json)): "
            f"{tx['logged']} Sepolia transactions from our addresses (`make sepolia-txs`); "
            f"{tx['expected_reverts']} intended reverts, {tx['unexpected_failures']} unintended "
            f"failure{'s' if tx['unexpected_failures'] != 1 else ''}, causes in the file. Latency, send → receipt (public "
            f"RPC): **p50 {lat['p50']} s, p90 {lat['p90']} s** (n = {lat['n']}{when}{more}). Evidence bundle: "
            f"{m['bundle_bytes_jcs']} bytes off-chain, only its 32-byte hash on chain."]
    calls = []
    for f in FUNCS:
        v = m["functions"].get(f)
        if not v:
            continue
        cost = f" (${v['usd_at_stated_gwei']:.2f})" if v.get("usd_at_stated_gwei") is not None else ""
        calls.append(f"`{f}` {v['gas_sepolia_median']:,}{cost}")
    rest = len(set(m["functions"]) - set(FUNCS))
    out += ["", f"**Gas per call**, Sepolia median (USD at {usd['stated_gwei']:g} gwei and Chainlink's ETH "
            f"${usd['eth_usd']:,}): {', '.join(calls)}; Anvil gas and the "
            f"{rest} other calls: [`FOOTPRINT`](docs/FOOTPRINT.md)."]
    return "\n".join(out)


def main() -> None:
    p = ROOT / "README.md"
    s = p.read_text()
    block = f"<!-- testnet:begin (generated by pipeline/readme_testnet.py) -->\n{render()}\n<!-- testnet:end -->"
    s2, n = re.subn(r"<!-- testnet:begin.*?<!-- testnet:end -->", lambda _: block, s, flags=re.S)
    if n != 1:
        raise SystemExit("README.md has no testnet block markers")
    p.write_text(s2)
    print("README testnet block rendered")


if __name__ == "__main__":
    main()
