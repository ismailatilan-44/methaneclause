"""Thin web3 wrapper shared by the Anvil demo, the operator's testnet run and the judge walk-through.

Every transaction is real. `send(..., expect_revert=...)` first simulates the call to read the custom error name,
then sends it anyway with a fixed gas limit, so the chain records the failure (receipt status 0) and the explorer
shows it. Each sent transaction is logged (label, hash, status, gas, wall-clock submit -> receipt seconds) for
pipeline/measure.py.
"""
from __future__ import annotations

import json
import time

from eth_account import Account
from web3 import Web3

from common import ROOT

STATES = ["NONE", "PROPOSED", "CHALLENGED", "CONTESTED", "CONFIRMED", "VOID", "EXPIRED", "LINKED"]
EXPLORERS = {11155111: "https://sepolia.etherscan.io", 84532: "https://sepolia.basescan.org"}
NETWORKS = {31337: "Anvil (local)", 11155111: "Ethereum Sepolia", 84532: "Base Sepolia"}
REVERT_GAS = 3_000_000  # a reverting call is sent with a fixed limit; only the gas used up to the revert is paid
PRIORITY_FEE_WEI = 10**6  # 0.001 gwei: enough for inclusion on an uncongested testnet, keeps judge costs tiny


LAG_MESSAGES = ("header not found", "unknown block", "block not found")


def lagging(err: Exception) -> bool:
    """A load-balanced RPC node that has not seen the block we pinned yet."""
    return any(m in str(err).lower() for m in LAG_MESSAGES)


def abi(name: str) -> list:
    return json.loads((ROOT / "contracts" / "out" / f"{name}.sol" / f"{name}.json").read_text())["abi"]


def _named(abi_outputs: list, values) -> dict:
    """Decode a returned struct tuple into {field: value} using the ABI component names."""
    comps = abi_outputs[0]["components"]
    return {c["name"]: v for c, v in zip(comps, values)}


