"""IEA Annex D export ("Summary of satellite-detected emission event") for one covered source, JSON + CSV.

Official Annex D labels only carry what public data supports; anything we cannot evidence says so in words
("not in public data", "NOT EVIDENCED"). `Emissions ceased?` is never YES: a clean pass is not a clean site.
MARS dictionary fields are copied verbatim (`emission_status` is empty in the public file and stays null).
Everything MethaneClause adds (episode, evidenceHash, the canonical bundle, tx links) lives under `x-methaneclause`
and is never presented as an official field.

v2 additions under `x-methaneclause` (spec-v2 §6): `euEvidence` — which items of Regulation (EU) 2024/1787 the record
can support as evidence (Annex IX, Art. 30, Art. 2(62), Art. 31, Rec. (EU) 2026/1834), never a statement of compliance;
`marsVocabulary` — the MARS `emission_status` term each clause state corresponds to (cited, never written into MARS's
field). "Mitigation measures taken?" cites a seller's Annex C claim by its on-chain hash when one is given.

Round trip: export -> import -> export gives the same canonical (RFC 8785) hash, and the embedded bundle still hashes
to the committed evidenceHash (pipeline/tests/test_export.py).

    python -m export.iea_annex_d [--source TKM_S_276]   # -> data/derived/export/<source>.annex_d.{json,csv}
"""
from __future__ import annotations

import argparse
import csv
import io
import json

from canon import canonical_bytes, evidence_hash
from common import CM_LICENCE, DERIVED, MARS_LICENCE
from observer.bundle import build_bundle
from observer.service import load_facility

OUT = DERIVED / "export"
TEMPLATE = "IEA, Annex D: Summary of satellite-detected emission event (response database template)"
NOT_PUBLIC = "not in public data"
ANNEX_D_FIELDS = [  # official labels, in template order
    "Source ID", "Plume ID", "Level", "Operator", "Date of MARS notification", "Notification",
    "Facility information", "Date of operator response", "Response", "Source identified?",
    "Description of emission source", "Emissions ceased?", "Mitigation measures taken?",
    "Event duration and total estimated emissions", "Verification activities and next steps",
]
MARS_PLUME_FIELDS = ["source_name", "id_plume", "satellite", "tile_date", "ch4_fluxrate", "ch4_fluxrate_std"]
MARS_SOURCE_FIELDS = ["source_name", "persistency_category", "emission_status"]
NAMESPACE = "x-methaneclause"
LEAD_PLUME = "tan20260611t083132c23s4001-AB"  # the demo episode's plume (observer/demo.py)
MARS_DICTIONARY = "https://methanedata.unep.org/dict-mars-plumes"
REG = "https://eur-lex.europa.eu/eli/reg/2024/1787/oj/eng"
REC = "https://eur-lex.europa.eu/eli/reco/2026/1834/oj/eng"


def eu_evidence() -> dict:
    """Evidence mapping for Regulation (EU) 2024/1787 (source: the Regulation's text on
    EUR-Lex, `REG`). What the record can support; it never states that anyone meets an obligation."""
    from policy import POLICY

    return {
        "regulation": "Regulation (EU) 2024/1787", "url": REG,
        "wording": "evidence an importer or producer may cite; never a statement of compliance",
        "annexIX": [
            {"item": "producer identity", "canSupport": "no",
             "how": "the record names the facility (MARS source) and the covenant's seller; the producer is named only "
                    "if the supply contract names it (the seller is [SIM] here)"},
            {"item": "source-level measurement",
             "canSupport": "partly (independent satellite measurement, not the producer's)",
             "how": "satellite plume rates with uncertainty at the source, attested by k observer organisations; not "
                    "an operator's site-level measurement or a third-party verification of one"},
            {"item": "LDAR / response evidence", "canSupport": "partly",
             "how": "detection -> episode -> rebate timeline on chain, and the seller's Annex C claim by hash; a "
                    "claim is not evidence that the emission ceased"},
        ],
        "art30": "methane transparency database and performance profiles analyse super-emitting events; the record "
                 "is an independently attested event history a profile can cite (due under the Regulation; not "
                 "live as of 15 Oct 2026)",
        "art2_62": {"superEmitterKgH": 100, "clauseMinRateKgH": POLICY["minRateKgH"],
                    "equalsClauseMinRate": POLICY["minRateKgH"] == 100,
                    "text": "Art. 2(62) defines a super-emitting event as more than 100 kg of methane per hour; the "
                            "clause's minimum rate is the same threshold"},
        "art31": "the rapid reaction mechanism notifies super-emitting events and asks for remedial measures; this "
                 "clause is the contract's reaction to the same alert (due under the Regulation; not live as of "
                 "15 Oct 2026)",
        "rec2026_1834": {"url": REC, "text": "Recommendation (EU) 2026/1834's four model clauses pass information "
                                             "only; the economic clause the Commission left blank"},
        "relatedStandards": [
            {"id": "IEEE 2418.5-2025", "area": "blockchain in energy",
             "relation": "context only; we do not claim to conform to it"},
            {"id": "IEEE 3218-2022", "area": "blockchain-based carbon trading",
             "relation": "context only; we do not claim to conform to it"},
            {"id": "IEEE P3271.01", "area": "recurring payments (non-custodial pull pattern)",
             "relation": "context only (a draft standard); we do not claim to conform to it"},
        ],
    }


