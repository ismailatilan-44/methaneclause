#!/usr/bin/env python3
"""Verify both halves of each observer's hybrid signature on a demo run, and print the size/time table.

    pipeline/.venv/bin/python scripts/verify_bundle.py out/demo/run.json [--rpc http://127.0.0.1:8545 [--record PATH]]
    pipeline/.venv/bin/python scripts/verify_bundle.py RUN --replay PATH   # the recorded answers of an --rpc run

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
             `KeyRegistry.getKey(signer)` read live. And what MethaneCovenant checked before it accepted the bundle:
             the covenant's `EpisodeProposed` (or `DetectionLinked`) log carrying this evidenceHash is found, and at
             that block's time every recovered signer holds an OBSERVER key (AttestorNotIndependent), of an
             organisation that is neither the covenant's seller's nor its buyer's (SelfAttestation), valid with
             validFrom <= time < validUntil (RevokedKey), and the signers span at least the covenant's k distinct
             organisations (QuorumNotMet); `getCovenant` supplies k and the two party organisations. A bundle the
             covenant never accepted fails. The log is looked up in the block of the run's `proposal.tx`; a run
             without one is scanned from block 0 in 40,000-block eth_getLogs spans. `--record PATH` saves the read-only answers; `--replay PATH` re-runs
             the same checks from them offline (scripts/tests/test_verify_bundle.py). Before any read, the run's
             covenant and registry must equal `contracts/deployments/<chainId>.json` (`--deployments DIR` names
             another directory): no record, or another address, is refused, because the genuine bytecode deployed by
             anyone over a registry whose observer orgs they control would pass every check above. Sepolia's record
             is committed; Anvil's (31337) is written by the local deploy (Deploy.s.sol) and is not committed.
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
sys.path.insert(0, str(ROOT / "scripts"))

from eth_abi import decode, encode  # noqa: E402
from eth_account import Account  # noqa: E402
from eth_utils import keccak, to_checksum_address  # noqa: E402

from canon import canonical_bytes, evidence_hash  # noqa: E402
from observer import attest, pq  # noqa: E402

import verify_evidence as ve  # noqa: E402  (the read-only RPC transport, ABI helpers and the contract's key rules)

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


DEPLOYMENTS = ROOT / "contracts" / "deployments"


def deployment_record_check(run: dict, deployments: Path = DEPLOYMENTS,
                            require: bool = False) -> list[tuple[bool, str]]:
    """The run's covenant and registry equal `contracts/deployments/<chainId>.json`. Offline, a missing record is said
    and passes (consistency only); with `require` (the --rpc path) a missing record fails: a covenant the record does
    not name could be the genuine bytecode deployed by anyone over a registry whose observer orgs they control."""
    path = deployments / f"{run['chainId']}.json"
    if not path.exists():
        if require:
            return [(False, f"no deployment record for chain {run['chainId']} ({path}): --rpc refuses a covenant it "
                            "cannot tie to a recorded deployment")]
        return [(True, f"no deployment record for chain {run['chainId']} ({path.name}); offline check rests on the "
                       "run's recorded registry read only")]
    rec = json.loads(path.read_text())
    out = []
    for key in ("methaneCovenant", "keyRegistry"):
        same = str(rec.get(key, "")).lower() == str(run["deployment"].get(key, "")).lower()
        out.append((same, f"run {key} {run['deployment'].get(key)} {'=' if same else '!='} deployment record "
                          f"{path.name} {rec.get(key)}"))
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


class CallW3:
    """The part of web3 that `anchors_from_rpc` uses (`eth.chain_id`, `eth.contract(a, abi).functions.f(*x).call()`)
    over a raw read-only JSON-RPC callable, so live, recorded and replayed reads go through one path."""

    def __init__(self, rpc):
        class _Eth:
            @property
            def chain_id(self) -> int:
                return ve._hex_int(rpc("eth_chainId", []))

            def contract(self, address: str, abi: list):
                return type("Contract", (), {"functions": _Functions(rpc, address, abi)})()

        self.eth = _Eth()


class _Functions:
    def __init__(self, rpc, address: str, abi: list):
        self._rpc, self._address, self._abi = rpc, address, {e["name"]: e for e in abi if e["type"] == "function"}

    def __getattr__(self, name: str):
        entry = self._abi[name]

        def bind(*args):
            def call():
                data = ve.selector(entry) + encode([ve._t(i) for i in entry["inputs"]], list(args))
                res = self._rpc("eth_call", [{"to": self._address, "data": "0x" + data.hex()}, "latest"])
                out = decode([ve._t(o) for o in entry["outputs"]], bytes.fromhex(res[2:]))
                return out[0] if len(out) == 1 else list(out)
            return type("Call", (), {"call": staticmethod(call)})()
        return bind


def covenant_of(run: dict, rpc) -> dict:
    """`MethaneCovenant.getCovenant` for the covenant the run's attestations name (one covenant per run)."""
    cids = {e["attestation"]["covenantId"].lower() for e in run["hybrid"]}
    if len(cids) != 1:
        raise ValueError(f"the attestations name {len(cids)} covenants")
    cov = to_checksum_address(run["deployment"]["methaneCovenant"])
    (c,) = ve.struct_call(rpc, cov, "MethaneCovenant", "getCovenant", [attest.b32(cids.pop())])
    return c


