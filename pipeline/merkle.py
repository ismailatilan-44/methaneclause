"""sourceId and covered-source Merkle tree, byte-compatible with contracts/src/lib/SigLib.sol.

  sourceId = keccak256(bytes(source_name))                      (as contracts/test/utils/Fixture.sol)
  leaf     = keccak256(keccak256(abi.encode(bytes32 sourceId)))   (SigLib.sourceLeaf, OZ StandardMerkleTree style)
  node     = keccak256(abi.encode(min(a,b), max(a,b)))           (sorted pairs, SigLib.verifyProof)
  layout   = leaves in the given order, pairs (2i, 2i+1), odd node promoted unchanged (as Fixture._root/_proof)
"""
from __future__ import annotations

from Crypto.Hash import keccak


def keccak256(b: bytes) -> bytes:
    return keccak.new(digest_bits=256, data=b).digest()


def hx(b: bytes) -> str:
    return "0x" + b.hex()


def source_id(name: str) -> bytes:
    return keccak256(name.encode("utf-8"))


def leaf(sid: bytes) -> bytes:
    assert len(sid) == 32
    return keccak256(keccak256(sid))  # abi.encode(bytes32) is the 32 bytes themselves


def _pair(a: bytes, b: bytes) -> bytes:
    return keccak256(a + b) if a < b else keccak256(b + a)


def _up(level: list[bytes]) -> list[bytes]:
    return [_pair(level[i], level[i + 1]) if i + 1 < len(level) else level[i] for i in range(0, len(level), 2)]


def root(sids: list[bytes]) -> bytes:
    level = [leaf(s) for s in sids]
    while len(level) > 1:
        level = _up(level)
    return level[0]


def proof(sids: list[bytes], sid: bytes) -> list[bytes]:
    level, idx, out = [leaf(s) for s in sids], sids.index(sid), []
    while len(level) > 1:
        if (idx ^ 1) < len(level):
            out.append(level[idx ^ 1])
        level, idx = _up(level), idx // 2
    return out


def verify(prf: list[bytes], rt: bytes, lf: bytes) -> bool:
    h = lf
    for p in prf:
        h = _pair(h, p)
    return h == rt
