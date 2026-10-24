"""sourceId / Merkle compatibility with contracts/src/lib/SigLib.sol."""
import subprocess
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from merkle import hx, keccak256, leaf, proof, root, source_id, verify  # noqa: E402

NAMES = ["TKM_S_276", "DZA_S_012", "USA_S_077", "CH4_1B2_250m_53.71782_39.48437", "LBY_S_119"]


def test_keccak_known_vector():
    assert keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


@pytest.mark.skipif(not shutil.which("cast"), reason="foundry cast not on PATH")
def test_source_id_matches_cast():
    out = subprocess.run(["cast", "keccak", "TKM_S_276"], capture_output=True, text=True).stdout.strip()
    assert hx(source_id("TKM_S_276")) == out


def test_every_proof_verifies_and_odd_sizes():
    for k in range(1, len(NAMES) + 1):
        sids = [source_id(n) for n in NAMES[:k]]
        rt = root(sids)
        for s in sids:
            assert verify(proof(sids, s), rt, leaf(s))


def test_non_member_and_tampered_proof_fail():
    sids = [source_id(n) for n in NAMES]
    rt = root(sids)
    assert not verify(proof(sids, sids[0]), rt, leaf(source_id("TKM_S_342")))
    bad = proof(sids, sids[1])
    bad[0] = bytes(32)
    assert not verify(bad, rt, leaf(sids[1]))