def mars_vocabulary(source: str, *, episode_state: str | None) -> dict:
    """MARS `emission_status` terms (cited from the MARS data dictionary) for this source's clause state. MARS's own
    field stays null in every export: we cite the term, we never fill it."""
    counted, relapse = 0, None
    cp = DERIVED / "cases" / f"{source}.clean_passes.json"
    if cp.exists():
        s = json.loads(cp.read_text())["summary"]
        counted = s["qualifiedBeforeFirstPlume"]
        relapse = (s.get("firstPlumeAfterSince") or {}).get("sceneAtIso")
    run_term = "Potentially ceased" if counted >= 3 else None
    return {
        "marsField": "emission_status", "source": MARS_DICTIONARY,
        "episodeConfirmed": "Ongoing",
        "claimWithAtLeast3CountedPasses": "Potentially ceased",
        "terms": {"Ongoing": "at least one plume observed recently",
                  "Potentially ceased": "MARS term for a source with several validated plume-free hyperspectral "
                                        "observations within the last 6 months; here applied to the clause's counted "
                                        "passes"},
        "whyCapped": json.loads((DERIVED / "cleanruns.json").read_text())["card"]["sentence"],
        "thisSource": {"episodeState": episode_state,
                       "termForEpisode": "Ongoing" if episode_state == "CONFIRMED" else None,
                       "countedCleanPasses": counted, "firstPlumeAfterSince": relapse},
        "termForCountedPasses": run_term,
        "currentTerm": "Ongoing" if relapse else (run_term or ("Ongoing" if episode_state == "CONFIRMED" else None)),
        "note": "a mapping of vocabulary, not MARS's assessment; MARS's emission_status is null in the public file "
                "and is never filled by us; 'Potentially ceased' is MARS's term, not a finding of repair",
    }


def _run_record() -> dict:
    """The attested episode: the public-testnet run if present, else the local demo."""
    t = DERIVED / "testnet_run.json"
    if t.exists():
        r = json.loads(t.read_text())
        prop = next(s for s in r["steps"] if s["step"] == "proposeEpisode")
        fin = next((s for s in r["steps"] if s["step"] == "finalize"), None)
        return {"chain": r["network"], "covenantContract": r["methaneCovenant"], "covenantId": r["covenantId"],
                "episodeId": r["episodeId"], "evidenceHash": r["evidenceHash"], "proposeTx": prop["tx"],
                "tier": prop.get("tier"), "state": "CONFIRMED" if fin and fin["status"] == 1 else "PROPOSED",
                "plume": r["plume"], "record": "data/derived/testnet_run.json"}
    d = json.loads((DERIVED / "demo_run.json").read_text())
    return {"chain": d["chain"], "covenantId": "0x" + "00" * 31 + "01", "evidenceHash": d["evidenceHash"],
            "state": "CONFIRMED", "plume": LEAD_PLUME, "record": "data/derived/demo_run.json"}


