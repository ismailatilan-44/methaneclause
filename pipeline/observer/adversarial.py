"""The adversarial input set (spec §10): every bad input, which layer catches it, and the detection rate.

Layers, reported separately so none takes credit for another:
  validation   the observer refuses to sign (bundle.validate raises a stable code)
  hash         the input passes validation, but the canonical bundle no longer hashes to the committed evidenceHash,
               so the observers' signatures do not verify (on chain: InvalidSignature) and a re-fetch exposes it
  re-check     the signatures verify (the signers lied together, [SIM]); a re-fetch from the provider shows the signed
               field is false, which is an objective challenge reason on chain
Subjects: "detection" (v1 Attestation bundles) and "clean pass" (v2 CleanPass bundles + the MARS Corroboration; every
refusal code of spec-v2 §2 has at least one case, built from real DZA scenes in the committed case file).

Slow drift is the case statistics would miss: a stored rate nudged -3 % per pass over 6 passes stays inside every
physical range, so validation passes each step; it is caught only because a re-fetch from the provider rebuilds a
bundle whose hash differs from the drifted one. We say exactly that, not that a detector spotted a trend.

    python -m observer.adversarial      # -> data/derived/adversarial.json (audited)
"""
from __future__ import annotations

import copy
import json

from common import DERIVED, write_json
from observer import attest
from observer.bundle import ValidationError, build_bundle, bundle_hash, validate
from observer.service import Observer, load_facility

CASE, PLUME = "TKM_S_276", "tan20260611t083132c23s4001-AB"
COV = "0x" + "00" * 31 + "01"
START, NOW = 1767225600, 1783765160  # 2026-01-01; 1 h after the plume was published
CONTRACT = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"
OUT = DERIVED / "adversarial.json"

FACILITY, COVERED, CASE_FILE = load_facility(CASE)
PLUME_ROW = next(p for p in CASE_FILE["cm_plumes_within_radius"] if p["plume_id"] == PLUME)
CONTENT = CASE_FILE["cm_sources"][PLUME_ROW["source_name"]]["content_sha256"]

# (label, plume-row edits, expected validation code)
BAD = [
    ("negative rate", {"emission_auto": -2548.0}, "RANGE"),
    ("rate 1e6 kg/h", {"emission_auto": 1e6}, "RANGE"),
    ("unit swap t/h written as kg/h", {"emission_auto": 2.548, "emission_uncertainty_auto": 0.483}, "BELOW_FLOOR"),
    ("unit swap kg/h written as t/h", {"emission_auto": 2548.0 * 1000}, "RANGE"),
    ("no quantification (real CM rows have null rates)", {"emission_auto": None}, "SCHEMA"),
    ("missing emission_version", {"emission_version": None}, "SCHEMA"),
    ("future timestamp", {"scene_timestamp": "2026-12-01T08:31:32Z", "published_at": "2026-12-02T00:00:00Z"},
     "TIME"),
    ("before covenant start", {"scene_timestamp": "2025-12-14T08:00:00Z", "published_at": "2026-01-10T00:00:00Z"},
     "TIME"),
    ("published before acquired", {"published_at": "2026-06-01T00:00:00Z"}, "TIME"),
    ("plume 50 km from the facility", {"lat": PLUME_ROW["lat"] + 0.45}, "DISTANCE"),
    ("CM source not linked to the facility", {"source_name": "CH4_1B2_1000m_5.99636_31.77708"}, "NOT_COVERED"),
    ("unknown instrument", {"instrument": "xyz"}, "SCHEMA"),
]
DRIFT_STEP, DRIFT_PASSES = 0.97, 6


def bundle(**edits) -> dict:
    return build_bundle({**PLUME_ROW, **edits}, covenant_id=COV, source_id=FACILITY["sourceId"],
                        source_name=FACILITY["source_name"], content_sha256=CONTENT)


