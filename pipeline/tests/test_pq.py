"""Hybrid signature, PQ half: ML-DSA-44 (FIPS 204) with the "MethaneClause/v1" context over the canonical bundle."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from observer import pq
from observer.bundle import bundle_bytes
from observer.service import Observer

CASE, PLUME = "TKM_S_276", "tan20260611t083132c23s4001-AB"
COV = "0x" + "00" * 31 + "01"
CONTRACT = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"


def anvil_anchor(org: str) -> str:
    """The pqPubKeyHash Deploy.s.sol registers on Anvil (`python -m observer.pq env` -> OBSERVER_<X>_PQ_HASH)."""
    return pq.pk_hash(pq.keypair(pq.seed_for(org, 31337))[0])


def _verify_bundle():
    path = Path(__file__).resolve().parents[2] / "scripts" / "verify_bundle.py"
    spec = importlib.util.spec_from_file_location("verify_bundle", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _attest_obs_a() -> dict:
    return Observer("OBS_A", 31337, CONTRACT).attest(CASE, PLUME, covenant_id=COV, covenant_start=1767225600,
                                                      now=1783765160)


def test_sizes_and_deterministic_keygen():
    pk, sk = pq.keypair(b"\x01" * 32)
    assert len(pk) == 1312 and pq.keypair(b"\x01" * 32)[0] == pk
    assert len(pq.sign(sk, b"m")) == 2420


def test_context_and_message_are_bound():
    pk, sk = pq.keypair(b"\x02" * 32)
    sig = pq.sign(sk, b"bundle")
    assert pq.verify(pk, b"bundle", sig)
    assert not pq.verify(pk, b"bundlf", sig)
    from dilithium_py.ml_dsa import ML_DSA_44
    assert not ML_DSA_44.verify(pk, b"bundle", sig, ctx=b"other/v1")  # a signature for another protocol fails


def test_observer_signs_the_same_bytes_the_evidence_hash_commits_to():
    s = _attest_obs_a()
    m = s["mldsa44"]
    pk = bytes.fromhex(m["publicKey"][2:])
    assert pq.pk_hash(pk) == anvil_anchor("OBS_A")  # the hash the Anvil deploy registers, not the bundle's own field
    assert pq.verify(pk, bundle_bytes(s["bundle"]), bytes.fromhex(m["sig"][2:]))
    edited = dict(s["bundle"], emissionKgH=548)
    assert not pq.verify(pk, bundle_bytes(edited), bytes.fromhex(m["sig"][2:]))


def test_pq_seed_is_not_derived_from_the_ecdsa_key(monkeypatch):
    """Off Anvil there is no PQ key unless its own seed is configured (never derived from the ECDSA key)."""
    monkeypatch.delenv("OBSERVER_A_MLDSA_SEED", raising=False)
    assert pq.seed_for("OBS_A", 11155111) is None


def test_verify_bundle_rejects_foreign_mldsa_key():
    """verify_bundle binds the ML-DSA half to the registry's pqPubKeyHash for the signer, never to the bundle's pkHash."""
    vb = _verify_bundle()
    s = _attest_obs_a()
    entry = {"bundle": s["bundle"], "mldsa44": dict(s["mldsa44"])}
    anchor = {"algId": pq.ALG_HYBRID, "pqPubKeyHash": anvil_anchor("OBS_A"), "source": "test"}
    assert vb.check_mldsa(entry, anchor)[0]

    # a foreign ML-DSA keypair signs the same canonical bytes; the bundle carries the foreign key and its recomputed hash
    fpk, fsk = pq.keypair(b"\x66" * 32)
    fsig = pq.sign(fsk, bundle_bytes(s["bundle"]))
    foreign = {"bundle": s["bundle"], "mldsa44": dict(s["mldsa44"], publicKey="0x" + fpk.hex(), sig="0x" + fsig.hex(),
                                                       pkHash=pq.pk_hash(fpk))}
    assert pq.verify(fpk, bundle_bytes(s["bundle"]), fsig)  # the signature itself is valid ...
    ok, msg = vb.check_mldsa(foreign, anchor)
    assert not ok and "!=" in msg                          # ... but not by the anchored key: FAIL

    assert not vb.check_mldsa(entry, None)[0]                              # no registry anchor: fail closed
    assert not vb.check_mldsa(entry, dict(anchor, algId=pq.ALG_ECDSA))[0]  # registered ECDSA-only: fail


def test_verify_bundle_anchor_comes_from_the_deployments_registry():
    """A registry read recorded for another registry anchors nothing."""
    vb = _verify_bundle()
    rec = {"address": "0x90F79bf6EB2c4f870365E785982E1f101E93b906", "algId": 2, "pqPubKeyHash": anvil_anchor("OBS_A"),
           "registry": "0x5FbDB2315678afecb367f032d93F642f64180aa3", "readAtBlock": 1}
    run = {"deployment": {"keyRegistry": rec["registry"]}, "registeredKeys": {"OBS_A": rec}}
    assert vb.anchors_from_record(run)[rec["address"].lower()]["pqPubKeyHash"] == anvil_anchor("OBS_A")
    run["deployment"]["keyRegistry"] = "0x" + "11" * 20
    assert vb.anchors_from_record(run) == {}


class _FakeW3:
    """Just enough of web3 for anchors_from_rpc: a covenant whose registry() is `registry`, and that registry's getKey.
    Every call is logged with the address it was made on."""

    def __init__(self, chain_id: int, covenant: str, registry: str, keys: dict):
        self.calls: list[tuple[str, str]] = []
        log = self.calls

        class _Call:
            def __init__(self, value):
                self.value = value

            def call(self):
                return self.value

        class _Fns:
            def __init__(self, address):
                self.address = address

            def registry(self):
                log.append(("registry", self.address))
                assert self.address.lower() == covenant.lower(), "registry() must be read from the covenant"
                return _Call(registry)

            def getKey(self, signer):  # noqa: N802 (contract function name)
                log.append(("getKey", self.address))
                assert self.address.lower() == registry.lower(), "getKey must hit covenant.registry()"
                return _Call(keys[signer.lower()])

        class _Contract:
            def __init__(self, address):
                self.address = address
                self.functions = _Fns(address)

        class _Eth:
            def __init__(self):
                self.chain_id = chain_id

            def contract(self, address, abi):
                return _Contract(address)

        self.eth = _Eth()


def test_verify_bundle_rpc_uses_covenant_registry():
    """--rpc reads the registry from MethaneCovenant.registry(), never from the run file, and refuses a mismatch."""
    vb = _verify_bundle()
    cov, reg, signer = ("0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512", "0x5FbDB2315678afecb367f032d93F642f64180aa3",
                        "0x90F79bf6EB2c4f870365E785982E1f101E93b906")
    key = (b"\x00" * 32, 1, 2, 0, 0, bytes.fromhex(anvil_anchor("OBS_A")[2:]), "0x" + "00" * 20)
    run = {"chainId": 31337, "deployment": {"methaneCovenant": cov, "keyRegistry": reg}}
    w3 = _FakeW3(31337, cov, reg, {signer.lower(): key})
    got = vb.anchors_from_rpc(run, "unused", [signer], w3=w3)
    assert got[signer.lower()]["pqPubKeyHash"] == anvil_anchor("OBS_A")
    assert w3.calls[0] == ("registry", cov) and w3.calls[1] == ("getKey", reg)

    # the run names a registry the covenant does not trust: refused before any key is read
    other = {"chainId": 31337, "deployment": {"methaneCovenant": cov, "keyRegistry": "0x" + "22" * 20}}
    w3 = _FakeW3(31337, cov, reg, {signer.lower(): key})
    with pytest.raises(SystemExit):
        vb.anchors_from_rpc(other, "unused", [signer], w3=w3)
    assert [c for c in w3.calls if c[0] == "getKey"] == []
    with pytest.raises(SystemExit):  # an RPC for another chain
        vb.anchors_from_rpc(run, "unused", [signer], w3=_FakeW3(1, cov, reg, {}))


def test_verify_bundle_offline_compares_the_deployment_record(tmp_path):
    vb = _verify_bundle()
    dep = {"methaneCovenant": "0xe7f1725E7734CE288F8367e1Bb143E90bb3F0512",
           "keyRegistry": "0x5FbDB2315678afecb367f032d93F642f64180aa3"}
    run = {"chainId": 31337, "deployment": dict(dep)}
    assert all(ok for ok, _ in vb.deployment_record_check(run, tmp_path))  # no record: nothing to compare (said)
    (tmp_path / "31337.json").write_text(json.dumps(dep))
    assert all(ok for ok, _ in vb.deployment_record_check(run, tmp_path))
    run["deployment"]["keyRegistry"] = "0x" + "33" * 20
    assert not all(ok for ok, _ in vb.deployment_record_check(run, tmp_path))
    sepolia = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "deployments" / "11155111.json")
                         .read_text())
    run = {"chainId": 11155111, "deployment": {k: sepolia[k] for k in ("methaneCovenant", "keyRegistry")}}
    assert all(ok for ok, _ in vb.deployment_record_check(run))  # the committed Sepolia record
