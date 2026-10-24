"""IEA Annex D export: official labels only, honest values, protocol fields namespaced, lossless round trip."""
from __future__ import annotations

import json

from canon import evidence_hash
from export.iea_annex_d import ANNEX_D_FIELDS, NAMESPACE, build, from_csv, from_json, to_csv


def test_official_fields_in_template_order_and_nothing_else():
    doc = build("TKM_S_276")
    assert list(doc["fields"]) == ANNEX_D_FIELDS
    assert not any(k.startswith("x-") for k in doc["fields"])


def test_never_claims_the_emission_ceased_and_keeps_mars_status_null():
    doc = build("TKM_S_276")
    assert doc["fields"]["Emissions ceased?"] == "NOT EVIDENCED"
    assert doc["fields"]["Date of MARS notification"] == "not in public data"
    assert doc["mars"]["source"]["emission_status"] is None  # empty in the public MARS file; never filled by us
    assert doc["fields"]["Operator"].startswith("[SIM]")


def test_embedded_bundle_hashes_to_the_attested_evidence():
    doc = build("TKM_S_276")
    ext = doc[NAMESPACE]
    assert evidence_hash(ext["bundle"]) == ext["bundleHash"] == ext["episode"]["evidenceHash"]


def test_json_round_trip_same_hash():
    doc = build("TKM_S_276")
    back = from_json(json.dumps(doc, indent=1))
    assert evidence_hash(back) == evidence_hash(doc)


def test_csv_round_trip_same_hash():
    doc = build("TKM_S_276")
    back = from_csv(to_csv(doc))
    assert evidence_hash(back) == evidence_hash(doc)
    assert evidence_hash(back[NAMESPACE]["bundle"]) == doc[NAMESPACE]["episode"]["evidenceHash"]


def test_import_rejects_a_file_without_the_template_fields():
    doc = build("TKM_S_276")
    del doc["fields"]["Emissions ceased?"]
    try:
        from_json(json.dumps(doc))
    except ValueError as e:
        assert "Emissions ceased?" in str(e)
    else:
        raise AssertionError("an incomplete export was accepted")


def test_eu_evidence_maps_without_claiming_compliance():
    ev = build("TKM_S_276")[NAMESPACE]["euEvidence"]
    assert ev["regulation"] == "Regulation (EU) 2024/1787"
    items = {i["item"]: i for i in ev["annexIX"]}
    assert {"producer identity", "source-level measurement", "LDAR / response evidence"} <= set(items)
    assert all(i["canSupport"].split(" ")[0] in ("yes", "partly", "no") for i in ev["annexIX"])
    assert ev["art2_62"]["superEmitterKgH"] == 100 and ev["art2_62"]["equalsClauseMinRate"] is True
    assert "left blank" in ev["rec2026_1834"]["text"] and ev["rec2026_1834"]["url"].startswith("https://eur-lex")
    assert {s["id"] for s in ev["relatedStandards"]} == {"IEEE 2418.5-2025", "IEEE 3218-2022", "IEEE P3271.01"}
    assert all("conform" in s["relation"] for s in ev["relatedStandards"])
    assert "never" in ev["wording"].lower()


def test_mars_vocabulary_mapping_never_fills_the_mars_field():
    from export.iea_annex_d import mars_vocabulary

    doc = build("TKM_S_276")
    assert doc["mars"]["source"]["emission_status"] is None  # read from the export, not constructed here
    m = doc[NAMESPACE]["marsVocabulary"]
    assert m["marsField"] == "emission_status" and m["source"].startswith("https://methanedata.unep.org/")
    assert m["termForCountedPasses"] is None and m["episodeConfirmed"] == "Ongoing"
    dza = mars_vocabulary("DZA_S_012", episode_state="CONFIRMED")
    assert dza["termForCountedPasses"] == "Potentially ceased"
    assert dza["thisSource"]["firstPlumeAfterSince"].startswith("2026-02-01")  # the relapse
    assert dza["currentTerm"] == "Ongoing"  # back to Ongoing after the 2026-02-01 relapse
    assert "relapseAfterTheRun" not in json.dumps(dza)


def test_potentially_ceased_definition_and_card_sentence_from_the_file():
    from common import DERIVED
    from export.iea_annex_d import mars_vocabulary

    m = mars_vocabulary("DZA_S_012", episode_state="CONFIRMED")
    assert m["terms"]["Potentially ceased"] == ("MARS term for a source with several validated plume-free "
                                                "hyperspectral observations within the last 6 months; here applied "
                                                "to the clause's counted passes")
    card = json.loads((DERIVED / "cleanruns.json").read_text())["card"]["sentence"]
    assert m["whyCapped"] == card


def test_eu_wording_for_measurement_and_articles_not_live():
    ev = build("TKM_S_276")[NAMESPACE]["euEvidence"]
    item = next(i for i in ev["annexIX"] if i["item"] == "source-level measurement")
    assert item["canSupport"] == "partly (independent satellite measurement, not the producer's)"
    assert ev["art30"].endswith("(due under the Regulation; not live as of 15 Oct 2026)")
    assert ev["art31"].endswith("(due under the Regulation; not live as of 15 Oct 2026)")