def check(b: dict, seen: set[str] | None = None) -> None:
    validate(b, facility=FACILITY, covered_cm=COVERED, covenant_start=START, now=NOW, seen_plumes=seen)


def refused(b: dict, seen: set[str] | None = None) -> str | None:
    try:
        check(b, seen)
    except ValidationError as e:
        return e.code
    return None


def cleanpass_cases() -> list[dict]:
    """Clean-pass inputs that must be refused (spec-v2 §2 codes), edited after signing, or caught on re-check."""
    from observer import cleanpass as cp

    doc = cp.load_case("DZA_S_012")
    claim, pool = "0x" + "ab" * 32, CONTRACT
    out = []

    def add(label, layer, expected, got, note=""):
        out.append({"subject": "clean pass", "case": label, "layer": layer, "expected": expected, "got": got,
                    "caught": got == expected, **({"note": note} if note else {})})

    def bundle(scene="2025-02-12 emi", **edits):
        b = cp.build_bundle(doc, cp.find_scene(doc, scene), covenant_id=COV, claim_id=claim)
        b.update(edits)
        return b

    def refused(b, **ctx_edits):
        try:
            cp.validate(b, {**cp.claim_context(doc), **ctx_edits})
        except cp.CleanPassRefused as e:
            return e.code
        return None

    base = bundle()
    t0 = base["sceneAt"]
    psq = [dict(q) for q in base["perSourceQueries"]]
    psq[0]["plumeCount"] = 1
    near = dict(base["plumesNearFacility"], count=1, plumeIds=["(plume filed under another source)"])
    mars = {"windowDays": 7, "count": 1, "rows": [{"id_plume": "x", "tile_date": "2025-02-09T10:00:00",
                                                    "satellite": "Sentinel-2 - ESA", "ch4_fluxrate": 2000.0}]}
    v = [
        ("not_cloudy false (cloud 80 / 80)", bundle(notCloudy=False, cloudCoverPct=80, cloudCoverPctAssessed=80), {},
         "CLOUD_FLAG"),
        ("not_cloudy true but cloud 42 / 0", bundle(cloudCoverPct=42, cloudCoverPctAssessed=0), {},
         "CLOUD_FLAG_MISMATCH"),
        ("footprint holds only a later-published origin (real 2025-07-08 Tanager)", bundle("2025-07-08 tan"), {},
         "NO_ORIGIN_INTERSECT"),
        ("plume of a linked source on the scene", bundle(perSourceQueries=psq), {}, "PLUME_ON_SCENE"),
        ("plume within 1.5 km filed under another source", bundle(plumesNearFacility=near), {}, "PLUME_ON_SCENE"),
        ("MARS plume 3 days before the scene", bundle(marsNearby=mars), {}, "MARS_PLUME_NEARBY"),
        ("inside the episode's link window (real 2024-10-26 Tanager)", bundle("2024-10-26 tan"), {},
         "SCENE_BEFORE_SINCE"),
        ("scene record not published", bundle(publishedAt=None), {}, "SCENE_NOT_PUBLISHED"),
        ("attested once published but inside the 60-day lag (real 2024-12-16 Tanager, published day 46)",
         bundle("2024-12-16 tan"), {"now": bundle("2024-12-16 tan")["publishedAt"] + 86400}, "BEFORE_PUBLICATION_LAG"),
        ("scene after the covenant term", bundle(), {"covenantEnd": t0}, "AFTER_TERM"),
        ("same scene twice for one claim", bundle(), {"seen": {base["sceneProviderId"]}}, "DUPLICATE_SCENE"),
        ("instrument without a published floor", bundle(instrument="ssc", floorKgH=None, rateFloorKgH=None), {},
         "UNKNOWN_INSTRUMENT"),
        ("episode rate below the EMIT floor", bundle(), {"rateKgH": 500}, "FLOOR_MISMATCH"),
        ("bundle understates the EMIT floor (100)", bundle(floorKgH=100, rateFloorKgH=100), {},
         "BUNDLE_POLICY_MISMATCH"),
        ("bundle narrows the MARS window to 1 day", bundle(marsNearby=dict(base["marsNearby"], windowDays=1)), {},
         "BUNDLE_POLICY_MISMATCH"),
        ("negative per-source count hiding a plume", bundle(perSourceQueries=[dict(psq[0]), dict(
            base["perSourceQueries"][1], plumeCount=-1)]), {}, "SCHEMA"),
        ("scene in the future", bundle(), {"now": t0 - 1}, "FUTURE"),
        ("extra field in the bundle", bundle(note="trust me"), {}, "SCHEMA"),
        ("sceneId of another scene", bundle(sceneId="0x" + "11" * 32), {}, "SCHEMA"),
        ("relapse scene offered as clean (real 2026-02-01 Tanager)", bundle("2026-02-01 tan"), {}, "CLOUD_FLAG"),
    ]
    for label, b, ctx, code in v:
        add(label, "validation", code, refused(b, **ctx))

    # Tamper C (spec-v2 §8.4): cloud fields edited after signing -> sceneHash changes -> both signatures fail.
    ctx = cp.claim_context(doc)
    s = cp.sign(base, org="OBS_A", chain_id=31337, pool=pool, ctx=ctx, nonce=1, expiry=ctx["now"] + 3600)
    t = copy.deepcopy(s)
    t["bundle"].update(cloudCoverPct=80, notCloudy=False)
    res = cp.verify(t, chain_id=31337, pool=pool)
    add("cloud fields edited after signing", "hash", "both signatures fail",
        "both signatures fail" if res == {"ecdsa": False, "mldsa": False} else str(res))
    other = attest.pool_recover("CleanPass", s["message"], bytes.fromhex(s["signature"][2:]), 84532, pool)
    add("clean-pass signature replayed on another chain", "hash", "signer mismatch",
        "signer mismatch" if other != s["signer"] else "verified")

    # [SIM] the seeded bad pass (spec-v2 §8.9 f): observers sign notCloudy = true on 2025-11-11, whose flag is false.
    # The signature verifies; the content differs from the committed provider record.
    lie = bundle("2025-11-11 tan", notCloudy=True, cloudCoverPct=0, cloudCoverPctAssessed=0)
    key, addr, _ = cp._keys("OBS_A", 31337, None)
    lmsg = cp.message(lie, nonce=2, expiry=ctx["now"] + 3600)
    verifies = attest.pool_recover("CleanPass", lmsg, attest.pool_sign("CleanPass", lmsg, key, 31337, pool), 31337,
                                   pool) == addr
    # Committed inputs only (no raw cache): the honest bundle rebuilt from the committed scene record says
    # notCloudy = false, so the signed content and sceneHash differ. Same result in every checkout.
    r = cp.check_against_committed(doc, cp.find_scene(doc, "2025-11-11 tan"), lie)
    if not verifies:
        got = "signature did not verify"
    else:
        got = ("content differs (notCloudy)" if r["equal"] is False and "bundle.notCloudy" in r["differences"]
               and r["sceneHashSigned"] == "0x" + lmsg["sceneHash"].hex() and not r["sceneHashEqual"] else "missed")
    add("[SIM] observers sign notCloudy = true on a cloudy scene (2025-11-11)", "re-check",
        "content differs (notCloudy)", got,
        note="the signature verifies; re-building the bundle from the committed provider record shows notCloudy = "
             "false and a different sceneHash (challenge reason 1, CLOUD_FLAG)")

    try:
        cp.sign_corroboration(cp.build_corroboration(doc, claim_id=claim, window_from=doc["since"] - 30 * 86400),
                              org="OBS_C", chain_id=31337, pool=pool, nonce=3, now=ctx["now"],
                              expiry=ctx["now"] + 3600)
        got = None
    except cp.CleanPassRefused as e:
        got = e.code
    add("MARS corroboration over a window with a MARS plume (2024-10-12)", "validation", "MARS_PLUME_IN_WINDOW", got)
    return out


