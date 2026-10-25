"""Signing and verification time: secp256k1 ECDSA (eth-account) vs ML-DSA-44 (dilithium-py), same machine, same run.

Both halves of a hybrid observer signature are timed over what they sign in the product: ECDSA over the EIP-712
digest of the recorded demo attestation, ML-DSA-44 over the RFC 8785 bundle bytes with the FIPS 204 context string
(scripts/tests/fixtures/verify_evidence/bundle_run.json, first hybrid entry). Keys are the public Anvil/demo test
keys. dilithium-py is a pure-Python reference implementation, not an optimised one; the ECDSA backend in use is
recorded too. Medians of n runs in milliseconds.

    pipeline/.venv/bin/python scripts/pq_bench.py [--n 200]    # writes data/derived/pq_bench.json
"""
from __future__ import annotations

import argparse
import importlib.metadata as md
import json
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))

from eth_account import Account  # noqa: E402
from eth_keys import KeyAPI  # noqa: E402

from observer import attest, pq  # noqa: E402
from observer.bundle import bundle_bytes  # noqa: E402
from observer.service import anvil_key  # noqa: E402

FIXTURE = ROOT / "scripts" / "tests" / "fixtures" / "verify_evidence" / "bundle_run.json"
OUT = ROOT / "data" / "derived" / "pq_bench.json"


def median_ms(fn, n: int) -> float:
    fn()  # warm-up, not counted
    times = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return round(statistics.median(times), 3)


def run(n: int) -> dict:
    rec = json.loads(FIXTURE.read_text())
    entry = rec["hybrid"][0]
    chain_id, contract = rec["chainId"], rec["deployment"]["methaneCovenant"]
    att = {k: (bytes.fromhex(v[2:]) if isinstance(v, str) and v.startswith("0x") and len(v) == 66 else v)
           for k, v in entry["attestation"].items()}
    message = bundle_bytes(entry["bundle"])

    ecdsa_pk = anvil_key(3)
    address = Account.from_key(ecdsa_pk).address
    sig = attest.sign(att, ecdsa_pk, chain_id, contract)
    assert attest.recover(att, sig, chain_id, contract) == address

    pq_pk, pq_sk = pq.keypair(pq.seed_for("OBS_A", 31337))
    pq_sig = pq.sign(pq_sk, message)
    assert pq.verify(pq_pk, message, pq_sig)

    return {
        "what": "median sign / verify time per signature, milliseconds, one process on one machine",
        "n": n,
        "ecdsa_secp256k1": {
            "library": f"eth-account {md.version('eth-account')} (eth-keys {md.version('eth-keys')}, backend "
                       f"{type(KeyAPI().backend).__name__})",
            "signs": "the recorded demo Attestation: EIP-712 typed-data encoding plus the signature",
            "verify": "EIP-712 encoding plus ecrecover, compared with the signer address",
            "sign_ms": median_ms(lambda: attest.sign(att, ecdsa_pk, chain_id, contract), n),
            "verify_ms": median_ms(lambda: attest.recover(att, sig, chain_id, contract) == address, n),
        },
        "mldsa44": {
            "library": f"dilithium-py {md.version('dilithium-py')}",
            "signs": f"RFC 8785 bundle bytes ({len(message)} bytes), FIPS 204 context {pq.CONTEXT.decode()}, hedged",
            "verify": "FIPS 204 ML-DSA.Verify with the same context",
            "sign_ms": median_ms(lambda: pq.sign(pq_sk, message), n),
            "verify_ms": median_ms(lambda: pq.verify(pq_pk, message, pq_sig), n),
        },
        "note": "dilithium-py is a pure-Python reference implementation, not an optimised one",
        "machine": f"{platform.system()} {platform.release()} {platform.machine()}, Python {platform.python_version()}",
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--n", type=int, default=200)
    a = ap.parse_args()
    out = run(a.n)
    OUT.write_text(json.dumps(out, indent=1, sort_keys=True) + "\n")
    print(json.dumps(out, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
