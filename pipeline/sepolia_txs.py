"""Rebuild every Ethereum Sepolia transaction our addresses sent, from public keyless reads -> data/derived/sepolia_txs.json.

Network (`make sepolia-txs`); pipeline/measure.py then runs offline from the file this writes.

Which addresses: every role in contracts/deployments/11155111.json (main and judge sandbox), the senders of every
transaction hash already in tracked files (Foundry broadcasts, data/derived/testnet_run.json,
data/derived/sepolia_txlog.jsonl), and, transitively, every account without code that one of them sent ETH to (the judge
test wallet, the bogus proposers of the challenge walk-through). No key is used anywhere.

How: an Etherscan-compatible explorer index that needs no key (Routescan; Blockscout as fallback) lists each address's
transactions; the list is accepted only if, for every sender, the number of transactions it sent equals its nonce read
from a public RPC (a nonce counts every mined transaction, failed ones included), so nothing can be missing. Every
field written comes from the RPC itself (transaction, receipt, block), not from the index. A failed transaction's reason
is read by replaying its calldata at the block before (an archive read) and naming the 4-byte error from our contract
ABIs; one that used its whole gas limit is "OutOfGas".

Labels: a transaction in a Foundry broadcast is `deploy_<Contract>` (create) or `deploy_<function>` (call), calls to the
IncentivePool `pool_<function>`; any other call is its function name decoded from the target contract's ABI; a plain ETH
transfer is `transfer_new_account` when no earlier successful transaction touched the recipient (Sepolia charges extra
for creating an account), `latency_sample` when an address pays itself (measure.py --sample), else `transfer`.

    python sepolia_txs.py [--rpc URL] [--index routescan|blockscout]
"""
from __future__ import annotations

import argparse
import json
import time

import requests
from eth_utils import keccak

from common import DERIVED, ROOT, UA, now_utc, write_json

CHAIN = 11155111
OUT = DERIVED / "sepolia_txs.json"
DEPLOYMENTS = ROOT / "contracts" / "deployments" / f"{CHAIN}.json"
BROADCASTS = ROOT / "contracts" / "broadcast"
ABI_DIR = ROOT / "contracts" / "out"
# Public RPCs that serve archive state (old receipts and eth_call at old blocks), tried in order; no key.
RPCS = ["https://sepolia.gateway.tenderly.co", "https://1rpc.io/sepolia", "https://ethereum-sepolia-rpc.publicnode.com"]
INDEXES = {
    "routescan": "https://api.routescan.io/v2/network/testnet/evm/11155111/etherscan/api",
    "blockscout": "https://eth-sepolia.blockscout.com/api",
}
CONTRACTS = ("MethaneCovenant", "KeyRegistry", "IncentivePool", "PoolCorroboration", "PoolEpisodes", "PoolInstallments",
             "PoolSetup", "PoolVerify")
# Role names for senders that the deployment record does not name; each must be found by the discovery below, or the
# run fails (a name never stands in for a missing address).
EXTRA_ROLES = {
    "0xdafe3fbf72b89851cb8d2266a49c7b3fceb92a0b": "proposer (operator run, PROPOSER_PK)",
    "0xbb1e155f81ddbd668d1afeabb24da5b2c9a59412": "judge test wallet (JUDGE_PK; funded by make fund-judge)",
    "0xd2218912f3a84767d0790694daf9871ba869774e": "judge test wallet, cure arc (JUDGE_PK; funded by make fund-judge)",
}


class Rpc:
    def __init__(self, urls: list[str]):
        self.urls = urls
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "content-type": "application/json"})

    def __call__(self, method: str, params: list, allow_error: bool = False):
        last = None
        for url in self.urls:
            for attempt in range(4):
                try:
                    r = self.s.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
                                    timeout=60)
                    j = r.json()
                except (requests.RequestException, ValueError) as e:
                    last = e
                    time.sleep(1 + attempt)
                    continue
                if "error" in j:
                    if allow_error and (j["error"].get("code") == 3 or "revert" in str(j["error"]).lower()):
                        return j["error"]  # an eth_call that reverted: the caller reads the error data
                    last = RuntimeError(f"{url} {method}: {j['error']}")
                    break  # try the next RPC (pruned history, rate limit)
                if j.get("result") is None and method in ("eth_getTransactionReceipt", "eth_getTransactionByHash"):
                    last = RuntimeError(f"{url} {method}: null (pruned?)")
                    break
                return j["result"]
        raise RuntimeError(f"no public RPC answered {method}{params[:1]}: {last}")


