"""IEA Annex C — MARS Feedback Form, as the seller's voice (spec-v2 §6).

The seller answers a detection with the operator feedback form IEA recommends for MARS notifications (IEA, "Responding
to Satellite Notifications from the Methane Alert and Response System", May 2026, Annex C; labels copied exactly). `claim_hash(form)` = SHA-256 of the RFC 8785 form is what the unchanged core takes in
`recordMitigationClaim(claimHash)`: the claim is recorded, timestamped and attributable to the seller, and it changes no
state — a claim is not evidence that the emission ceased. Annex D cites it as "seller's claim, hash on chain, state
unchanged".

`blank_form` pre-fills only what public data says (Source ID, the MARS plume id, the [SIM] operator); `example_form` is a
[SIM] seller's filled form for the demo. `validate(form, submit=True)` is IEA's "check form completeness" step: Part 1
must be answered; every label must be present and nothing else may be added outside `x-methaneclause`.

    python -m export.annex_c --case DZA_S_012 [--example]   # -> data/derived/export/<case>.annex_c.{json,csv}
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re

from canon import canonical_bytes
from common import DERIVED

SCHEMA = "methaneclause.annex_c/1"
TEMPLATE = "IEA, Annex C: MARS Feedback Form (Responding to Satellite Notifications from MARS, May 2026)"
NAMESPACE = "x-methaneclause"
OUT = DERIVED / "export"
DATE_LABEL = "Date of Completion of Feedback Form (DD-MM-YYYY)"
_P1 = ["Source ID", "Plume(s) ID(s)", "Operator", "Emissions cause",
       "Did the MARS notification alert you to the emissions?",
       "Have the emissions ceased? Was mitigation action taken to address the emissions?",
       "What date did the mitigation action occur?", "Executed or planned operator efforts"]
_P2 = ["Facility Name", "Facility Type", "Facility Contact Information", "Corporate Contact Information",
       "OGMP 2.0 Asset Name"]
_P3 = ["Emission Source Category", "Emission Source Category - If other, please specify",
       "Details of the specific point source within the facility (e.g. specific tank, compressor, flare)",
       "Briefly describe the root cause of the emissions event",
       "Operational situation or activity that led to the emission",
       "Operational situation or activity that led to the emission - If other, please specify",
       "Has the emission ceased or been mitigated or is it ongoing?",
       "If ceased/mitigated, describe the actions taken",
       "If ongoing, do you have plans to mitigate?", "If yes, describe the mitigation plans",
       "Timeline by which you expect to mitigate the emission (date)",
       "Are you able to quantify the emissions from this event?",
       "If yes, estimate of total emissions released (kg methane if possible)",
       "Duration of the event (hours or days)"]
FIELDS = ([{"part": 0, "label": DATE_LABEL}] + [{"part": 1, "label": x} for x in _P1]
          + [{"part": 2, "label": x} for x in _P2] + [{"part": 3, "label": x} for x in _P3])
LABELS = [f["label"] for f in FIELDS]
REQUIRED_ON_SUBMIT = [DATE_LABEL] + _P1
DATE_RE = re.compile(r"^\d{2}-\d{2}-\d{4}$")


class Incomplete(ValueError):
    """IEA's completeness check failed: a required answer is missing."""


def _case(case: str) -> dict:
    return json.loads((DERIVED / "cases" / f"{case}.json").read_text())


def blank_form(case: str) -> dict:
    ms = _case(case)["mars_source"]
    fields = {label: None for label in LABELS}
    fields.update({"Source ID": ms["source_name"], "Plume(s) ID(s)": ms["id_last_plume"],
                   "Operator": "[SIM] seller (the real operator is not named in public data)"})
    return {"schema": SCHEMA, "template": TEMPLATE, "fields": fields,
            NAMESPACE: {"label": "blank form; only public facts pre-filled", "case": case}}


