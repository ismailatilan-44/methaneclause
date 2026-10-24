#!/usr/bin/env python3
"""Re-check the committed Ethereum Sepolia record against the chain, with no key and no account.

    make verify-evidence            # live: read-only JSON-RPC on a public Sepolia endpoint
    OFFLINE=1 make verify-evidence  # replay the recorded RPC answers (scripts/tests/fixtures/verify_evidence/rpc.json)

Chain (every answer from a public RPC: eth_chainId, eth_getCode, eth_getTransactionReceipt, eth_getTransactionByHash,
eth_call; nothing is signed or sent):
  - every address in contracts/deployments/11155111.json holds code; the v1 core (MethaneCovenant, KeyRegistry) and the
    IncentivePool with its five libraries equal this repository's compiled code (contracts/out) outside the masked
    bytes, and every masked byte is read back and checked: MethaneCovenant's immutables = the recorded registry,
    proposerBond, challengerBond, chain id and EIP-712 domain separator (and its getters agree); the pool's = the
    recorded covenant, registry, chain id and domain separator (covenant() and registry() agree); its link slots =
    the recorded library addresses; each library's call guard = its own address; each covenant's registry() is the
    recorded KeyRegistry;
  - every transaction in data/derived/sepolia_txs.json exists with the recorded status, sender, target, block and gas
    used, and every step of data/derived/testnet_run.json with its recorded status, target and gas used;
  - every recorded failed transaction fails for the recorded reason: OutOfGas used its whole gas limit; any other is
    replayed with eth_call at the block before and its 4-byte error is named from the compiled ABIs;
  - the tamper rejection quoted in README.md (testnet_run step reject_tamper; README.md must link it) has receipt
    status 0, targets the covenant and reverts with InvalidSignature (when the endpoint cannot replay old state, status 0 is checked and the
    row says the revert data was not retrievable).
Evidence (the code the observers and the contract use, imported from pipeline/):
  - the canonical bundle is rebuilt from the committed Carbon Mapper snapshot (data/derived/cases/<case>.json) and its
    RFC 8785 JCS SHA-256 equals the recorded evidenceHash;
  - the accepted proposeEpisode calldata commits to that hash, its attestations carry the bundle's rate, uncertainty,
    detection time and provider, and its Merkle proof reaches the committed covered-source root;
  - each EIP-712 signature (secp256k1 ECDSA, domain bound to chain 11155111 and the covenant) recovers to a recorded
    observer key of a distinct organisation, registered as OBSERVER in the recorded KeyRegistry at the proposal block;
  - the covenant and episode state on chain hold the committed root, the recorded policy hash and the bundle's hash;
  - when data/derived/multiyear_run.json exists: its transactions succeeded from the recorded seller / buyer, its
    covenant has POLICY_MULTIYEAR's three-year term over the committed root, and the main pool holds it under
    POLICY_V2_MULTIYEAR's policy and params hashes (so the final installment is reachable, pipeline/final_reach.py);
  - the tamper transaction reused the accepted signatures over an edited rate, so they recover to addresses with no
    organisation in KeyRegistry.keyStatus at that block: the contract's own InvalidSignature test.
Exit status 1 on any FAIL. Signatures here are secp256k1 ECDSA (the on-chain check); the post-quantum half (ML-DSA-44)
is re-verified by `make verify-bundle` on the demo run.

    python scripts/verify_evidence.py [--offline] [--fixtures PATH] [--record] [--rpc URL ...]
                                      [--txs PATH] [--run PATH] [--deployments PATH] [--readme PATH]
                                      [--multiyear PATH]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from eth_abi import decode, encode  # noqa: E402
from eth_utils import keccak, to_checksum_address  # noqa: E402

from canon import evidence_hash  # noqa: E402
from merkle import leaf, verify as merkle_verify  # noqa: E402
from observer import attest  # noqa: E402
from observer.bundle import INSTRUMENTS, build_bundle  # noqa: E402
from observer.service import load_facility  # noqa: E402
from sepolia_txs import RPCS, Rpc, _sig, load_abis  # noqa: E402

CHAIN = 11155111
DERIVED = ROOT / "data" / "derived"
OUT = ROOT / "contracts" / "out"
FIXTURE = ROOT / "scripts" / "tests" / "fixtures" / "verify_evidence" / "rpc.json"
DEFAULTS = {"txs": DERIVED / "sepolia_txs.json", "run": DERIVED / "testnet_run.json",
            "deployments": ROOT / "contracts" / "deployments" / f"{CHAIN}.json", "readme": ROOT / "README.md",
            "multiyear": DERIVED / "multiyear_run.json"}
LIBS = ("PoolVerify", "PoolSetup", "PoolEpisodes", "PoolCorroboration", "PoolInstallments")


@dataclass
class Row:
    key: str
    ok: bool
    check: str
    detail: str


class RpcError(RuntimeError):
    """The endpoint could not answer (pruned state, rate limit, no recorded answer)."""


# ------------------------------------------------------------------------------------------------------- transport
def _slim(method: str, result):
    """Keep only what the checks read, so live and recorded answers go through the same code path."""
    if method == "eth_getTransactionReceipt" and isinstance(result, dict):
        return {k: result.get(k) for k in ("transactionHash", "status", "from", "to", "contractAddress",
                                           "blockNumber", "gasUsed")}
    if method == "eth_getTransactionByHash" and isinstance(result, dict):
        return {k: result.get(k) for k in ("hash", "from", "to", "input", "value", "gas", "blockNumber")}
    return result


def _key(method: str, params: list) -> str:
    return f"{method}|{json.dumps(params, separators=(',', ':'), sort_keys=True)}"


class LiveRpc:
    """Read-only JSON-RPC over the public keyless endpoints (pipeline/sepolia_txs.py Rpc); optionally records."""

    def __init__(self, urls: list[str], record: bool = False, backoff: float = 2.0):
        # one client per endpoint, so any failure of one endpoint moves on to the next
        self.inners, self.urls, self.record, self.calls, self.backoff = [Rpc([u]) for u in urls], urls, record, {}, backoff

    def __call__(self, method: str, params: list, allow_error: bool = False):
        k = _key(method, params)
        last: Exception | None = None
        for attempt in range(3):  # public endpoints rate-limit bursts: the endpoint list is retried twice
            for inner in self.inners:
                try:
                    res = inner(method, params, allow_error=allow_error)
                    break
                # RuntimeError: no answer; Attribute/Type/ValueError: a malformed answer (e.g. a rate-limit message
                # as a bare string where a JSON-RPC error object belongs)
                except (RuntimeError, AttributeError, TypeError, ValueError) as e:
                    last = e
            else:
                if attempt < 2:
                    time.sleep(self.backoff * (attempt + 1))
                continue
            break
        else:
            msg = f"{type(last).__name__}: {last}"
            self.calls[k] = {"__rpc_error__": msg[:300]}
            raise RpcError(msg) from last
        if allow_error and isinstance(res, dict) and "code" in res:  # a reverted eth_call: keep its data
            data = res.get("data")
            data = data.get("data") if isinstance(data, dict) else data
            self.calls[k] = {"__revert__": data}
            return {"code": 3, "data": data}
        res = _slim(method, res)
        self.calls[k] = res
        return res

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"note": "recorded public JSON-RPC answers for scripts/verify_evidence.py --offline (read-only calls; "
                       "receipts and transactions trimmed to the fields the checks read)",
               "rpc": self.urls[0], "recordedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "calls": dict(sorted(self.calls.items()))}
        path.write_text(json.dumps(doc, indent=0, sort_keys=False) + "\n")


class FixtureRpc:
    """Replays recorded answers; a call that was not recorded fails closed."""

    def __init__(self, path: Path):
        doc = json.loads(path.read_text())
        self.calls, self.urls = doc["calls"], [f"recorded answers {path.name} (from {doc.get('rpc')}, "
                                                    f"{doc.get('recordedAt')})"]

    def __call__(self, method: str, params: list, allow_error: bool = False):
        k = _key(method, params)
        if k not in self.calls:
            raise RpcError(f"no recorded answer for {method} {json.dumps(params)[:90]}")
        v = self.calls[k]
        if isinstance(v, dict) and "__rpc_error__" in v:
            raise RpcError(v["__rpc_error__"])
        if isinstance(v, dict) and "__revert__" in v:
            if not allow_error:
                raise RpcError(f"{method} reverted")
            return {"code": 3, "data": v["__revert__"]}
        return v


class Memo:
    """One answer per distinct call (receipts are read by several checks)."""

    def __init__(self, rpc):
        self.rpc, self.cache = rpc, {}

    def __call__(self, method: str, params: list, allow_error: bool = False):
        k = (_key(method, params), allow_error)
        if k not in self.cache:
            try:
                self.cache[k] = ("ok", self.rpc(method, params, allow_error=allow_error))
            except RpcError as e:
                self.cache[k] = ("err", e)
        tag, v = self.cache[k]
        if tag == "err":
            raise v
        return v


# ------------------------------------------------------------------------------------------------------ ABI helpers
def artifact(name: str) -> dict:
    p = OUT / f"{name}.sol" / f"{name}.json"
    if not p.exists():
        raise SystemExit(f"{p.relative_to(ROOT)} missing: run `forge build` in contracts/ first")
    return json.loads(p.read_text())


def _t(i: dict) -> str:
    if i["type"].startswith("tuple"):
        return "(" + ",".join(_t(c) for c in i["components"]) + ")" + i["type"][5:]
    return i["type"]


def function(contract: str, name: str) -> dict:
    return next(e for e in artifact(contract)["abi"] if e["type"] == "function" and e["name"] == name)


def selector(entry: dict) -> bytes:
    return keccak(text=_sig(entry))[:4]


def error_selector(name: str) -> bytes:
    e = next(e for e in artifact("MethaneCovenant")["abi"] if e["type"] == "error" and e["name"] == name)
    return selector(e)


def eth_call(rpc, to: str, contract: str, name: str, args: list, block: str = "latest") -> tuple:
    e = function(contract, name)
    data = selector(e) + encode([_t(i) for i in e["inputs"]], args)
    res = rpc("eth_call", [{"to": to, "data": "0x" + data.hex()}, block])
    return decode([_t(o) for o in e["outputs"]], bytes.fromhex(res[2:]))


def decode_propose(inp: str) -> dict:
    e = function("MethaneCovenant", "proposeEpisode")
    raw = bytes.fromhex(inp[2:])
    if raw[:4] != selector(e):
        raise ValueError(f"selector {raw[:4].hex()} is not proposeEpisode")
    vals = decode([_t(i) for i in e["inputs"]], raw[4:])
    out = dict(zip([i["name"] for i in e["inputs"]], vals))
    out["atts"] = [dict(zip(attest.ORDER, a)) for a in out["atts"]]
    return out


def role_index(name: str) -> int:
    """Position of a KeyRegistry.Role member, read from the contract source (the ABI carries enums as uint8)."""
    src = (ROOT / "contracts" / "src" / "KeyRegistry.sol").read_text()
    members = [m.strip() for m in re.search(r"enum Role\s*\{([^}]*)\}", src).group(1).split(",") if m.strip()]
    return members.index(name)


def tx_hash(link: str) -> str:
    return link.rsplit("/", 1)[-1].lower()


def _hex_int(x) -> int:
    return int(x, 16) if isinstance(x, str) else int(x)


# ----------------------------------------------------------------------------------------------- code comparisons
def _masked(code: bytes, db: dict) -> bytes:
    c = bytearray(code)
    for refs in (db.get("immutableReferences") or {}).values():
        for r in refs:
            c[r["start"]:r["start"] + r["length"]] = bytes(r["length"])
    for libs in (db.get("linkReferences") or {}).values():
        for refs in libs.values():
            for r in refs:
                c[r["start"]:r["start"] + r["length"]] = bytes(r["length"])
    return bytes(c)


def _compiled(db: dict) -> bytes:
    return bytes.fromhex(re.sub(r"__\$[0-9a-f]{34}\$__", "0" * 40, db["object"][2:]))


def same_code(code: bytes, name: str, address: str | None = None) -> bool:
    """Deployed code equals the compiled artifact outside the masked bytes. A library's masked call guard
    (PUSH20 <address> at bytes 1..20) must hold the library's own address."""
    db = artifact(name)["deployedBytecode"]
    if name in LIBS:
        if not (len(code) > 21 and code[0] == 0x73 and address and code[1:21] == bytes.fromhex(address[2:])):
            return False
        code = code[:1] + bytes(20) + code[21:]
    return len(code) > 0 and _masked(code, db) == _masked(_compiled(db), db)


