"""RFC 8785 (JCS) canonical JSON -> SHA-256 = evidenceHash.

The JS side (`canonicalize` npm package) must reproduce the hashes in pipeline/tests/vectors/.
Usage: python canon.py bundle.json   -> prints 0x<sha256>
"""
from __future__ import annotations

import hashlib
import json
import math
import sys

import rfc8785


def _check(o) -> None:
    # JCS cannot represent NaN/Infinity; reject early with a clear message.
    if isinstance(o, float) and not math.isfinite(o):
        raise ValueError("non-finite number is not valid JSON")
    if isinstance(o, dict):
        for k, v in o.items():
            if not isinstance(k, str):
                raise TypeError("JSON object keys must be strings")
            _check(v)
    elif isinstance(o, list):
        for v in o:
            _check(v)


def canonical_bytes(obj) -> bytes:
    _check(obj)
    return rfc8785.dumps(obj)


def evidence_hash(obj) -> str:
    """0x-prefixed lowercase hex SHA-256 of the JCS bytes (bytes32 on-chain)."""
    return "0x" + hashlib.sha256(canonical_bytes(obj)).hexdigest()


def evidence_hash_text(text: str) -> str:
    """Hash a JSON document given as text. Duplicate keys are rejected (ambiguous evidence)."""
    def no_dupes(pairs):
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate key in JSON object")
        return dict(pairs)

    return evidence_hash(json.loads(text, object_pairs_hook=no_dupes,
                                    parse_constant=lambda c: (_ for _ in ()).throw(ValueError(c))))


if __name__ == "__main__":
    print(evidence_hash_text(open(sys.argv[1]).read()))