def run() -> dict:
    cases = []

    def add(label, layer, expected, got, note=""):
        cases.append({"subject": "detection", "case": label, "layer": layer, "expected": expected, "got": got,
                      "caught": got == expected, **({"note": note} if note else {})})

    for label, edits, code in BAD:
        add(label, "validation", code, refused(bundle(**edits)))
    b = bundle()
    b["sourceId"] = "0x" + "11" * 32
    add("facility not in the covenant's set", "validation", "NOT_COVERED", refused(b))
    b = bundle()
    b["note"] = "trust me"
    add("extra field in the bundle", "validation", "SCHEMA", refused(b))
    add("plume attested twice by one observer", "validation", "DUPLICATE", refused(bundle(), seen={PLUME}))

    # Edited after signing: valid-looking bundle, but the signatures were made over another hash.
    obs = Observer("OBS_A", 31337, CONTRACT)
    signed = obs.attest(CASE, PLUME, covenant_id=COV, covenant_start=START, now=NOW)
    sig = bytes.fromhex(signed["signature"][2:])
    tampered = dict(signed["attestation"], emissionKgH=548)
    tb = dict(signed["bundle"], emissionKgH=548)
    tampered["evidenceHash"] = attest.b32(bundle_hash(tb))
    recovered = attest.recover(tampered, sig, 31337, CONTRACT)
    add("bundle edited after signing (2,548 -> 548)", "hash", "signer mismatch",
        "signer mismatch" if recovered != signed["signer"] else "verified")
    add("signature replayed on another chain", "hash", "signer mismatch",
        "signer mismatch" if attest.recover(signed["attestation"], sig, 84532, CONTRACT) != signed["signer"]
        else "verified")

    # Slow drift: -3 %/pass over 6 passes, every step physically plausible.
    honest = bundle()
    committed = bundle_hash(honest)
    steps = []
    for k in range(1, DRIFT_PASSES + 1):
        d = copy.deepcopy(honest)
        d["emissionKgH"] = round(honest["emissionKgH"] * DRIFT_STEP ** k)
        steps.append({"pass": k, "emissionKgH": d["emissionKgH"], "validation": refused(d) or "passes",
                      "hashMatchesRefetch": bundle_hash(d) == committed})
    all_pass_validation = all(s["validation"] == "passes" for s in steps)
    all_caught_by_hash = not any(s["hashMatchesRefetch"] for s in steps)
    add(f"slow drift ({DRIFT_PASSES} passes, x{DRIFT_STEP}/pass)", "hash", "hash mismatch on re-fetch",
        "hash mismatch on re-fetch" if all_caught_by_hash else "missed",
        note=("validation passes every step (not caught by statistics); caught because a re-fetch from the provider "
              "rebuilds a bundle whose hash differs" if all_pass_validation else "validation also refused a step"))

    cases += cleanpass_cases()
    caught = sum(c["caught"] for c in cases)

    def tally(key):
        return {v: {"n": sum(c[key] == v for c in cases), "caught": sum(c["caught"] for c in cases if c[key] == v)}
                for v in dict.fromkeys(c[key] for c in cases)}

    return {"cases": cases, "slowDrift": steps, "n": len(cases), "caught": caught,
            "detection_rate": round(caught / len(cases), 3), "by_layer": tally("layer"),
            "by_subject": tally("subject"),
            "scope": "inputs we wrote; a detection rate over our own adversarial set, not over unknown attacks"}


def main() -> None:
    r = run()
    write_json(OUT, r)
    for c in r["cases"]:
        print(f"   {'ok  ' if c['caught'] else 'MISS'}  [{c['subject'][:10]:10}|{c['layer']:10}] {c['case']}: "
              f"{c['got']}")
    print(f"adversarial: {r['caught']}/{r['n']} caught ({r['detection_rate']:.0%}) -> {OUT.name}")
    if r["caught"] != r["n"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