def immutable_values(code: bytes, name: str) -> list[bytes]:
    """The 32-byte value of each immutable, read from the deployed code at its reference offsets (all references of
    one immutable must hold the same bytes)."""
    out = []
    for refs in (artifact(name)["deployedBytecode"].get("immutableReferences") or {}).values():
        got = {code[r["start"]:r["start"] + r["length"]] for r in refs}
        if len(got) != 1:
            raise ValueError(f"{name}: one immutable holds {len(got)} different values")
        out.append(got.pop())
    return out


def word(x) -> bytes:
    """An address, integer or bytes32 as the 32-byte word an immutable slot holds."""
    if isinstance(x, (bytes, bytearray)):
        return bytes(x).rjust(32, b"\0")
    if isinstance(x, str):
        return bytes.fromhex(x[2:]).rjust(32, b"\0")
    return int(x).to_bytes(32, "big")


def domain_separator(contract: str) -> bytes:
    """EIP-712 domain separator for the MethaneClause domain (attest.TYPES) bound to CHAIN and `contract`."""
    fields = attest.TYPES["EIP712Domain"]
    type_hash = keccak(text="EIP712Domain(" + ",".join(f"{f['type']} {f['name']}" for f in fields) + ")")
    dom = attest.typed({}, CHAIN, to_checksum_address(contract))["domain"]
    return keccak(type_hash + keccak(text=dom["name"]) + keccak(text=dom["version"])
                  + encode(["uint256", "address"], [dom["chainId"], dom["verifyingContract"]]))


