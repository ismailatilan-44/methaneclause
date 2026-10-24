#!/usr/bin/env python3
"""W2 (spec-v2 §9.1): how long it takes to verify one rebate decision by hand and with the tool.

    pipeline/.venv/bin/python scripts/measure_verification.py tool [--n 5] [--live]   # make measure-verification
    pipeline/.venv/bin/python scripts/measure_verification.py manual                 # make measure-verification-manual
    pipeline/.venv/bin/python scripts/measure_verification.py show

The question is the same both ways, for the lead case TKM_S_276: did a quantified plume reappear after UNEP MARS
labelled the source, and is the attested evidence exactly what the observers signed?

  manual  the written protocol below, timed step by step by the author (press Enter after each step); one run per call,
          repeat until n >= 5.
  tool    `tool-once` in a fresh Python process (start-up included): read the MARS record and the Carbon Mapper passes
          since the label from the committed case file, fetch the plume (committed snapshot; --live: Carbon Mapper now),
          build + validate + hash the canonical bundle, compare with the attested evidenceHash, and verify both
          observers' ECDSA and ML-DSA-44 signatures. n runs (default 5), wall-clock seconds each.

Writes data/derived/verification_time.json (schema methaneclause.verification_time/1): each `tool` call replaces the
tool runs (one mode per median: snapshot or live); `manual` appends one run. The manual protocol uses the live MARS and
Carbon Mapper portals; the tool's default mode reads the pinned snapshot (the committed case file).
Labelled everywhere "author-measured, not a user study". Not part of `make check`: the times depend on the machine.
"""
from __future__ import annotations

import argparse
import json
import platform
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
OUT = ROOT / "data" / "derived" / "verification_time.json"
CASE = "TKM_S_276"
LABEL = "author-measured, not a user study"

MANUAL_STEPS = [
    {"id": "mars", "text": "Open the UNEP MARS record of the source (MARS data portal) and note its persistency "
                           "category and the date of MARS's last plume."},
    {"id": "catalogue", "text": "Find the Carbon Mapper sources within 1.5 km of the MARS point in the Carbon Mapper "
                                "data portal."},
    {"id": "passes", "text": "List every Carbon Mapper pass since MARS's last plume, with plume / no plume."},
    {"id": "compare", "text": "Compare dates: note the most recent quantified plume after the label, its date, rate "
                              "and uncertainty."},
    {"id": "hash", "text": "Write the bundle fields, canonicalise them by the published RFC 8785 steps, compute "
                           "SHA-256 and compare it with the attested evidenceHash."},
]


def load() -> dict:
    if OUT.exists():
        return json.loads(OUT.read_text())
    return {"schema": "methaneclause.verification_time/1", "label": LABEL, "case": CASE,
            "question": "did a quantified plume reappear after the MARS label, and is the attested evidence exactly "
                        "what the observers signed?",
            "manual": {"n": 0, "steps": MANUAL_STEPS, "runs": []}, "tool": {"n": 0, "runs": []}}


def stats(xs: list[float]) -> dict:
    return {"medianSeconds": round(statistics.median(xs), 2), "minSeconds": round(min(xs), 2),
            "maxSeconds": round(max(xs), 2)} if xs else {"medianSeconds": None, "minSeconds": None, "maxSeconds": None}


def save(doc: dict) -> None:
    m, t = doc["manual"], doc["tool"]
    m.update(n=len(m["runs"]), steps=MANUAL_STEPS, mode="live portals (UNEP MARS, Carbon Mapper)",
             **stats([r["totalSeconds"] for r in m["runs"]]))
    m["stepMedianSeconds"] = {s["id"]: round(statistics.median(r["stepSeconds"][s["id"]] for r in m["runs"]), 1)
                              for s in MANUAL_STEPS} if m["runs"] else {}
    t.update(n=len(t["runs"]), **stats([r["seconds"] for r in t["runs"]]))
    OUT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"-> {OUT.relative_to(ROOT)}: manual n={m['n']} median {m['medianSeconds']} s · tool n={t['n']} "
          f"median {t['medianSeconds']} s ({LABEL})")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------------------------------------- tool path