def index_txlist(index: str, address: str) -> list[dict]:
    """Every normal transaction touching `address`, from a keyless Etherscan-compatible index (paged)."""
    out, page = [], 1
    while True:
        for attempt in range(8):
            r = requests.get(INDEXES[index], params={
                "module": "account", "action": "txlist", "address": address, "startblock": 0, "endblock": 99_999_999,
                "page": page, "offset": 1000, "sort": "asc"}, headers={"User-Agent": UA}, timeout=60)
            if r.status_code == 200:
                break
            time.sleep(3 * (attempt + 1))  # rate limited: back off
        else:
            raise RuntimeError(f"{index} index: HTTP {r.status_code} for {address}")
        j = r.json()
        rows = j.get("result") if isinstance(j.get("result"), list) else []
        if not rows and j.get("message") not in ("OK", "No transactions found"):
            raise RuntimeError(f"{index} index refused {address}: {j}")
        out += rows
        if len(rows) < 1000:
            return out
        page += 1
        time.sleep(0.6)


def _sig(e: dict) -> str:
    def t(i):
        if i["type"].startswith("tuple"):
            return "(" + ",".join(t(c) for c in i["components"]) + ")" + i["type"][5:]
        return i["type"]
    return f"{e['name']}({','.join(t(i) for i in e['inputs'])})"


def load_abis() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """{contract: {selector: function name}}, {error selector: error name} from Foundry's build output."""
    funcs: dict[str, dict[str, str]] = {}
    errors: dict[str, str] = {}
    for c in CONTRACTS:
        p = ABI_DIR / f"{c}.sol" / f"{c}.json"
        if not p.exists():
            raise SystemExit(f"{p.relative_to(ROOT)} missing: run `forge build` in contracts/ first")
        abi = json.loads(p.read_text())["abi"]
        funcs[c] = {"0x" + keccak(text=_sig(e))[:4].hex(): e["name"] for e in abi if e["type"] == "function"}
        errors |= {"0x" + keccak(text=_sig(e))[:4].hex(): e["name"] for e in abi if e["type"] == "error"}
    return funcs, errors


def tracked_hashes() -> dict[str, dict]:
    """tx hash -> broadcast entry (or {} for hashes from the run records), from every tracked file that holds hashes."""
    out: dict[str, dict] = {}
    for f in sorted(BROADCASTS.glob(f"*/{CHAIN}/*.json")):
        for t in json.loads(f.read_text())["transactions"]:
            if t.get("hash"):
                out[t["hash"].lower()] = {**t, "_file": str(f.relative_to(ROOT))}
    run = DERIVED / "testnet_run.json"
    if run.exists():
        for s in json.loads(run.read_text())["steps"]:
            out.setdefault(s["tx"].rsplit("/", 1)[-1].lower(), {})
    log = DERIVED / "sepolia_txlog.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            if line.strip():
                out.setdefault(json.loads(line)["tx"].lower(), {})
    return out


def roles() -> dict[str, str]:
    dep = json.loads(DEPLOYMENTS.read_text())
    out = {}
    for k in ("deployer", "buyer", "seller", "observerA", "observerB", "observerC"):
        out[dep[k].lower()] = k
    for k in ("buyer", "seller", "observer1", "observer2", "judgeController", "independentController"):
        out[dep["sandbox"][k].lower()] = f"sandbox.{k}"
    return out


