"""Gas against facility-set size (pipeline/observer/scale.py -> data/derived/scale.json): the sets are what the file says,
the committed measurement is internally consistent, and its sentence is rendered from the file (offline; no chain)."""
import json
import math

import pytest

from common import DERIVED
from merkle import leaf, source_id, verify
from observer import scale


@pytest.fixture(scope="module")
def sets():
    return scale.build_sets()


@pytest.fixture(scope="module")
def doc():
    return json.loads((DERIVED / "scale.json").read_text())


def test_sets_grow_to_ten_thousand_and_hold_the_case(sets):
    assert [s["key"] for s in sets] == ["v2", "v3", "mars_targets", "padded"]
    assert [s["n"] for s in sets] == sorted(s["n"] for s in sets) and sets[-1]["n"] == scale.PAD_TO
    for s in sets:
        assert verify([bytes.fromhex(p[2:]) for p in s["proof"]], bytes.fromhex(s["root"][2:]),
                      leaf(source_id(scale.CASE))), s["key"]
        assert len(s["proof"]) <= s["maxProofSteps"] == math.ceil(math.log2(s["n"]))


def test_real_sets_are_the_committed_ones_and_padding_is_labelled(sets):
    for s, v in zip(sets[:2], ("v2", "v3")):
        committed = json.loads((DERIVED / f"sourceset_{v}.json").read_text())
        assert s["root"] == committed["root"] and s["n"] == committed["n"] and s["real"]
    assert sets[2]["real"] and sets[2]["n"] == len(scale.mars_targets())
    assert not sets[3]["real"] and "[SIM padding]" in sets[3]["label"]


def test_committed_measurement_matches_the_sets(sets, doc):
    assert [r["set"] for r in doc["rows"]] == [s["key"] for s in sets]
    for r, s in zip(doc["rows"], sets):
        assert (r["n"], r["root"], r["proofSteps"], r["maxProofSteps"]) == (s["n"], s["root"], len(s["proof"]),
                                                                            s["maxProofSteps"])


def test_create_is_flat_propose_grows_with_the_path_and_wrong_paths_fail_closed(doc):
    rows = doc["rows"]
    cc = [r["createCovenantGas"] for r in rows]
    assert max(cc) - min(cc) <= 1_000
    pe = [r["proposeEpisodeGas"] for r in rows]
    assert pe == sorted(pe) and pe[-1] > pe[0]
    assert [r["proofSteps"] for r in rows] == sorted(r["proofSteps"] for r in rows)
    calldata = [r["proposeEpisodeCalldataBytes"] for r in rows]
    assert all(b - a == 32 * (rb["proofSteps"] - ra["proofSteps"])
               for a, b, ra, rb in zip(calldata, calldata[1:], rows, rows[1:]))  # one bytes32 per step, nothing else
    for r in rows:
        w = r["wrongProof"]
        assert w["status"] == 0 and w["revert"] == "NotCovered" and w["episodeCountAndBondUnchanged"]
    assert doc["status0"] == len(rows)


def test_fit_and_sentence_are_rendered_from_the_file(doc):
    assert scale.fit(doc["rows"]) == doc["fit"]
    assert 100 <= doc["fit"]["proposeEpisodeGasPerProofStep"] <= 3_000
    assert scale.sentence(doc) == doc["sentence"]
    assert "[SIM padding]" in doc["sentence"]


def test_fit_slope_on_known_rows():
    rows = [{"proofSteps": k, "proposeEpisodeGas": 1000 + 800 * k, "createCovenantGas": 5} for k in (2, 8, 11, 12)]
    assert scale.fit(rows) == {"proposeEpisodeGasPerProofStep": 800, "createCovenantGasMin": 5,
                               "createCovenantGasMax": 5}
