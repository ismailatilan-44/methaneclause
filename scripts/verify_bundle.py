#!/usr/bin/env python3
"""Verify both halves of each observer's hybrid signature on a demo run, and print the size/time table.

    pipeline/.venv/bin/python scripts/verify_bundle.py out/demo/run.json [--rpc http://127.0.0.1:8545]

For every attestation in `run.json["hybrid"]`:
  1. the canonical bundle (RFC 8785) hashes to the evidenceHash the attestation commits to;
  2. secp256k1 ECDSA (EIP-712, domain bound to chainId + contract) recovers to the observer's key — the check the
     contract runs;
  3. ML-DSA-44 (FIPS 204, context "MethaneClause/v1") verifies over the same canonical bytes, and SHA-256 of the
     public key equals the pqPubKeyHash that KeyRegistry holds for the signer (algId 2).
The bundle's own `pkHash` is informational only: a bundle signed with a foreign ML-DSA key that carries that key's hash
fails. No anchor for the signer, or an algId other than 2, also fails (fail closed).

What each mode proves:
  --rpc URL  against the chain, now: the registry is the one the covenant itself trusts (`MethaneCovenant.registry()`
             at the run's covenant address); a run whose `deployment.keyRegistry` differs is refused; the anchor is
             `KeyRegistry.getKey(signer)` read live.
  offline    consistency only: the anchor is the registry read the run recorded (`run.json["registeredKeys"]`, read on
             chain at `readAtBlock`), accepted only from the run's own registry; and when
             `contracts/deployments/<chainId>.json` exists (Sepolia is committed; the Anvil record is written by the
             local deploy), the run's covenant and registry addresses must equal that record's. It does not show that
             the recorded read is what the chain holds; `--rpc` does.
Exit status 1 if any check fails. EVM identity and the on-chain check stay ECDSA: this is not a quantum-safe chain.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from eth_account import Account  # noqa: E402

from canon import canonical_bytes, evidence_hash  # noqa: E402
from observer import attest, pq  # noqa: E402

GREEN, RED, END = "\033[32m", "\033[31m", "\033[0m"
BYTE_FIELDS = {"covenantId", "sourceId", "evidenceHash", "modelHash", "providerId"}


def line(ok: bool, msg: str) -> bool:
    print(f"   {GREEN + 'ok  ' if ok else RED + 'FAIL'}{END}  {msg}")
    return ok


def timed(fn, n: int = 20) -> float:
    xs = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t) * 1000)
    return statistics.median(xs)


GET_KEY_ABI = [{"type": "function", "name": "getKey", "stateMutability": "view",
                "inputs": [{"name": "addr", "type": "address"}],
                "outputs": [{"name": "", "type": "tuple", "components": [
                    {"name": "orgId", "type": "bytes32"}, {"name": "role", "type": "uint8"},
                    {"name": "algId", "type": "uint8"}, {"name": "validFrom", "type": "uint64"},
                    {"name": "validUntil", "type": "uint64"}, {"name": "pqPubKeyHash", "type": "bytes32"},
                    {"name": "replacedBy", "type": "address"}]}]}]


def anchors_from_record(run: dict) -> dict[str, dict]:
    """signer (lower-case) -> {algId, pqPubKeyHash, source}, from the registry read the run recorded on chain."""
    registry = run["deployment"]["keyRegistry"].lower()
    out = {}
    for k in (run.get("registeredKeys") or {}).values():
        if k.get("registry", "").lower() != registry:
            continue  # a record from another registry anchors nothing here
        out[k["address"].lower()] = {"algId": k["algId"], "pqPubKeyHash": k["pqPubKeyHash"].lower(),
                                     "source": f"KeyRegistry {k['registry'][:10]}..., read at block {k.get('readAtBlock')}"}
    return out


REGISTRY_ABI = [{"type": "function", "name": "registry", "stateMutability": "view", "inputs": [],
                 "outputs": [{"name": "", "type": "address"}]}]


def deployment_record_check(run: dict, deployments: Path = ROOT / "contracts" / "deployments") -> list[tuple[bool, str]]:
    """Offline: the run's covenant and registry equal `contracts/deployments/<chainId>.json`, when that record exists."""
    path = deployments / f"{run['chainId']}.json"
    if not path.exists():
        return [(True, f"no deployment record for chain {run['chainId']} ({path.name}); offline check rests on the "
                       "run's recorded registry read only")]
    rec = json.loads(path.read_text())
    out = []
    for key in ("methaneCovenant", "keyRegistry"):
        same = str(rec.get(key, "")).lower() == str(run["deployment"].get(key, "")).lower()
        out.append((same, f"run {key} {'=' if same else '!='} deployment record {path.name} ({str(rec.get(key))[:10]}...)"))
    return out


def anchors_from_rpc(run: dict, rpc: str, signers: list[str], w3=None) -> dict[str, dict]:
    """signer (lower-case) -> {algId, pqPubKeyHash, source}, from KeyRegistry.getKey(signer) over RPC, now. The
    registry is the one the covenant trusts (`MethaneCovenant.registry()`); a run naming another is refused."""
    from web3 import Web3
    w3 = w3 or Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))
    if w3.eth.chain_id != run["chainId"]:
        raise SystemExit(f"--rpc serves chain {w3.eth.chain_id}; the run is chain {run['chainId']}")
    cov = w3.eth.contract(address=Web3.to_checksum_address(run["deployment"]["methaneCovenant"]), abi=REGISTRY_ABI)
    registry = cov.functions.registry().call()
    if registry.lower() != run["deployment"]["keyRegistry"].lower():
        raise SystemExit(f"the covenant's registry() is {registry}; the run names {run['deployment']['keyRegistry']}"
                         " — refused")
    reg = w3.eth.contract(address=Web3.to_checksum_address(registry), abi=GET_KEY_ABI)
    out = {}
    for s in signers:
        k = reg.functions.getKey(Web3.to_checksum_address(s)).call()
        out[s.lower()] = {"algId": k[2], "pqPubKeyHash": "0x" + bytes(k[5]).hex(),
                          "source": f"KeyRegistry {registry[:10]}... (= covenant.registry()), getKey over RPC"}
    return out