def build(source: str = "TKM_S_276", annex_c_claim_hash: str | None = None) -> dict:
    facility, _, cf = load_facility(source)
    ms = cf["mars_source"]
    run = _run_record()
    post = cf["post_mars_last_plume"]
    dets = sorted((p for p in cf["cm_plumes_within_radius"] if p["scene_timestamp"][:10] >= post["since"]),
                  key=lambda p: p["scene_timestamp"])
    row = next((p for p in cf["cm_plumes_within_radius"] if p["plume_id"] == run.get("plume")), None)
    bundle = None
    if row:
        bundle = build_bundle(dict(row), covenant_id=run["covenantId"], source_id=facility["sourceId"],
                              source_name=facility["source_name"],
                              content_sha256=cf["cm_sources"][row["source_name"]]["content_sha256"])
    annex = {
        "Source ID": ms["source_name"],
        "Plume ID": ms["id_last_plume"],
        "Level": NOT_PUBLIC,
        "Operator": "[SIM] seller (a simulated party; the real operator is not named in public data)",
        "Date of MARS notification": NOT_PUBLIC,
        "Notification": f"{NOT_PUBLIC} (MARS lists notified = {ms['notified']})",
        "Facility information": f"{ms['source_type']}, {ms['sector']}, {ms['country']}; lat {ms['lat']}, lon {ms['lon']}",
        "Date of operator response": NOT_PUBLIC,
        "Response": f"{NOT_PUBLIC} (MARS lists feedback_operator = {ms['feedback_operator']})",
        "Source identified?": NOT_PUBLIC,
        "Description of emission source": f"MARS source type: {ms['source_type']}",
        "Emissions ceased?": "NOT EVIDENCED",
        "Mitigation measures taken?": (
            f"NOT EVIDENCED; seller's claim (IEA Annex C feedback form), hash on chain {annex_c_claim_hash}, state "
            "unchanged" if annex_c_claim_hash else
            "NOT EVIDENCED (a seller mitigation claim, if recorded on chain, changes no state)"),
        "Event duration and total estimated emissions": "",  # ongoing: the template leaves it blank
        "Verification activities and next steps": (
            f"Independent re-detection: Carbon Mapper detected a plume on {post['n_cm_detected']} of "
            f"{post['n_cm_passes']} passes over the sources within {post['radius_km']:g} km of the facility since "
            f"{post['since']} (MARS persistency_category = "
            f"{ms['persistency_category']}). Attested on chain by 2 observer organisations [SIM keys]. "
            "Next step: on-site inspection."),
    }
    mars = {"source": {k: ms.get(k) for k in MARS_SOURCE_FIELDS},
            "plumes": [{k: p.get(k) for k in MARS_PLUME_FIELDS} | {"source_name": ms["source_name"]}
                       for p in cf["mars_plumes"]]}
    ext = {"note": "Protocol fields added by MethaneClause; not part of the IEA template.",
           "episode": run, "bundle": bundle, "bundleHash": evidence_hash(bundle) if bundle else None,
           "carbonMapperDetectionsSince": post["since"],
           "carbonMapperPlumesSince": [{k: d.get(k) for k in ("plume_id", "scene_timestamp", "emission_auto",
                                                              "emission_uncertainty_auto", "instrument")}
                                       for d in dets],
           "licences": [f"{CM_LICENCE} — Data by Carbon Mapper®", MARS_LICENCE], "euEvidence": eu_evidence(),
           "marsVocabulary": mars_vocabulary(source, episode_state=run.get("state"))}
    return {"template": TEMPLATE, "fields": annex, "mars": mars, NAMESPACE: ext}


def to_csv(doc: dict) -> str:
    """Two columns, official fields first; nested values JSON-encoded so the CSV round-trips exactly."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["field", "value"])
    w.writerow(["template", doc["template"]])
    for k in ANNEX_D_FIELDS:
        w.writerow([k, doc["fields"][k]])
    w.writerow(["mars", json.dumps(doc["mars"], sort_keys=True)])
    w.writerow([NAMESPACE, json.dumps(doc[NAMESPACE], sort_keys=True)])
    return buf.getvalue()


def from_csv(text: str) -> dict:
    rows = dict(list(csv.reader(io.StringIO(text)))[1:])
    return {"template": rows["template"], "fields": {k: rows[k] for k in ANNEX_D_FIELDS},
            "mars": json.loads(rows["mars"]), NAMESPACE: json.loads(rows[NAMESPACE])}


def from_json(text: str) -> dict:
    doc = json.loads(text)
    missing = [k for k in ANNEX_D_FIELDS if k not in doc["fields"]]
    if missing:
        raise ValueError(f"not an Annex D export: missing {missing}")
    return doc


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--source", default="TKM_S_276")
    a = ap.parse_args()
    doc = build(a.source)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{a.source}.annex_d.json").write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n")
    (OUT / f"{a.source}.annex_d.csv").write_text(to_csv(doc))
    print(f"export: {a.source} -> data/derived/export/ (canonical hash {evidence_hash(doc)}, "
          f"{len(canonical_bytes(doc))} bytes JCS)")


if __name__ == "__main__":
    main()
