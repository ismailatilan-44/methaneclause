"""IEA Annex C (MARS feedback form) as the seller's voice (spec-v2 §6): exact IEA labels, completeness check, the hash
that `recordMitigationClaim(claimHash)` takes, lossless round trip, and Annex D citing it without changing any state."""
from __future__ import annotations

import json

import pytest
from canon import canonical_bytes

from export import annex_c as ac


def test_labels_are_the_iea_template_in_order():
    labels = [f["label"] for f in ac.FIELDS]
    assert labels[0] == "Date of Completion of Feedback Form (DD-MM-YYYY)"
    assert labels[1:4] == ["Source ID", "Plume(s) ID(s)", "Operator"]
    assert "Have the emissions ceased? Was mitigation action taken to address the emissions?" in labels
    assert "OGMP 2.0 Asset Name" in labels and "Duration of the event (hours or days)" in labels
    assert sum(f["part"] == 1 for f in ac.FIELDS) == 8 and sum(f["part"] == 2 for f in ac.FIELDS) == 5
    assert len(labels) == len(set(labels))


def test_blank_form_prefills_only_public_facts():
    f = ac.blank_form("DZA_S_012")
    assert f["fields"]["Source ID"] == "DZA_S_012" and f["fields"]["Operator"].startswith("[SIM]")
    assert f["fields"]["Plume(s) ID(s)"]  # MARS's last plume id
    assert f["fields"]["Have the emissions ceased? Was mitigation action taken to address the emissions?"] is None
    with pytest.raises(ac.Incomplete):
        ac.validate(f, submit=True)


def test_example_form_is_complete_labelled_and_hashes():
    f = ac.example_form("DZA_S_012")
    ac.validate(f, submit=True)
    assert f["x-methaneclause"]["label"].startswith("[SIM]")
    h = ac.claim_hash(f)
    assert h.startswith("0x") and len(h) == 66 and h == ac.claim_hash(json.loads(json.dumps(f)))
    assert ac.claim_hash(f) == "0x" + __import__("hashlib").sha256(canonical_bytes(f)).hexdigest()


@pytest.mark.parametrize("edit", ["extra", "missing", "date"])
def test_malformed_forms_are_refused(edit):
    f = ac.example_form("DZA_S_012")
    if edit == "extra":
        f["fields"]["Our own field"] = "x"
    elif edit == "missing":
        del f["fields"]["Facility Name"]
    else:
        f["fields"]["Date of Completion of Feedback Form (DD-MM-YYYY)"] = "2026/03/20"
    with pytest.raises(ValueError):
        ac.validate(f, submit=True)


def test_round_trip_json_and_csv_keep_the_claim_hash():
    f = ac.example_form("DZA_S_012")
    assert ac.claim_hash(ac.from_json(json.dumps(f))) == ac.claim_hash(f)
    assert ac.claim_hash(ac.from_csv(ac.to_csv(f))) == ac.claim_hash(f)


def test_annex_d_cites_the_claim_as_the_sellers_and_changes_nothing():
    from export import iea_annex_d

    f = ac.example_form("TKM_S_276")
    doc = iea_annex_d.build("TKM_S_276", annex_c_claim_hash=ac.claim_hash(f))
    m = doc["fields"]["Mitigation measures taken?"]
    assert "seller's claim" in m and ac.claim_hash(f) in m and "state unchanged" in m
    assert doc["fields"]["Emissions ceased?"] == "NOT EVIDENCED"