def check_mldsa(entry: dict, anchor: dict | None) -> tuple[bool, str]:
    """ML-DSA-44 verifies over the canonical bundle AND SHA-256(pk) equals the registry's pqPubKeyHash for the signer.
    The bundle's own `pkHash` is never trusted."""
    m = entry["mldsa44"]
    pk, sig = bytes.fromhex(m["publicKey"][2:]), bytes.fromhex(m["sig"][2:])
    sig_ok = pq.verify(pk, canonical_bytes(entry["bundle"]), sig)
    if anchor is None:
        return False, "ML-DSA-44: no registry anchor for this signer (fail closed)"
    if anchor["algId"] != pq.ALG_HYBRID:
        return False, f"ML-DSA-44: the registry holds this signer as algId {anchor['algId']}, not 2 (hybrid)"
    bound = pq.pk_hash(pk) == anchor["pqPubKeyHash"].lower()
    note = "" if m.get("pkHash", "").lower() == pq.pk_hash(pk) else "; the bundle's own pkHash differs (informational)"
    return sig_ok and bound, (f"ML-DSA-44 / FIPS 204 (ctx \"{m['context']}\"): signature "
                              f"{'verifies' if sig_ok else 'does not verify'}; SHA-256(pk) "
                              f"{'=' if bound else '!='} registry pqPubKeyHash {anchor['pqPubKeyHash'][:18]}... "
                              f"[{anchor['source']}]{note}")


def main() -> None:
    args = sys.argv[1:]
    rpc = None
    if "--rpc" in args:
        i = args.index("--rpc")
        rpc = args[i + 1]
        del args[i:i + 2]
    path = Path(args[0] if args else ROOT / "out" / "demo" / "run.json")
    run = json.loads(path.read_text())
    chain_id, contract = run["chainId"], run["deployment"]["methaneCovenant"]
    entries = run.get("hybrid") or sys.exit(f"{path}: no hybrid signatures (run `make demo` first)")
    all_ok = True
    if rpc:
        anchors = anchors_from_rpc(run, rpc, [e["signer"] for e in entries])
        print(f"mode --rpc: covenant.registry() equals the run's registry; anchors read live from chain {chain_id}")
    else:
        anchors = anchors_from_record(run)
        print("mode offline: anchors from the run's recorded registry read (consistency, not a live read)")
        for ok, what in deployment_record_check(run):
            all_ok &= line(ok, what)
    for e in entries:
        att = {k: (bytes.fromhex(v[2:]) if k in BYTE_FIELDS else v) for k, v in e["attestation"].items()}
        msg = canonical_bytes(e["bundle"])
        print(f"{e['org']}  {e['signer']}")
        all_ok &= line(evidence_hash(e["bundle"]) == e["evidenceHash"] == "0x" + att["evidenceHash"].hex(),
                       f"bundle ({len(msg)} B JCS) hashes to the attested evidenceHash {e['evidenceHash'][:18]}…")
        rec = attest.recover(att, bytes.fromhex(e["signature"][2:]), chain_id, contract)
        all_ok &= line(rec == e["signer"], "ECDSA secp256k1 / EIP-712: recovers to the observer key (checked on chain)")
        ok, what = check_mldsa(e, anchors.get(e["signer"].lower()))
        all_ok &= line(ok, what)

    # size / time table, measured here (Python reference implementations: eth-account, dilithium-py)
    e = entries[0]
    msg = canonical_bytes(e["bundle"])
    acct = Account.create()
    pkq, skq = pq.keypair(b"\x07" * 32)
    sigq = pq.sign(skq, msg)
    att = {k: (bytes.fromhex(v[2:]) if k in BYTE_FIELDS else v) for k, v in e["attestation"].items()}
    sig_e = attest.sign(att, acct.key, chain_id, contract)
    t = {"ecdsa_sign": timed(lambda: attest.sign(att, acct.key, chain_id, contract)),
         "ecdsa_verify": timed(lambda: attest.recover(att, sig_e, chain_id, contract)),
         "mldsa_sign": timed(lambda: pq.sign(skq, msg)),
         "mldsa_verify": timed(lambda: pq.verify(pkq, msg, sigq))}
    print("\n| Scheme | public key | signature | sign (ms) | verify (ms) | where it is checked |")
    print("|---|---|---|---|---|---|")
    ecdsa_pk = len(acct._key_obj.public_key.to_bytes())
    print(f"| secp256k1 ECDSA (EIP-712) | {ecdsa_pk} B | {len(sig_e)} B | {t['ecdsa_sign']:.2f} | {t['ecdsa_verify']:.2f} | "
          "on chain (`ecrecover`) |")
    print(f"| ML-DSA-44 (FIPS 204) | {len(pkq):,} B | {len(sigq):,} B | {t['mldsa_sign']:.2f} | {t['mldsa_verify']:.2f} | "
          "off chain; SHA-256(pk) anchored in KeyRegistry |")
    print("\nTimes: median of 20 runs, pure-Python reference code on this machine (dilithium-py is not optimised).")
    print("EVM transaction identity and the on-chain check remain secp256k1 ECDSA; not a quantum-safe chain.")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