def pool_links(code: bytes) -> dict[str, set[str]]:
    links = artifact("IncentivePool")["deployedBytecode"]["linkReferences"]
    return {n: {"0x" + code[r["start"]:r["start"] + r["length"]].hex() for r in links[f"src/lib/{n}.sol"][n]}
            for n in LIBS}


# ---------------------------------------------------------------------------------------------------------- checks
def run_checks(rpc, txs: dict, run: dict, dep: dict, readme: str, multiyear: dict | None = None) -> list[Row]:
    rows: list[Row] = []

    def row(key: str, check: str, fn) -> None:
        try:
            ok, detail = fn()
        except RpcError as e:
            ok, detail = False, f"RPC: {e}"
        except Exception as e:  # noqa: BLE001 - any unexpected shape is a FAIL, never a crash or a pass
            ok, detail = False, f"{type(e).__name__}: {e}"
        rows.append(Row(key, bool(ok), check, detail))

    cov, reg = run["methaneCovenant"], run["keyRegistry"]
    code_cache: dict[str, bytes] = {}

    def code(addr: str) -> bytes:
        a = addr.lower()
        if a not in code_cache:
            code_cache[a] = bytes.fromhex(rpc("eth_getCode", [to_checksum_address(a), "latest"])[2:])
        return code_cache[a]

    # ---- chain
    def chain():
        cid = _hex_int(rpc("eth_chainId", []))
        return cid == CHAIN == run["chainId"] == dep["chainId"], f"chainId {cid}"
    row("chain", "endpoint serves Ethereum Sepolia", chain)

    contracts = {}  # address -> label
    for scope, d in (("main", dep), ("sandbox", dep.get("sandbox", {})), ("sandboxPrevious", dep.get("sandboxPrevious", {}))):
        for k in ("keyRegistry", "methaneCovenant", "incentivePool"):
            if d.get(k):
                contracts[d[k].lower()] = f"{scope}.{k}"
        for n, a in (d.get("incentivePoolLibraries") or {}).items():
            contracts.setdefault(a.lower(), f"{scope}.{n}")

    def has_code():
        empty = [f"{lbl} {a[:10]}" for a, lbl in contracts.items() if not code(a)]
        return not empty and bool(contracts), (f"{len(contracts)} addresses in {DEFAULTS['deployments'].name} hold code"
                                               if not empty else "no code at " + ", ".join(empty))
    row("code", "deployed code at every recorded address", has_code)

    scopes = [(n, d) for n, d in (("main", dep), ("sandbox", dep.get("sandbox", {})),
                                  ("sandboxPrevious", dep.get("sandboxPrevious", {}))) if d]

    def core_code():
        bad, n, unrecorded = [], 0, []
        for scope, d in scopes:
            if d.get("keyRegistry"):
                n += 1
                kc = code(d["keyRegistry"])
                if not same_code(kc, "KeyRegistry") or immutable_values(kc, "KeyRegistry"):
                    bad.append(f"{scope} KeyRegistry")
            if not d.get("methaneCovenant"):
                continue
            n += 1
            a = to_checksum_address(d["methaneCovenant"])
            cc = code(a)
            if not same_code(cc, "MethaneCovenant"):
                bad.append(f"{scope} MethaneCovenant code")
                continue
            bonds = []
            for fn in ("proposerBond", "challengerBond"):
                (v,) = eth_call(rpc, a, "MethaneCovenant", fn, [])
                if fn in d and v != d[fn]:
                    bad.append(f"{scope} {fn}() {v} != recorded {d[fn]}")
                elif fn not in d:
                    unrecorded.append(f"{scope}.{fn}")
                bonds.append(d.get(fn, v))
            want = [word(d["keyRegistry"]), word(bonds[0]), word(bonds[1]), word(CHAIN), domain_separator(a)]
            if sorted(immutable_values(cc, "MethaneCovenant")) != sorted(want):
                bad.append(f"{scope} MethaneCovenant immutables")
        note = f"; {', '.join(unrecorded)} not in the record, read from the getter" if unrecorded else ""
        return n > 0 and not bad, (
            f"{n} core contracts (main, sandbox, sandboxPrevious) = contracts/out outside the masked bytes; masked: "
            "MethaneCovenant's 5 immutables, each read back from the code = recorded registry, proposerBond, "
            f"challengerBond, chain id, EIP-712 domain separator; KeyRegistry has none{note}"
            if not bad else "; ".join(bad))
    row("core-code", "v1 core on chain is this repository's compiled code", core_code)

    def pool_code():
        bad, n = [], 0
        for scope, d in scopes:
            if not d.get("incentivePool"):
                continue
            a = to_checksum_address(d["incentivePool"])
            pc = code(a)
            n += 1
            if not same_code(pc, "IncentivePool"):
                bad.append(f"{scope} pool code")
            if len(pc) != d["incentivePoolSizes"]["IncentivePool"] or len(pc) > 24_576:
                bad.append(f"{scope} pool size {len(pc)} B")
            for fn, key in (("covenant", "methaneCovenant"), ("registry", "keyRegistry")):
                (v,) = eth_call(rpc, a, "IncentivePool", fn, [])
                if v.lower() != d[key].lower():
                    bad.append(f"{scope} pool {fn}() {v[:10]} != recorded {d[key][:10]}")
            want = [word(d["methaneCovenant"]), word(d["keyRegistry"]), word(CHAIN), domain_separator(a)]
            if sorted(immutable_values(pc, "IncentivePool")) != sorted(want):
                bad.append(f"{scope} pool immutables")
            for lib, linked in pool_links(pc).items():
                rec = d["incentivePoolLibraries"][lib].lower()
                if linked != {rec}:
                    bad.append(f"{scope} {lib} linked {sorted(linked)} != recorded {rec[:10]}")
                lc = code(rec)
                if not same_code(lc, lib, rec) or len(lc) != d["incentivePoolSizes"][lib]:
                    bad.append(f"{scope} {lib} code")
        return n > 0 and not bad, (
            f"{n} pools + {len(LIBS)} libraries = contracts/out outside the masked bytes; masked: the pool's 4 "
            "immutables, read back = recorded covenant, registry, chain id, EIP-712 domain separator (covenant() and "
            "registry() agree); its library link slots = recorded library addresses; each library's call guard = its "
            "own address; sizes as recorded" if not bad else "; ".join(bad))
    row("pool-code", "IncentivePool and its libraries are this repository's code", pool_code)

    def registry():
        bad = []
        for scope, d in (("main", dep), ("sandbox", dep.get("sandbox", {})), ("sandboxPrevious", dep.get("sandboxPrevious", {}))):
            if d.get("methaneCovenant"):
                (r,) = eth_call(rpc, to_checksum_address(d["methaneCovenant"]), "MethaneCovenant", "registry", [])
                if r.lower() != d["keyRegistry"].lower():
                    bad.append(f"{scope}: registry() {r[:10]} != recorded {d['keyRegistry'][:10]}")
        ok = not bad and cov.lower() == dep["methaneCovenant"].lower() and reg.lower() == dep["keyRegistry"].lower()
        return ok, ("each covenant's registry() is its recorded KeyRegistry; testnet_run names the main pair"
                    if ok else "; ".join(bad) or "testnet_run covenant/registry differ from the deployment record")
    row("registry", "covenant -> KeyRegistry binding", registry)

    # ---- transactions
    def receipt(h: str) -> dict:
        r = rpc("eth_getTransactionReceipt", [h])
        if not r:
            raise RpcError(f"no receipt for {h}")
        return r

    def all_txs():
        bad, n0 = [], 0
        for t in txs["transactions"]:
            try:
                r = receipt(t["tx"])
            except RpcError as e:
                bad.append(f"{t['tx'][:12]}: {e}")
                continue
            got = (_hex_int(r["status"]), r["from"].lower(), (r.get("to") or None) and r["to"].lower(),
                   _hex_int(r["blockNumber"]), _hex_int(r["gasUsed"]))
            want = (t["status"], t["from"], t["to"], t["block"], t["gasUsed"])
            if got != want:
                bad.append(f"{t['tx'][:12]} chain {got} != recorded {want}")
            n0 += t["status"] == 0
        n = len(txs["transactions"])
        return n > 0 and not bad, (f"{n} recorded transactions: status, sender, target, block, gas used as recorded "
                                   f"({n - n0} status 1, {n0} status 0)" if not bad else
                                   f"{len(bad)} of {n} recorded differ: " + "; ".join(bad[:4]))
    row("txs", "every recorded Sepolia transaction is on chain as recorded", all_txs)

    def run_steps():
        bad = []
        for s in run["steps"]:
            h = tx_hash(s["tx"])
            r = receipt(h)
            if (_hex_int(r["status"]), (r.get("to") or "").lower(), _hex_int(r["gasUsed"])) != (
                    s["status"], cov.lower(), s["gasUsed"]):
                bad.append(f"{s['step']} {h[:12]}")
        return bool(run["steps"]) and not bad, (f"{len(run['steps'])} steps of the TKM_S_276 run: status, covenant "
                                                "target, gas used as recorded" if not bad else "differ: " + ", ".join(bad))
    row("run-steps", "testnet_run.json steps are on chain as recorded", run_steps)

    errors = load_abis()[1]

    def revert_of(h: str) -> tuple[str | None, str]:
        """(error name or None, note) for a failed transaction, replayed at the block before."""
        tx = rpc("eth_getTransactionByHash", [h])
        call = {"from": tx["from"], "data": tx["input"], "value": tx["value"], "gas": tx["gas"]}
        if tx.get("to"):
            call["to"] = tx["to"]
        try:
            res = rpc("eth_call", [call, hex(_hex_int(tx["blockNumber"]) - 1)], allow_error=True)
        except RpcError as e:
            return None, f"revert data not retrievable from this endpoint ({str(e)[:60]})"
        data = res.get("data") if isinstance(res, dict) else None
        if not data:
            return None, "replay did not revert"
        return errors.get(str(data)[:10], f"unnamed:{str(data)[:10]}"), ""

    def failed_txs():
        bad, notes, n = [], 0, 0
        for t in txs["transactions"]:
            if t["status"] != 0:
                continue
            n += 1
            if t["revert"] == "OutOfGas":
                tx = rpc("eth_getTransactionByHash", [t["tx"]])
                if _hex_int(tx["gas"]) != t["gasUsed"]:
                    bad.append(f"{t['tx'][:12]} OutOfGas but gas used {t['gasUsed']} < limit {_hex_int(tx['gas'])}")
                continue
            name, note = revert_of(t["tx"])
            if name is None and "not retrievable" in note:
                notes += 1
            elif name != t["revert"]:
                bad.append(f"{t['tx'][:12]} reverts {name or note}, recorded {t['revert']}")
        names = sorted({t["revert"] for t in txs["transactions"] if t["status"] == 0})
        return not bad, (f"{n} failed transactions fail as recorded ({', '.join(names)})"
                         + (f"; {notes} with revert data not retrievable here, status 0 checked" if notes else "")
                         if not bad else "; ".join(bad))
    row("failed-txs", "every recorded failure fails for the recorded reason", failed_txs)

    tamper_step = next(s for s in run["steps"] if s["step"] == "reject_tamper")
    tamper = tx_hash(tamper_step["tx"])

    def tamper_row():
        r = receipt(tamper)
        st, to = _hex_int(r["status"]), (r.get("to") or "").lower()
        if st != 0 or to != cov.lower():
            return False, f"{tamper[:12]}: status {st}, to {to[:10]} (expected status 0 at the covenant)"
        name, note = revert_of(tamper)
        linked = tamper in readme.lower()
        where = "README.md links it" if linked else "README.md does not link it (the row claims the README's tx)"
        if name is None and "not retrievable" in note:
            return linked, f"{tamper[:12]}…: receipt status 0 at the covenant; {note}; {where}"
        ok = linked and name == tamper_step["reason"] == "InvalidSignature"
        return ok, f"{tamper[:12]}…: receipt status 0 at the covenant, reverts {name or note}; {where}"
    row("tamper", "the README's status-0 tamper transaction", tamper_row)

    # ---- evidence
    facility, _, case_file = load_facility(run["case"])
    snap = next(p for p in case_file["cm_plumes_within_radius"] if p["plume_id"] == run["plume"])
    bundle = build_bundle(dict(snap), covenant_id=run["covenantId"], source_id=facility["sourceId"],
                          source_name=facility["source_name"],
                          content_sha256=case_file["cm_sources"][snap["source_name"]]["content_sha256"])
    ev = evidence_hash(bundle)

    def bundle_hash():
        return ev == run["evidenceHash"].lower(), (f"rebuilt from cases/{run['case']}.json plume {run['plume'][:24]}…: "
                                                   f"JCS SHA-256 {ev[:18]}… {'=' if ev == run['evidenceHash'].lower() else '!='} recorded")
    row("bundle-hash", "committed bundle hashes (RFC 8785) to the recorded evidenceHash", bundle_hash)

    proposal = tx_hash(next(s["tx"] for s in run["steps"] if s["step"] == "proposeEpisode"))
    prop_block: list[int] = []

    def proposal_call() -> dict:
        tx = rpc("eth_getTransactionByHash", [proposal])
        if not prop_block:
            prop_block.append(_hex_int(tx["blockNumber"]))
        if (tx.get("to") or "").lower() != cov.lower():
            raise ValueError(f"proposal {proposal[:12]} is not sent to the covenant")
        return decode_propose(tx["input"])

    def calldata():
        p = proposal_call()
        bad = []
        if "0x" + p["evidenceHash"].hex() != ev:
            bad.append("evidenceHash")
        if "0x" + p["covenantId"].hex() != run["covenantId"].lower() or "0x" + p["sourceId"].hex() != facility["sourceId"].lower():
            bad.append("covenant/source")
        want = {"evidenceHash": bytes.fromhex(ev[2:]), "emissionKgH": bundle["emissionKgH"],
                "uncertaintyKgH": bundle["uncertaintyKgH"], "detectedAt": bundle["detectedAt"],
                "instrumentId": INSTRUMENTS[bundle["instrument"]], "providerId": attest.b32(bundle["providerId"]),
                "covenantId": p["covenantId"], "sourceId": p["sourceId"], "kind": attest.KIND_DETECT}
        for i, a in enumerate(p["atts"]):
            bad += [f"att{i}.{k}" for k, v in want.items() if a[k] != v]
        root = bytes.fromhex(json.loads((DERIVED / "sourceset_v2.json").read_text())["root"][2:])
        if not merkle_verify(list(p["merkleProof"]), root, leaf(p["sourceId"])):
            bad.append("Merkle proof")
        return not bad and len(p["atts"]) >= 2, (
            f"{proposal[:12]}…: {len(p['atts'])} attestations commit to the bundle hash, rate "
            f"{bundle['emissionKgH']} kg/h, detection time, provider; Merkle proof -> committed source root"
            if not bad else "mismatch: " + ", ".join(bad))
    row("calldata", "accepted proposal commits to the committed bundle", calldata)

    signers: list[str] = []

    def eip712():
        p = proposal_call()
        rec = [attest.recover(a, s, CHAIN, to_checksum_address(cov)) for a, s in zip(p["atts"], p["sigs"])]
        signers[:] = rec
        want = [dep["observerA"], dep["observerB"]]
        ok = rec == want and len(set(rec)) == len(rec)
        return ok, (f"secp256k1 ECDSA, domain chain {CHAIN} + covenant: recover to observerA {rec[0][:10]}…, observerB "
                    f"{rec[1][:10]}… of the deployment record" if ok else f"recovered {rec}, recorded {want}")
    row("eip712", "EIP-712 signatures recover to the recorded observer keys", eip712)

    def registered():
        if not signers:
            return False, "no recovered signers"
        block = hex(prop_block[0])
        when = next(t["timestamp"] for t in txs["transactions"] if t["tx"] == proposal)
        observer, orgs, bad = role_index("OBSERVER"), set(), []
        for s in signers:
            (k,) = eth_call(rpc, to_checksum_address(reg), "KeyRegistry", "getKey", [s], block)
            org, role, _alg, vfrom, vuntil = k[0], k[1], k[2], k[3], k[4]
            if role != observer or org == bytes(32) or not (vfrom <= when and (vuntil == 0 or when < vuntil)):
                bad.append(f"{s[:10]} role {role}, validFrom {vfrom}, validUntil {vuntil}")
            orgs.add(org)
        ok = not bad and len(orgs) == len(signers)
        return ok, (f"KeyRegistry {reg[:10]}… at block {prop_block[0]}: {len(signers)} keys role OBSERVER, "
                    f"{len(orgs)} distinct organisations, valid at the proposal" if ok else
                    "; ".join(bad) or "signers share an organisation")
    row("registered", "signers registered in the recorded KeyRegistry", registered)

    def state():
        (c,) = eth_call(rpc, to_checksum_address(cov), "MethaneCovenant", "getCovenant", [attest.b32(run["covenantId"])])
        names = [x["name"] for x in function("MethaneCovenant", "getCovenant")["outputs"][0]["components"]]
        c = dict(zip(names, c))
        (e,) = eth_call(rpc, to_checksum_address(cov), "MethaneCovenant", "getEpisode", [run["episodeId"]])
        enames = [x["name"] for x in function("MethaneCovenant", "getEpisode")["outputs"][0]["components"]]
        e = dict(zip(enames, e))
        root = json.loads((DERIVED / "sourceset_v2.json").read_text())["root"].lower()
        checks = {"sourceSetRoot": "0x" + c["sourceSetRoot"].hex() == root,
                  "policyHash": "0x" + c["policyHash"].hex() == run["policyHash"].lower(),
                  "seller/buyer": (c["seller"].lower(), c["buyer"].lower()) == (dep["seller"].lower(), dep["buyer"].lower()),
                  "episode evidenceHash": "0x" + e["evidenceHash"].hex() == ev,
                  "episode rate": e["emissionKgH"] == bundle["emissionKgH"],
                  "episode covenant": "0x" + e["covenantId"].hex() == run["covenantId"].lower()}
        bad = [k for k, v in checks.items() if not v]
        return not bad, (f"covenant {run['covenantId'][-4:]}: committed source root, recorded policy hash, recorded "
                         f"seller/buyer; episode {run['episodeId']}: bundle hash and rate" if not bad else
                         "differs: " + ", ".join(bad))
    row("state", "covenant and episode state on chain match the record", state)

    def tamper_calldata():
        t = rpc("eth_getTransactionByHash", [tamper])
        tp, p = decode_propose(t["input"]), proposal_call()
        same_sigs = list(tp["sigs"]) == list(p["sigs"])
        edited = sorted({k for a, b in zip(tp["atts"], p["atts"]) for k in attest.ORDER if a[k] != b[k]})
        tb = dict(bundle, emissionKgH=tp["atts"][0]["emissionKgH"])
        rehashed = "0x" + tp["evidenceHash"].hex() == evidence_hash(tb)
        rec = [attest.recover(a, s, CHAIN, to_checksum_address(cov)) for a, s in zip(tp["atts"], tp["sigs"])]
        block = hex(_hex_int(t["blockNumber"]))
        when = next(x["timestamp"] for x in txs["transactions"] if x["tx"] == tamper)
        # the contract's own test (MethaneCovenant._verifyOne): keyStatus(signer, block.timestamp).orgId == 0
        # -> InvalidSignature; read here at the tamper's block and time
        orgs = [eth_call(rpc, to_checksum_address(reg), "KeyRegistry", "keyStatus", [r, when], block)[0] for r in rec]
        ok = (same_sigs and edited == ["emissionKgH", "evidenceHash"] and rehashed
              and all(o == bytes(32) for o in orgs) and not set(rec) & {dep["observerA"], dep["observerB"]})
        return ok, (f"signatures of the accepted proposal reused over rate {bundle['emissionKgH']} -> "
                    f"{tp['atts'][0]['emissionKgH']} kg/h (bundle re-hashed); they recover to {len(rec)} addresses "
                    "with no organisation in KeyRegistry.keyStatus at that block (the contract's InvalidSignature test)"
                    if ok else f"same sigs {same_sigs}, edited {edited}, re-hashed {rehashed}, "
                    f"orgIds {['0x' + o.hex()[:8] for o in orgs]}")
    row("tamper-calldata", "why the tamper failed: edited rate, kept signatures", tamper_calldata)

    if multiyear is not None:
        def multiyear_term():
            import policy as policy_mod
            from final_reach import final_reach

            pol, core, my = policy_mod.POLICY_V2_MULTIYEAR, policy_mod.POLICY_MULTIYEAR, multiyear
            bad = []
            for s in my["steps"]:
                r = rpc("eth_getTransactionReceipt", [tx_hash(s["tx"])])
                want = {"createCovenant": (dep["seller"], cov), "fund": (dep["buyer"], dep["incentivePool"])}[s["step"]]
                if (_hex_int(r["status"]), r["from"].lower(), r["to"].lower()) != (1, *(x.lower() for x in want)):
                    bad.append(f"{s['step']} {s['tx'][-10:]}")
            (c,) = eth_call(rpc, to_checksum_address(cov), "MethaneCovenant", "getCovenant", [attest.b32(my["covenantId"])])
            names = [x["name"] for x in function("MethaneCovenant", "getCovenant")["outputs"][0]["components"]]
            c = dict(zip(names, c))
            pl, _ = eth_call(rpc, to_checksum_address(dep["incentivePool"]), "IncentivePool", "getPool",
                             [attest.b32(my["covenantId"])])
            pnames = [x["name"] for x in function("IncentivePool", "getPool")["outputs"][0]["components"]]
            pl = dict(zip(pnames, pl))
            root = json.loads((DERIVED / "sourceset_v2.json").read_text())["root"].lower()
            reach = final_reach(pol)
            checks = {"term": c["end"] - c["start"] == core["term"], "start": c["start"] == core["start"],
                      "root": "0x" + c["sourceSetRoot"].hex() == root,
                      "policyHash": "0x" + c["policyHash"].hex() == my["policyHash"].lower(),
                      "seller/buyer": (c["seller"].lower(), c["buyer"].lower()) == (dep["seller"].lower(), dep["buyer"].lower()),
                      "pool policyHash": "0x" + pl["policyHash"].hex() == policy_mod.policy_hash(pol),
                      "pool paramsHash": "0x" + pl["paramsHash"].hex() == pol["onchainParamsHash"],
                      "pool mode ONCHAIN": pl["mode"] == 0, "finalReach": my["finalReach"] == reach}
            bad += [k for k, v in checks.items() if not v]
            return not bad, (f"covenant {int(my['covenantId'], 16)}: {core['term'] // 86400}-day term on chain, recorded "
                             f"policy hash; main pool opened for it with POLICY_V2_MULTIYEAR (paramsHash "
                             f"{pol['onchainParamsHash'][:12]}…): final installment reachable for episodes up to day "
                             f"{reach['lastDetectionDay']}" if not bad else "differs: " + ", ".join(bad))
        row("multiyear", "three-year covenant and its pool on chain match the record", multiyear_term)
    return rows


