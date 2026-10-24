"""JCS / evidenceHash tests: 3 shared vectors (Python and JS must agree) + adversarial inputs."""
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from canon import canonical_bytes, evidence_hash, evidence_hash_text  # noqa: E402

V = Path(__file__).parent / "vectors"
EXPECTED = json.loads((V / "expected.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_vectors(name):
    text = (V / name).read_text(encoding="utf-8")
    assert canonical_bytes(json.loads(text)).decode() == EXPECTED[name]["canonical"]
    assert evidence_hash_text(text) == EXPECTED[name]["evidenceHash"]


def test_rfc8785_published_example():
    # RFC 8785 §3.2.3 output, written with chr() so no editor or tool unescapes it.
    b = chr(92)
    want = ('{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],'
            '"string":"' + chr(0x20AC) + "$" + b + "u000f" + b + "nA'B" + b + '"' + b + b + b + b + b + '"/"}')
    assert EXPECTED["v2_rfc8785_example.json"]["canonical"] == want


def _tkm():
    return json.loads((V / "v1_tkm_bundle.json").read_text())


def test_adversarial_reordered_whitespace_same_hash():
    # Same content, different key order and whitespace -> identical evidenceHash (must pass).
    obj = _tkm()
    shuffled = json.dumps(dict(reversed(list(obj.items()))), indent=7)
    assert evidence_hash_text(shuffled) == EXPECTED["v1_tkm_bundle.json"]["evidenceHash"]


def test_adversarial_edited_rate_changes_hash():
    # The demo tamper: 2,548 kg/h edited to 548 kg/h -> different hash -> signatures no longer match.
    obj = _tkm()
    obj["emissionKgH"] = 548.2483082628996
    assert evidence_hash(obj) != EXPECTED["v1_tkm_bundle.json"]["evidenceHash"]


def test_adversarial_duplicate_key_rejected():
    # Two values for one field: parsers disagree on which wins, so the bundle is ambiguous -> reject.
    text = '{"emissionKgH": 2548.2, "emissionKgH": 548.2, "sourceId": "TKM_S_276"}'
    with pytest.raises(ValueError):
        evidence_hash_text(text)


@pytest.mark.parametrize("bad", ['{"emissionKgH": NaN}', '{"emissionKgH": Infinity}'])
def test_adversarial_non_finite_rejected(bad):
    with pytest.raises(ValueError):
        evidence_hash_text(bad)
    with pytest.raises(ValueError):
        evidence_hash({"emissionKgH": math.nan})