def contract_names() -> dict[str, str]:
    """address -> contract name for every contract we deployed (broadcast creates + the deployment record)."""
    out = {}
    for f in sorted(BROADCASTS.glob(f"*/{CHAIN}/*.json")):
        for t in json.loads(f.read_text())["transactions"]:
            if t["transactionType"].startswith("CREATE") and t.get("contractAddress"):
                out[t["contractAddress"].lower()] = t["contractName"]
    dep = json.loads(DEPLOYMENTS.read_text())
    for d in (dep, dep["sandbox"], dep.get("sandboxPrevious", {})):
        for key, name in (("methaneCovenant", "MethaneCovenant"), ("keyRegistry", "KeyRegistry"),
                          ("incentivePool", "IncentivePool")):
            if d.get(key):
                out[d[key].lower()] = name
    return out


def discover(index: str, rpc: Rpc, seeds: dict[str, str]) -> tuple[dict[str, dict], list[dict], dict[str, list]]:
    """Closure over seeds + EOAs they funded. Returns (index rows by hash, sender summaries, index rows per address)."""
    todo, done = list(seeds), set()
    rows_by_hash: dict[str, dict] = {}
    per_addr: dict[str, list] = {}
    senders = []
    while todo:
        a = todo.pop(0)
        if a in done:
            continue
        done.add(a)
        rows = index_txlist(index, a)
        per_addr[a] = rows
        sent = [r for r in rows if r["from"].lower() == a]
        nonce = int(rpc("eth_getTransactionCount", [a, "latest"]), 16)
        if len(sent) != nonce:
            raise SystemExit(f"{a}: the {index} index lists {len(sent)} sent transactions but the nonce is {nonce}; "
                             "refusing to write an incomplete history (retry, or --index blockscout)")
        senders.append({"address": a, "role": seeds.get(a), "nonce": nonce})
        for r in sent:
            rows_by_hash[r["hash"].lower()] = r
            to = (r.get("to") or "").lower()
            if to and int(r["value"]) > 0 and to != a and to not in done and to not in todo:
                if rpc("eth_getCode", [to, "latest"]) == "0x":
                    todo.append(to)
                    seeds.setdefault(to, f"funded by {seeds.get(a) or a}")
        time.sleep(0.6)
    return rows_by_hash, senders, per_addr


