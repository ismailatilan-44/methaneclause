"""Hybrid signatures, the post-quantum half: ML-DSA-44 (FIPS 204) over the same RFC 8785 bundle bytes.

The secp256k1 EIP-712 signature is what the contract checks. ML-DSA-44 signs the canonical bundle bytes with the FIPS
204 context string "MethaneClause/v1" and is verified off-chain (scripts/verify_bundle.py, and in the browser by the
app); KeyRegistry only anchors SHA-256(public key) as `pqPubKeyHash` with algId 2.

Seeds are independent of the ECDSA key on purpose: a seed derived from the ECDSA private key would fall to the same
quantum attack that recovers that key from its public key, and the second signature would protect nothing.
  Anvil (31337): seed = SHA-256("MethaneClause ML-DSA-44 test seed / <ORG>") — public test seeds, like Anvil's keys.
  Elsewhere: OBSERVER_{A,B,C}_MLDSA_SEED (32-byte hex); absent -> no PQ signature (the key stays algId 1).

Honest limit: EVM transaction identity and the on-chain check remain ECDSA, which Shor breaks; this protects the
long-term authenticity of bundles verified off-chain. Not a quantum-safe chain.

    python -m observer.pq env    # export lines with OBSERVER_{A,B,C}_PQ_HASH for the Anvil deploy
"""
from __future__ import annotations

import hashlib
import os
import sys

from dilithium_py.ml_dsa import ML_DSA_44

CONTEXT = b"MethaneClause/v1"
ALG_ECDSA, ALG_HYBRID = 1, 2
ORGS = ("OBS_A", "OBS_B", "OBS_C")


def seed_for(org: str, chain_id: int) -> bytes | None:
    env = os.environ.get(f"OBSERVER_{org[-1]}_MLDSA_SEED")
    if env:
        b = bytes.fromhex(env.removeprefix("0x"))
        if len(b) != 32:
            raise SystemExit(f"OBSERVER_{org[-1]}_MLDSA_SEED must be 32 bytes")
        return b
    if chain_id == 31337:
        return hashlib.sha256(f"MethaneClause ML-DSA-44 test seed / {org}".encode()).digest()
    return None


def keypair(seed: bytes) -> tuple[bytes, bytes]:
    """FIPS 204 ML-DSA.KeyGen_internal from a 32-byte seed: deterministic, so the public key is reproducible."""
    return ML_DSA_44.key_derive(seed)


def pk_hash(pk: bytes) -> str:
    return "0x" + hashlib.sha256(pk).hexdigest()


def sign(sk: bytes, message: bytes, deterministic: bool = False) -> bytes:
    """Default: FIPS 204 hedged signing (fresh 32 random bytes per signature). `deterministic=True` uses FIPS 204's
    deterministic variant (rnd = 32 zero bytes, exposed by dilithium-py), for repeatable demo runs only; both verify
    with the same public key."""
    return ML_DSA_44.sign(sk, message, ctx=CONTEXT, deterministic=deterministic)


def verify(pk: bytes, message: bytes, sig: bytes) -> bool:
    return ML_DSA_44.verify(pk, message, sig, ctx=CONTEXT)


def main() -> None:
    if sys.argv[1:] != ["env"]:
        raise SystemExit("usage: python -m observer.pq env")
    for org in ORGS:
        print(f"export OBSERVER_{org[-1]}_PQ_HASH={pk_hash(keypair(seed_for(org, 31337))[0])}")


if __name__ == "__main__":
    main()