def example_form(case: str) -> dict:
    """A [SIM] seller's completed form for the demo. Every answer is the simulated seller's claim, not a finding."""
    f = blank_form(case)
    ms = _case(case)["mars_source"]
    f["fields"].update({
        DATE_LABEL: "20-03-2026",
        "Emissions cause": "[SIM] seller's claim: unplanned venting during a compressor trip",
        "Did the MARS notification alert you to the emissions?": "[SIM] Yes",
        "Have the emissions ceased? Was mitigation action taken to address the emissions?":
            "[SIM] seller's claim: ceased; a relief valve was replaced",
        "What date did the mitigation action occur?": "15-03-2026",
        "Executed or planned operator efforts": "[SIM] valve replaced; quarterly LDAR survey planned",
        "Facility Name": f"[SIM] facility at MARS source {ms['source_name']}",
        "Facility Type": ms["source_type"],
        "Emission Source Category": "[SIM] Venting",
        "Has the emission ceased or been mitigated or is it ongoing?": "[SIM] Ceased",
        "If ceased/mitigated, describe the actions taken": "[SIM] relief valve replaced",
        "Are you able to quantify the emissions from this event?": "[SIM] No",
    })
    f[NAMESPACE] = {"label": "[SIM] example: a simulated seller's claim; recorded on chain by hash, changes no state",
                    "case": case}
    return f


def validate(form: dict, *, submit: bool = False) -> None:
    if form.get("schema") != SCHEMA or form.get("template") != TEMPLATE:
        raise ValueError("not an Annex C form")
    if set(form) - {"schema", "template", "fields", NAMESPACE}:
        raise ValueError(f"unexpected top-level keys {sorted(set(form) - {'schema', 'template', 'fields', NAMESPACE})}")
    fields = form["fields"]
    if list(fields) != LABELS:
        extra, missing = sorted(set(fields) - set(LABELS)), sorted(set(LABELS) - set(fields))
        raise ValueError(f"labels differ from the IEA template: extra {extra}, missing {missing}")
    if any(v is not None and not isinstance(v, str) for v in fields.values()):
        raise ValueError("answers are text (or null when unanswered)")
    for label in (DATE_LABEL, "What date did the mitigation action occur?",
                  "Timeline by which you expect to mitigate the emission (date)"):
        v = fields[label]
        if v and not DATE_RE.match(v):
            raise ValueError(f"{label}: expected DD-MM-YYYY, got {v!r}")
    if submit:
        missing = [k for k in REQUIRED_ON_SUBMIT if not fields[k]]
        if missing:
            raise Incomplete(f"Part 1 not answered: {missing}")


def claim_hash(form: dict) -> str:
    """bytes32 for recordMitigationClaim(claimHash): 0x + SHA-256(JCS(form))."""
    validate(form)
    return "0x" + hashlib.sha256(canonical_bytes(form)).hexdigest()


def from_json(text: str) -> dict:
    form = json.loads(text)
    validate(form)
    return form


def to_csv(form: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["part", "label", "answer"])
    w.writerow(["", "schema", form["schema"]])
    w.writerow(["", "template", form["template"]])
    for f in FIELDS:
        v = form["fields"][f["label"]]
        w.writerow([f["part"], f["label"], json.dumps(v)])
    w.writerow(["", NAMESPACE, json.dumps(form.get(NAMESPACE), sort_keys=True)])
    return buf.getvalue()


def from_csv(text: str) -> dict:
    rows = list(csv.reader(io.StringIO(text)))[1:]
    top = {r[1]: r[2] for r in rows if r[0] == ""}
    form = {"schema": top["schema"], "template": top["template"],
            "fields": {r[1]: json.loads(r[2]) for r in rows if r[0] != ""}}
    ns = json.loads(top[NAMESPACE])
    if ns is not None:
        form[NAMESPACE] = ns
    validate(form)
    return form


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", default="DZA_S_012")
    ap.add_argument("--example", action="store_true", help="the [SIM] seller's completed form")
    a = ap.parse_args()
    form = example_form(a.case) if a.example else blank_form(a.case)
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"{a.case}.annex_c" + (".example" if a.example else "")
    (OUT / f"{stem}.json").write_text(json.dumps(form, indent=1, ensure_ascii=False) + "\n")
    (OUT / f"{stem}.csv").write_text(to_csv(form))
    print(f"annex C: {stem} -> data/derived/export/ (claimHash {claim_hash(form)})")


if __name__ == "__main__":
    main()
