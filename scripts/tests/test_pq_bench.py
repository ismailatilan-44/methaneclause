"""docs/SECURITY.md section 9 quotes the signing and verification times from data/derived/pq_bench.json; the numbers in
the sentence must equal the file (rounded to 0.1 ms), so nothing is typed by hand. scripts/pq_bench.py writes the file."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_security_md_pq_timing_matches_the_json():
    b = json.loads((ROOT / "data" / "derived" / "pq_bench.json").read_text())
    text = " ".join((ROOT / "docs" / "SECURITY.md").read_text().split())
    m = re.search(r"Time \(median of (\d+) runs on one machine.*?ML-DSA-44 signs the bundle in ([\d.]+) ms and verifies "
                  r"it in ([\d.]+) ms; secp256k1 ECDSA signs the EIP-712 attestation in ([\d.]+) ms and recovers it in "
                  r"([\d.]+) ms", text)
    assert m, "the timing sentence is missing from docs/SECURITY.md section 9"
    n, pq_sign, pq_verify, ec_sign, ec_verify = m.groups()
    assert int(n) == b["n"]
    assert pq_sign == f"{b['mldsa44']['sign_ms']:.1f}" and pq_verify == f"{b['mldsa44']['verify_ms']:.1f}"
    assert ec_sign == f"{b['ecdsa_secp256k1']['sign_ms']:.1f}" and ec_verify == f"{b['ecdsa_secp256k1']['verify_ms']:.1f}"
    assert "pure-Python reference implementation, not an optimised one" in b["note"]
    assert "NativeECCBackend" in b["ecdsa_secp256k1"]["library"]  # the sentence says eth-keys runs in pure Python
