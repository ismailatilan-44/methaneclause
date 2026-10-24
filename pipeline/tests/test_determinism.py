"""Opt-in determinism hooks for the demo (requested by the demo): defaults stay random, explicit parameters make runs repeatable."""
from observer import pq
from observer.service import Observer

CASE, PLUME = "TKM_S_276", "tan20260611t083132c23s4001-AB"
COV = "0x" + "00" * 31 + "01"
CONTRACT = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"
START, NOW = 1767225600, 1783765160


def _attest(**kw):
    return Observer("OBS_A", 31337, CONTRACT).attest(CASE, PLUME, covenant_id=COV, covenant_start=START, now=NOW, **kw)


def test_pq_sign_defaults_to_hedged_random():
    pk, sk = pq.keypair(pq.seed_for("OBS_A", 31337))
    a, b = pq.sign(sk, b"bundle"), pq.sign(sk, b"bundle")
    assert a != b and pq.verify(pk, b"bundle", a) and pq.verify(pk, b"bundle", b)


def test_pq_sign_deterministic_is_repeatable_and_valid():
    pk, sk = pq.keypair(pq.seed_for("OBS_A", 31337))
    a, b = pq.sign(sk, b"bundle", deterministic=True), pq.sign(sk, b"bundle", deterministic=True)
    assert a == b and pq.verify(pk, b"bundle", a)


def test_attest_defaults_are_random():
    a, b = _attest(), _attest()
    assert a["attestation"]["nonce"] != b["attestation"]["nonce"]
    assert a["signature"] != b["signature"]
    assert a["mldsa44"]["sig"] != b["mldsa44"]["sig"]


def test_attest_with_explicit_nonce_and_deterministic_pq_is_repeatable():
    a, b = _attest(nonce=42, pq_deterministic=True), _attest(nonce=42, pq_deterministic=True)
    assert a["attestation"]["nonce"] == 42
    assert a["signature"] == b["signature"] and a["mldsa44"]["sig"] == b["mldsa44"]["sig"]


def test_opt_in_determinism_refused_off_anvil():
    import pytest

    from observer import cleanpass as cp

    with pytest.raises(ValueError):
        Observer("OBS_A", 11155111, CONTRACT, pk="0x" + "11" * 32).attest(CASE, PLUME, covenant_id=COV,
                                                                         covenant_start=START, now=NOW, nonce=1)
    with pytest.raises(ValueError):
        Observer("OBS_A", 11155111, CONTRACT, pk="0x" + "11" * 32).attest(CASE, PLUME, covenant_id=COV,
                                                                         covenant_start=START, now=NOW,
                                                                         pq_deterministic=True)
    doc = cp.load_case("DZA_S_012")
    b = cp.build_bundle(doc, cp.find_scene(doc, "2025-02-12 emi"), covenant_id=COV, claim_id="0x" + "ab" * 32)
    ctx = cp.claim_context(doc)
    for kw in ({"nonce": 1}, {"pq_deterministic": True}):
        with pytest.raises(ValueError):
            cp.sign(b, org="OBS_A", chain_id=11155111, pool=CONTRACT, ctx=ctx, pk="0x" + "11" * 32, **kw)
        with pytest.raises(ValueError):
            cp.CleanPassObserver("OBS_A", 11155111, CONTRACT, pk="0x" + "11" * 32).attest(
                "DZA_S_012", "2025-02-12 emi", covenant_id=COV, claim_id="0x" + "ab" * 32, **kw)
    c = cp.build_corroboration(doc, claim_id="0x" + "ab" * 32)
    with pytest.raises(ValueError):
        cp.sign_corroboration(c, org="OBS_C", chain_id=11155111, pool=CONTRACT, now=ctx["now"],
                              expiry=ctx["now"] + 60, nonce=1, pk="0x" + "11" * 32)


def test_service_has_no_pq_sign_bridge():
    from observer import service

    assert not hasattr(service, "pq_sign")