def find_acceptance(run: dict, rpc, covenant_id: bytes, ev: str) -> tuple[str, int, int] | None:
    """(event, episodeId, block) of the covenant's EpisodeProposed or DetectionLinked log that carries this covenant
    and evidenceHash, or None. The run's proposal transaction, when it names one, only narrows the block range; the
    log itself must carry the hash. Without it, blocks 0..latest are read in chunks of verify_evidence.LOG_CHUNK
    (public endpoints cap the eth_getLogs range)."""
    cov = to_checksum_address(run["deployment"]["methaneCovenant"])
    events = [e for e in ve.artifact("MethaneCovenant")["abi"]
              if e["type"] == "event" and e["name"] in ("EpisodeProposed", "DetectionLinked")]
    topics = ["0x" + keccak(text=ve._sig(e)).hex() for e in events]
    tx = (run.get("proposal") or {}).get("tx")
    r = rpc("eth_getTransactionReceipt", [tx if tx.startswith("0x") else "0x" + tx]) if tx else None
    if r:
        spans = [(r["blockNumber"], r["blockNumber"])]
    else:
        latest = ve._hex_int(rpc("eth_blockNumber", []))
        spans = [(hex(lo), hex(min(lo + ve.LOG_CHUNK - 1, latest))) for lo in range(0, latest + 1, ve.LOG_CHUNK)]
    logs = [lg for lo, hi in spans
            for lg in rpc("eth_getLogs", [{"address": cov, "fromBlock": lo, "toBlock": hi, "topics": [topics]}])]
    for lg in logs:
        got = ve.decode_log("MethaneCovenant", lg)
        if lg["address"].lower() != cov.lower() or got is None:
            continue
        name, args = got
        if args["covenantId"] == covenant_id and "0x" + args["evidenceHash"].hex() == ev.lower():
            return name, args["episodeId"], ve._hex_int(lg["blockNumber"])
    return None