def tool_once(live: bool) -> int:
    """One tool run: the same question as the manual protocol, answered from the committed files (or live)."""
    from observer.bundle import build_bundle, bundle_hash, validate
    from observer.service import Observer, load_facility
    from policy import POLICY

    hyb = json.loads((ROOT / "app" / "data" / "app.json").read_text())["hybrid"]
    lead = hyb["entries"][0]
    facility, covered, case = load_facility(CASE)
    since = case["post_mars_last_plume"]["since"]
    passes = [r for r in case["cm_timeline_union"] if r["date"] >= since]
    obs = Observer("OBS_A", hyb["chainId"], hyb["verifyingContract"], live=live)
    plume, h, _ = obs.fetch_plume(case, lead["bundle"]["plumeId"])
    b = build_bundle(plume, covenant_id=lead["bundle"]["covenantId"], source_id=facility["sourceId"],
                     source_name=facility["source_name"], content_sha256=h)
    validate(b, facility=facility, covered_cm=covered, covenant_start=POLICY["start"], now=int(time.time()),
             min_rate_kgh=POLICY["minRateKgH"])
    same = bundle_hash(b) == lead["evidenceHash"]
    sigs = _verify_signatures(hyb)
    print(f"MARS '{case['mars_source']['persistency_category']}' since {since}; {len(passes)} Carbon Mapper passes "
          f"since; plume {b['plumeId']} {b['emissionKgH']} kg/h; hash {'equal' if same else 'DIFFERS'}; "
          f"signatures {'valid' if sigs else 'INVALID'}")
    return 0 if same and sigs else 1


def _verify_signatures(hyb: dict) -> bool:
    from canon import canonical_bytes
    from observer import attest, pq

    good = True
    for e in hyb["entries"]:
        att = {k: (attest.b32(v) if isinstance(v, str) and v.startswith("0x") else int(v))
               for k, v in e["attestation"].items()}
        rec = attest.recover(att, bytes.fromhex(e["signature"][2:]), hyb["chainId"], hyb["verifyingContract"])
        m = e["mldsa44"]
        good &= rec.lower() == e["signer"].lower() and pq.verify(
            bytes.fromhex(m["publicKey"][2:]), canonical_bytes(e["bundle"]), bytes.fromhex(m["sig"][2:]))
    return good


def run_tool(n: int, live: bool) -> None:
    """Replaces the tool runs on each call, so a median never mixes snapshot and live runs."""
    doc = load()
    doc["tool"]["runs"] = []
    cmd = [sys.executable, str(Path(__file__).resolve()), "tool-once"] + (["--live"] if live else [])
    for i in range(n):
        t0 = time.perf_counter()
        r = subprocess.run(cmd, capture_output=True, text=True)
        dt = time.perf_counter() - t0
        if r.returncode != 0:
            sys.exit(f"tool run {i + 1} failed:\n{r.stdout}{r.stderr}")
        print(f"   tool run {i + 1}/{n}: {dt:.2f} s  {r.stdout.strip().splitlines()[-1]}")
        doc["tool"]["runs"].append({"at": now_iso(), "seconds": round(dt, 3), "mode": "live" if live else "snapshot"})
    doc["tool"].update(mode="live" if live else "snapshot",
                       modeNote="Carbon Mapper fetched live" if live else
                       "pinned snapshot: the committed case file (the manual protocol uses the live portals)",
                       command="pipeline/.venv/bin/python scripts/measure_verification.py tool-once"
                               + (" --live" if live else ""),
                       machine=f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
                       what="fresh process: MARS record + Carbon Mapper passes since the label (case file), plume "
                            "fetch, canonical bundle, validation, SHA-256 vs evidenceHash, ECDSA + ML-DSA-44 of both "
                            "observers")
    save(doc)


def run_manual() -> None:
    if not sys.stdin.isatty():
        sys.exit("manual timing is interactive: run it in a terminal (make measure-verification-manual)")
    doc = load()
    print(f"Manual protocol for {CASE} ({LABEL}). Press Enter when each step is done; Ctrl-C aborts without saving.")
    input("Ready? Press Enter to start the clock. ")
    secs: dict[str, float] = {}
    for i, s in enumerate(MANUAL_STEPS, 1):
        t0 = time.perf_counter()
        input(f"\n[{i}/{len(MANUAL_STEPS)}] {s['text']}\n   Enter when done ")
        secs[s["id"]] = round(time.perf_counter() - t0, 1)
        print(f"   {secs[s['id']]} s")
    doc["manual"]["runs"].append({"at": now_iso(), "stepSeconds": secs, "totalSeconds": round(sum(secs.values()), 1)})
    save(doc)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=["tool", "manual", "show", "tool-once"])
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--live", action="store_true")
    a = ap.parse_args()
    if a.mode == "tool-once":
        sys.exit(tool_once(a.live))
    if a.mode == "tool":
        run_tool(a.n, a.live)
    elif a.mode == "manual":
        run_manual()
    else:
        print(json.dumps(load(), indent=1))


if __name__ == "__main__":
    main()
