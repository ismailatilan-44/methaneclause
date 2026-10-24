"""EIP-712 Attestation, byte-compatible with contracts/src/lib/Attestation.sol (see contracts/README.md)."""
from __future__ import annotations

from eth_account import Account
from eth_account.messages import encode_typed_data

KIND_DETECT, KIND_CONFIRM, KIND_WITHDRAW = 1, 2, 3
MAX_EXPIRY_HORIZON = 86_400  # contract: expiry <= now + 1 day

TYPES = {
    "EIP712Domain": [
        {"name": "name", "type": "string"},
        {"name": "version", "type": "string"},
        {"name": "chainId", "type": "uint256"},
        {"name": "verifyingContract", "type": "address"},
    ],
    "Attestation": [
        {"name": "covenantId", "type": "bytes32"},
        {"name": "sourceId", "type": "bytes32"},
        {"name": "evidenceHash", "type": "bytes32"},
        {"name": "modelHash", "type": "bytes32"},
        {"name": "emissionKgH", "type": "uint32"},
        {"name": "uncertaintyKgH", "type": "uint32"},
        {"name": "detectedAt", "type": "uint64"},
        {"name": "instrumentId", "type": "uint8"},
        {"name": "providerId", "type": "bytes32"},
        {"name": "kind", "type": "uint8"},
        {"name": "nonce", "type": "uint64"},
        {"name": "expiry", "type": "uint64"},
    ],
}
ORDER = [f["name"] for f in TYPES["Attestation"]]


def b32(x: str | bytes) -> bytes:
    """0x-hex of exactly 32 bytes -> bytes; short ASCII (Solidity `bytes32 c = "carbonmapper"`) -> right-padded."""
    if isinstance(x, str) and x.startswith("0x"):
        b = bytes.fromhex(x[2:])
        if len(b) != 32:
            raise ValueError("bytes32 hex must be 32 bytes")
        return b
    b = x if isinstance(x, bytes) else x.encode()
    if len(b) > 32:
        raise ValueError("longer than 32 bytes")
    return b.ljust(32, b"\0")


def typed(att: dict, chain_id: int, verifying_contract: str) -> dict:
    return {
        "types": TYPES,
        "primaryType": "Attestation",
        "domain": {"name": "MethaneClause", "version": "1", "chainId": chain_id,
                   "verifyingContract": verifying_contract},
        "message": att,
    }


def digest(att: dict, chain_id: int, verifying_contract: str) -> bytes:
    from eth_account.messages import _hash_eip191_message  # keccak(0x19 0x01 ‖ domain ‖ struct)

    return _hash_eip191_message(encode_typed_data(full_message=typed(att, chain_id, verifying_contract)))


def sign(att: dict, private_key, chain_id: int, verifying_contract: str) -> bytes:
    """65-byte r ‖ s ‖ v, v in {27, 28}, low-s (eth-account canonicalises s)."""
    signed = Account.sign_message(encode_typed_data(full_message=typed(att, chain_id, verifying_contract)), private_key)
    return bytes(signed.signature)


def recover(att: dict, sig: bytes, chain_id: int, verifying_contract: str) -> str:
    return Account.recover_message(encode_typed_data(full_message=typed(att, chain_id, verifying_contract)),
                                   signature=sig)


def as_tuple(att: dict) -> tuple:
    """Struct order for web3 contract calls."""
    return tuple(att[k] for k in ORDER)


# ---------------------------------------------------------------------- v2: IncentivePool attestation types
# Byte-compatible with contracts/src/lib/CleanPass.sol (spec-v2 §3.1). Same domain name/version as the core, bound to
# the pool's address. Test vector: pipeline/tests/vectors/cleanpass_v1.json (checked by Python and by Foundry).
KIND_CLEAN, KIND_CONFIRM_CLEAN, KIND_WITHDRAW_CLEAN, KIND_CORROBORATE = 4, 5, 6, 7

POOL_TYPES = {
    "EIP712Domain": TYPES["EIP712Domain"],
    "CleanPass": [
        {"name": "claimId", "type": "bytes32"},
        {"name": "sourceId", "type": "bytes32"},
        {"name": "sceneId", "type": "bytes32"},
        {"name": "sceneHash", "type": "bytes32"},
        {"name": "sceneAt", "type": "uint64"},
        {"name": "publishedAt", "type": "uint64"},
        {"name": "instrumentId", "type": "uint8"},
        {"name": "providerId", "type": "bytes32"},
        {"name": "cloudBp", "type": "uint16"},
        {"name": "notCloudy", "type": "bool"},
        {"name": "floorKgH", "type": "uint32"},
        {"name": "rateFloorKgH", "type": "uint32"},
        {"name": "kind", "type": "uint8"},
        {"name": "nonce", "type": "uint64"},
        {"name": "expiry", "type": "uint64"},
    ],
    "Corroboration": [
        {"name": "claimId", "type": "bytes32"},
        {"name": "sourceId", "type": "bytes32"},
        {"name": "providerId", "type": "bytes32"},
        {"name": "marsSnapshotHash", "type": "bytes32"},
        {"name": "windowFrom", "type": "uint64"},
        {"name": "windowTo", "type": "uint64"},
        {"name": "marsPlumesInWindow", "type": "uint16"},
        {"name": "kind", "type": "uint8"},
        {"name": "nonce", "type": "uint64"},
        {"name": "expiry", "type": "uint64"},
    ],
}
CLEANPASS_ORDER = [f["name"] for f in POOL_TYPES["CleanPass"]]
CORROBORATION_ORDER = [f["name"] for f in POOL_TYPES["Corroboration"]]


def _pool_typed(primary: str, msg: dict, chain_id: int, pool: str) -> dict:
    types = {"EIP712Domain": POOL_TYPES["EIP712Domain"], primary: POOL_TYPES[primary]}
    return {"types": types, "primaryType": primary,
            "domain": {"name": "MethaneClause", "version": "1", "chainId": chain_id, "verifyingContract": pool},
            "message": msg}


def pool_digest(primary: str, msg: dict, chain_id: int, pool: str) -> bytes:
    """EIP-712 digest for "CleanPass" or "Corroboration" under the pool's domain."""
    from eth_account.messages import _hash_eip191_message

    return _hash_eip191_message(encode_typed_data(full_message=_pool_typed(primary, msg, chain_id, pool)))


def pool_sign(primary: str, msg: dict, private_key, chain_id: int, pool: str) -> bytes:
    """65-byte r ‖ s ‖ v (low-s), the format SigLib accepts."""
    signed = Account.sign_message(encode_typed_data(full_message=_pool_typed(primary, msg, chain_id, pool)), private_key)
    return bytes(signed.signature)


def pool_recover(primary: str, msg: dict, sig: bytes, chain_id: int, pool: str) -> str:
    return Account.recover_message(encode_typed_data(full_message=_pool_typed(primary, msg, chain_id, pool)), signature=sig)


def cleanpass_tuple(cp: dict) -> tuple:
    return tuple(cp[k] for k in CLEANPASS_ORDER)


def corroboration_tuple(c: dict) -> tuple:
    return tuple(c[k] for k in CORROBORATION_ORDER)