class Chain:
    def __init__(self, rpc: str, sandbox: bool = False):
        self.w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 60}))
        self.chain_id = self.w3.eth.chain_id
        path = ROOT / "contracts" / "deployments" / f"{self.chain_id}.json"
        if not path.exists():
            raise SystemExit(f"no deployment for chain {self.chain_id} ({path.relative_to(ROOT)})")
        self.deployment = json.loads(path.read_text())
        self.dep = self.deployment["sandbox"] if sandbox else self.deployment
        self.cov = self.w3.eth.contract(address=self.dep["methaneCovenant"], abi=abi("MethaneCovenant"))
        self.reg = self.w3.eth.contract(address=self.dep["keyRegistry"], abi=abi("KeyRegistry"))
        self.errors = {Web3.to_hex(Web3.keccak(text=self._sig(e)))[:10]: e["name"]
                       for e in self.cov.abi + self.reg.abi if e["type"] == "error"}
        self.gas: dict[str, int] = {}
        self.txlog: list[dict] = []
        self.local = self.chain_id == 31337
        # Public RPCs are load-balanced: a read right after a write can hit a node a block behind. Reads are pinned
        # to the newest block this process has seen a receipt in, and nonces are tracked locally.
        self.seen_block = 0
        self._nonces: dict[str, int] = {}

    @staticmethod
    def _sig(e: dict) -> str:
        def t(i):
            if i["type"].startswith("tuple"):
                return "(" + ",".join(t(c) for c in i["components"]) + ")" + i["type"][5:]
            return i["type"]
        return f"{e['name']}({','.join(t(i) for i in e['inputs'])})"

    # ------------------------------------------------------------------ reads
    @property
    def network(self) -> str:
        return NETWORKS.get(self.chain_id, f"chain {self.chain_id}")

    def now(self) -> int:
        return self.w3.eth.get_block("latest")["timestamp"]

    def read(self, fn, tx: dict | None = None):
        """eth_call at the newest block we have seen (retrying while a lagging node catches up)."""
        if not self.seen_block:
            return fn.call(tx or {})
        for attempt in range(30):
            try:
                return fn.call(tx or {}, block_identifier=self.seen_block)
            except Exception as e:
                if not lagging(e) or attempt == 29:
                    raise
                time.sleep(1)

    def covenant(self, cid: bytes) -> dict:
        fn = self.cov.functions.getCovenant(cid)
        return _named(fn.abi["outputs"], self.read(fn))

    def episode(self, eid: int) -> dict:
        fn = self.cov.functions.getEpisode(eid)
        e = _named(fn.abi["outputs"], self.read(fn))
        e["stateName"] = STATES[e["state"]]
        return e

    def tx_link(self, tx_hash) -> str:
        h = tx_hash if isinstance(tx_hash, str) else "0x" + bytes(tx_hash).hex()
        if not h.startswith("0x"):
            h = "0x" + h
        base = EXPLORERS.get(self.chain_id)
        return f"{base}/tx/{h}" if base else h

    def address_link(self, addr: str) -> str:
        base = EXPLORERS.get(self.chain_id)
        return f"{base}/address/{addr}" if base else addr

    # ------------------------------------------------------------------ local-only time travel
    def set_time(self, ts: int) -> None:
        if not self.local:
            raise RuntimeError("chain time can only be moved on Anvil")
        self.w3.provider.make_request("evm_setNextBlockTimestamp", [ts])
        self.w3.provider.make_request("evm_mine", [])

    # ------------------------------------------------------------------ writes
    def send(self, fn, pk: str, value: int = 0, label: str | None = None, expect_revert: str | None = None):
        """Send a real transaction; returns (receipt, reverted error name or None)."""
        acct = Account.from_key(pk)
        tx = {"from": acct.address, "value": value, "nonce": self.nonce(acct.address), "chainId": self.chain_id,
              **self._fees()}
        if not expect_revert:
            return self._sign_send(self._build(fn, tx), acct, label, None)
        if self.revert_reason(fn, acct.address, value) is None:
            raise RuntimeError(f"expected {expect_revert} but the call succeeds in simulation; not sending it")
        tx["gas"] = REVERT_GAS
        built = fn.build_transaction(tx)
        rcpt, _ = self._sign_send(built, acct, label, None)
        # The reason is read from the mined transaction (its calldata, replayed at the block before), not from the
        # pre-send simulation, and an out-of-gas failure is named as such: a status-0 receipt alone proves nothing.
        if rcpt.status == 1:
            reason = None
        elif rcpt.gasUsed >= built["gas"]:
            reason = "OutOfGas"
        else:
            reason = self.mined_revert_reason(built, rcpt.blockNumber)
        self.txlog[-1]["revert"] = reason
        return rcpt, reason

    def _build(self, fn, tx: dict) -> dict:
        """build_transaction estimates gas at 'latest'. On a load-balanced RPC that can be a node that has not seen our
        previous transaction yet (UnknownCovenant, zero balance), so wait for it to catch up and retry briefly."""
        for attempt in range(8):
            if not self.local:
                for _ in range(20):
                    if self.w3.eth.block_number >= self.seen_block:
                        break
                    time.sleep(1)
            try:
                return fn.build_transaction(tx)
            except Exception:
                if self.local or attempt == 7:
                    raise
                time.sleep(2)

    def mined_revert_reason(self, built: dict, block: int) -> str | None:
        call = {k: built[k] for k in ("from", "to", "data", "value")}
        for attempt in range(30):
            try:
                self.w3.eth.call(call, block_identifier=block - 1)
                return None
            except Exception as e:
                if lagging(e) and attempt < 29:
                    time.sleep(1)
                    continue
                data = getattr(e, "data", None) or (e.args[1] if len(e.args) > 1 else str(e))
                return self.errors.get(str(data)[:10], str(data)[:60])
        return None

    def transfer(self, pk: str, to: str, value: int, label: str = "transfer"):
        acct = Account.from_key(pk)
        # Not a fixed 21,000: Sepolia charges extra for creating a new account (a fresh address receiving ETH).
        gas = self.w3.eth.estimate_gas({"from": acct.address, "to": to, "value": value})
        tx = {"from": acct.address, "to": to, "value": value, "gas": gas * 6 // 5 if gas > 21_000 else gas,
              "chainId": self.chain_id, "nonce": self.nonce(acct.address)}
        fees = self._fees()
        tx.update(fees)
        return self._sign_send(tx, acct, label, None)[0]

    def nonce(self, addr: str) -> int:
        n = max(self.w3.eth.get_transaction_count(addr, "pending"), self._nonces.get(addr, 0))
        self._nonces[addr] = n + 1
        return n

    def revert_reason(self, fn, sender: str, value: int = 0) -> str | None:
        try:
            self.read(fn, {"from": sender, "value": value})
        except Exception as e:  # web3 raises ContractCustomError with the 4-byte selector in data
            data = getattr(e, "data", None) or (e.args[1] if len(e.args) > 1 else str(e))
            return self.errors.get(str(data)[:10], str(data)[:60])
        return None

    def _fees(self) -> dict:
        base = self.w3.eth.get_block("latest").get("baseFeePerGas", 0) or 0
        tip = PRIORITY_FEE_WEI
        return {"maxFeePerGas": 2 * base + tip, "maxPriorityFeePerGas": tip}

    def _sign_send(self, built: dict, acct, label: str | None, reason: str | None):
        if "maxFeePerGas" not in built and "gasPrice" not in built:
            built.update(self._fees())
        signed = acct.sign_transaction(built)
        t0 = time.time()
        h = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        rcpt = self.w3.eth.wait_for_transaction_receipt(h, timeout=300, poll_latency=0.1 if self.local else 1.0)
        seconds = round(time.time() - t0, 2)
        self.seen_block = max(self.seen_block, rcpt.blockNumber)
        if label and rcpt.status == 1:
            self.gas[label] = rcpt.gasUsed
        self.txlog.append({"label": label or "", "tx": "0x" + bytes(rcpt.transactionHash).hex(),
                           "status": rcpt.status, "gasUsed": rcpt.gasUsed,
                           "effectiveGasPrice": rcpt.effectiveGasPrice, "seconds": seconds,
                           "revert": reason, "block": rcpt.blockNumber})
        return rcpt, reason