# ------------------------------------------------------------------------------------------------------------- CLI
def print_table(rows: list[Row], mode: str) -> None:
    print(f"verify-evidence: {mode}")
    w = max(len(r.check) for r in rows)
    for r in rows:
        print(f"  {'PASS' if r.ok else 'FAIL'}  {r.check:<{w}}  {r.detail}")
    n_fail = sum(not r.ok for r in rows)
    print(f"verify-evidence: {len(rows) - n_fail} PASS, {n_fail} FAIL")


def main(argv: list[str] | None = None, sink: list | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--offline", action="store_true", help="replay recorded RPC answers instead of a live endpoint")
    ap.add_argument("--fixtures", type=Path, default=FIXTURE)
    ap.add_argument("--record", action="store_true", help="live run that rewrites the recorded answers")
    ap.add_argument("--rpc", action="append", help="public Sepolia RPC (repeatable; default: the keyless list)")
    for k, v in DEFAULTS.items():
        ap.add_argument(f"--{k}", type=Path, default=v)
    a = ap.parse_args(argv)
    if a.offline and a.record:
        ap.error("--record needs a live endpoint")
    txs, run, dep = (json.loads(p.read_text()) for p in (a.txs, a.run, a.deployments))
    readme = a.readme.read_text(encoding="utf-8") if a.readme.exists() else ""
    base = FixtureRpc(a.fixtures) if a.offline else LiveRpc(a.rpc or RPCS, record=a.record)
    multiyear = json.loads(a.multiyear.read_text()) if a.multiyear.exists() else None
    rows = run_checks(Memo(base), txs, run, dep, readme, multiyear)
    mode = (f"offline replay of {base.urls[0]}" if a.offline else
            f"live, read-only JSON-RPC at {base.urls[0]} (no key, nothing sent)")
    print_table(rows, mode)
    if a.record:
        base.save(a.fixtures)
        print(f"recorded {len(base.calls)} answers -> {a.fixtures.relative_to(ROOT) if a.fixtures.is_relative_to(ROOT) else a.fixtures}")
    if sink is not None:
        sink.extend(rows)
    return 0 if all(r.ok for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