def contract_checks(run: dict, rpc, entries: list[dict]) -> list[tuple[bool, str]]:
    """What MethaneCovenant enforced when it accepted this bundle, re-checked over read-only RPC: the acceptance log,
    then per recovered signer the key rules at that block's time and the k-of-n quorum (verify_evidence.key_rules)."""
    chain_id, cov = run["chainId"], to_checksum_address(run["deployment"]["methaneCovenant"])
    evs = {e["evidenceHash"].lower() for e in entries}
    if len(evs) != 1:
        return [(False, f"the bundle's attestations commit to {len(evs)} evidence hashes")]
    ev = evs.pop()
    c = covenant_of(run, rpc)
    if int(c["seller"], 16) == 0:
        return [(False, f"getCovenant: no covenant {entries[0]['attestation']['covenantId']} at {cov}")]
    cid = attest.b32(entries[0]["attestation"]["covenantId"])
    out = [(True, f"getCovenant({int.from_bytes(cid, 'big')}): k = {c['k']}, seller org {ve.org_label(c['sellerOrg'])}, "
                  f"buyer org {ve.org_label(c['buyerOrg'])}")]
    found = find_acceptance(run, rpc, cid, ev)
    if found is None:
        return out + [(False, f"no EpisodeProposed or DetectionLinked log for evidenceHash {ev[:18]}… at the covenant: "
                              "the contract never accepted this bundle")]
    name, eid, block = found
    when = ve._hex_int(rpc("eth_getBlockByNumber", [hex(block), False])["timestamp"])
    out.append((True, f"{name}(episode {eid}) carries evidenceHash {ev[:18]}…, block {block}, block time {when}"))
    (reg,) = ve.eth_call(rpc, cov, "MethaneCovenant", "registry", [])
    keys = {}
    for e in entries:
        att = {k: (bytes.fromhex(v[2:]) if k in BYTE_FIELDS else v) for k, v in e["attestation"].items()}
        s = attest.recover(att, bytes.fromhex(e["signature"][2:]), chain_id, cov)
        keys[s] = ve.eth_call(rpc, to_checksum_address(reg), "KeyRegistry", "getKey", [s])[0]
    return out + ve.key_rules(keys, when, c["sellerOrg"], c["buyerOrg"], c["k"])


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


def _opt(args: list[str], flag: str) -> str | None:
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        raise SystemExit(f"{flag} needs a value")
    value = args[i + 1]
    del args[i:i + 2]
    return value


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    rpc_url, record, replay = _opt(args, "--rpc"), _opt(args, "--record"), _opt(args, "--replay")
    deployments = Path(_opt(args, "--deployments") or DEPLOYMENTS)
    if record and not rpc_url:
        raise SystemExit("--record needs --rpc (a live chain to record from)")
    if rpc_url and replay:
        raise SystemExit("--rpc reads the chain; --replay reads recorded answers: choose one")
    path = Path(args[0] if args else ROOT / "out" / "demo" / "run.json")
    run = json.loads(path.read_text())
    chain_id, contract = run["chainId"], run["deployment"]["methaneCovenant"]
    entries = run.get("hybrid") or sys.exit(f"{path}: no hybrid signatures (run `make demo` first)")
    all_ok = True
    if rpc_url or replay:
        print(f"covenant {contract} on chain {chain_id}, registry {run['deployment']['keyRegistry']} (named by "
              f"{path.name}); deployment record {deployments / f'{chain_id}.json'}")
        recorded = deployment_record_check(run, deployments, require=True)
        for ok, what in recorded:
            all_ok &= line(ok, what)
        if not all_ok:  # refused before anything is read from the chain
            return 1
        base = ve.FixtureRpc(Path(replay)) if replay else ve.LiveRpc([rpc_url], record=bool(record), backoff=0.5)
        rpc = ve.Memo(base)
        try:
            anchors = anchors_from_rpc(run, rpc_url or base.urls[0], [e["signer"] for e in entries], w3=CallW3(rpc))
            mode = f"replay of {base.urls[0]}" if replay else f"--rpc {rpc_url}"
            print(f"mode {mode}: covenant.registry() equals the run's registry; anchors read from chain {chain_id}")
            print("what the covenant checked before it accepted the bundle (MethaneCovenant._verifyQuorum):")
            for ok, what in contract_checks(run, rpc, entries):
                all_ok &= line(ok, what)
        except (ve.RpcError, ValueError) as err:
            line(False, f"{'RPC' if isinstance(err, ve.RpcError) else type(err).__name__}: {err} (fail closed)")
            return 1
        finally:
            if record:
                base.save(Path(record), note=f"read-only JSON-RPC answers of `scripts/verify_bundle.py --rpc` on "
                                             f"{path.name} (chain {chain_id}); replay with --replay")
    else:
        anchors = anchors_from_record(run)
        print("mode offline: anchors from the run's recorded registry read (consistency, not a live read)")
        for ok, what in deployment_record_check(run, deployments):
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
    if replay:  # the replay re-checks recorded answers; the size/time table is measured on a real run only
        return 0 if all_ok else 1

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
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