def build(rpc: Rpc, index: str) -> dict:
    funcs, errors = load_abis()
    known = tracked_hashes()
    seeds = roles()
    for h, b in known.items():  # senders of every hash we already hold
        frm = (b.get("transaction") or {}).get("from")
        if not frm:
            try:
                frm = rpc("eth_getTransactionByHash", [h])["from"]
            except RuntimeError:  # not on chain: reported below as a tracked hash not found
                continue
        seeds.setdefault(frm.lower(), None)
    for a, name in EXTRA_ROLES.items():
        if seeds.get(a) is None:
            seeds[a] = name
    rows_by_hash, senders, per_addr = discover(index, rpc, seeds)
    for a, name in EXTRA_ROLES.items():
        if a not in per_addr:
            raise SystemExit(f"{name} {a} was not found by discovery")
    for s in senders:
        s["role"] = seeds.get(s["address"]) or EXTRA_ROLES.get(s["address"])
    missing = sorted(h for h in known if h not in rows_by_hash)
    names = contract_names()
    first_touch: dict[str, tuple[int, int]] = {}  # first successful transaction touching each address (a failed one
    for a, rows in per_addr.items():              # creates nothing)
        ok = [r for r in rows if r.get("txreceipt_status", "1" if r.get("isError") == "0" else "0") == "1"]
        if ok:
            first_touch[a] = min((int(r["blockNumber"]), int(r["transactionIndex"])) for r in ok)
    blocks: dict[int, int] = {}
    txs = []
    for h in sorted(rows_by_hash, key=lambda x: (int(rows_by_hash[x]["blockNumber"]),
                                                 int(rows_by_hash[x]["transactionIndex"]))):
        tx = rpc("eth_getTransactionByHash", [h])
        rc = rpc("eth_getTransactionReceipt", [h])
        bn = int(rc["blockNumber"], 16)
        if bn not in blocks:
            blocks[bn] = int(rpc("eth_getBlockByNumber", [hex(bn), False])["timestamp"], 16)
        frm, to = tx["from"].lower(), (tx.get("to") or "").lower() or None
        inp = tx["input"]
        b = known.get(h, {})
        contract = names.get(to) if to else None
        if b.get("_file"):
            if b["transactionType"].startswith("CREATE"):
                label = f"deploy_{b['contractName']}"
            elif b.get("contractName") == "IncentivePool":
                label = f"pool_{b['function'].split('(')[0]}"
            else:
                label = f"deploy_{b['function'].split('(')[0]}"
            contract = b.get("contractName") or contract
        elif to is None:
            label = "deploy_unknown"
        elif inp in ("0x", ""):
            if to == frm:
                label = "latency_sample"
            elif first_touch.get(to, (1 << 62, 0)) >= (bn, int(rc["transactionIndex"], 16)):  # none succeeded before
                label = "transfer_new_account"
            else:
                label = "transfer"
        elif contract:
            fn = funcs.get(contract, {}).get(inp[:10], inp[:10])
            label = f"pool_{fn}" if contract == "IncentivePool" else fn
        else:
            label = f"call_{inp[:10]}"
        status = int(rc["status"], 16)
        gas_used, gas_limit = int(rc["gasUsed"], 16), int(tx["gas"], 16)
        revert = None
        if status == 0:
            if gas_used >= gas_limit:
                revert = "OutOfGas"
            else:
                call = {"from": tx["from"], "data": inp, "value": tx["value"], "gas": tx["gas"]}
                if to:
                    call["to"] = tx["to"]
                err = rpc("eth_call", [call, hex(bn - 1)], allow_error=True)
                data = err.get("data") if isinstance(err, dict) else None
                data = data.get("data") if isinstance(data, dict) else data
                revert = errors.get(str(data)[:10], f"unnamed:{str(data)[:10]}") if data else "unnamed"
        txs.append({
            "tx": h, "block": bn, "timestamp": blocks[bn], "index": int(rc["transactionIndex"], 16),
            "from": frm, "fromRole": seeds.get(frm), "to": to, "toRole": seeds.get(to) if to else None,
            "contract": contract, "selector": inp[:10] if len(inp) >= 10 else None, "label": label,
            "labelSource": b.get("_file") or ("abi" if contract and inp not in ("0x", "") else "chain"),
            "nonce": int(tx["nonce"], 16), "valueWei": str(int(tx["value"], 16)), "status": status,
            "gasLimit": gas_limit, "gasUsed": gas_used, "effectiveGasPrice": int(rc["effectiveGasPrice"], 16),
            "revert": revert,
        })
        time.sleep(0.05)
    return {
        "chain": "ethereum-sepolia-11155111",
        "fetchedAt": now_utc(),
        "method": {"index": f"{index} {INDEXES[index]} (keyless; lists hashes only)",
                   "rpc": f"{rpc.urls[0]} (keyless; every field below is read here)",
                   "completeness": "per sender: transactions found == nonce at the latest block",
                   "revertReason": "eth_call replay at block - 1, 4-byte error named from contracts/out ABIs; "
                                   "gasUsed == gasLimit -> OutOfGas"},
        "senders": sorted(senders, key=lambda s: s["address"]),
        "trackedHashesNotFound": missing,
        "transactions": txs,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rpc", action="append", help="public RPC (repeatable; default: the keyless list in RPCS)")
    ap.add_argument("--index", choices=sorted(INDEXES), default="routescan")
    a = ap.parse_args()
    d = build(Rpc(a.rpc or RPCS), a.index)
    write_json(OUT, d)
    by = {}
    for t in d["transactions"]:
        by[t["label"]] = by.get(t["label"], 0) + 1
    print(f"wrote {OUT.relative_to(ROOT)}: {len(d['transactions'])} transactions from {len(d['senders'])} senders; "
          f"status 0: {sum(t['status'] == 0 for t in d['transactions'])}; tracked hashes not found: "
          f"{len(d['trackedHashesNotFound'])}")
    for k, v in sorted(by.items()):
        print(f"  {k:32} {v}")


if __name__ == "__main__":
    main()
