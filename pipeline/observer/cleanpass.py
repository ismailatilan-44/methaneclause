"""Clean-pass observer (spec-v2 §2 rules 1-6, §3.1 CleanPass / Corroboration, §4 bundle).

Five steps, same labels as the page and the v1 observer: Fetch -> Validate -> Seal -> Sign -> Re-check.
  Fetch     the scene record and its receipts (scene query, per-source plume queries, scene-plume query, MARS rows) from
            the committed case file `data/derived/cases/<case>.clean_passes.json` (built by clean_passes.py from
            cached keyless Carbon Mapper responses)
  Validate  the qualification rules of `cleanpass_rules.qualify` (one copy of the rules, imported, never forked) plus
            a schema check of the bundle; any failure is a refusal code and nothing is signed
  Seal      the bundle of spec-v2 §4, RFC 8785 (JCS) bytes, SHA-256 = `sceneHash`
  Sign      EIP-712 `CleanPass` (secp256k1, checked on chain) and ML-DSA-44 over the same JCS bytes (context
            "MethaneClause/v1", checked off chain; SHA-256 of its public key is anchored in KeyRegistry)
  Re-check  rebuild the scene record from the raw responses (cache; LIVE=1: Carbon Mapper now, into a temporary
            cache) and compare `sceneHash`
It attests what it received, from whom and when; not what the satellite saw. A clean pass is not a clean site.

Entry point for the demo and the page: `bundle_for(case, scene, covenant_id=..., claim_id=...) -> (bundle, jcs_bytes, sceneHash)`,
offline, from the committed case file only.

    python -m observer.cleanpass --case DZA_S_012 --scene "2025-02-12 emi" --org OBS_A        # five steps + JSON
    python -m observer.cleanpass --case DZA_S_012 --corroborate --org OBS_C                    # MARS Corroboration
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
from pathlib import Path

from eth_account import Account

import clean_passes
import policy as policy_mod
from canon import canonical_bytes
from cleanpass_rules import attestable_from, cloud_bp, qualify
from common import DERIVED, RADIUS_KM
from observer import attest, pq
from observer.bundle import INSTRUMENTS, PROVIDER_CM
from observer.service import observer_key, require_anvil_for_determinism

SCHEMA_VERSION = "methaneclause.cleanpass/1"
PROVIDER_MARS = "unep-mars"
STEPS = ("Fetch", "Validate", "Seal", "Sign", "Re-check")
MAX_EXPIRY_HORIZON = attest.MAX_EXPIRY_HORIZON  # contract: expiry <= now + 1 day
DEFAULT_COVENANT = "0x" + "00" * 31 + "01"  # offline examples only; the demo passes the real ids
DEFAULT_CLAIM = "0x" + "00" * 32
ANVIL_POOL = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"

_H32 = "h32"
# spec-v2 §4 bundle fields + `plumesNearFacility` (our stricter rule 3, see pipeline/observer/README.md)
BUNDLE_FIELDS = {
    "schemaVersion": (str,), "covenantId": _H32, "claimId": _H32, "sourceId": _H32, "sourceName": (str,),
    "providerId": (str,), "sceneProviderId": (str,), "sceneId": _H32, "instrument": (str,),
    "platform": (str, type(None)), "sceneAt": (int,), "publishedAt": (int, type(None)),
    "cloudCoverPct": (int, type(None)), "cloudCoverPctAssessed": (int, type(None)), "notCloudy": (bool, type(None)),
    "bounds": (list,), "originPoints": (list,), "originIntersect": (bool,), "perSourceQueries": (list,),
    "plumesNearFacility": (dict,), "marsNearby": (dict,), "sensitivityMode": (str, type(None)),
    "solarZenithAngle": (int, float, type(None)), "offNadir": (int, float, type(None)),
    "gsd": (int, float, type(None)), "floorKgH": (int, type(None)), "rateFloorKgH": (int, type(None)),
    "sceneRecordSha256": (str,),
}
PER_SOURCE_KEYS = ("cmSource", "url", "params", "responseSha256", "plumeCount")


class CleanPassRefused(Exception):
    def __init__(self, code: str, detail: str = "", refusals: list[str] | None = None):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.refusals = code, detail, refusals or [code]


# ------------------------------------------------------------------------------------------------ data access
def load_case(case: str) -> dict:
    return json.loads((DERIVED / "cases" / f"{case}.clean_passes.json").read_text())


def find_scene(doc: dict, key: str) -> dict:
    """By provider scene id, scene name, or "YYYY-MM-DD <instrument>"."""
    for s in doc["scenes"]:
        if key in (s["sceneProviderId"], s["sceneName"], f"{s['sceneAtIso'][:10]} {s['instrument']}"):
            return s
    raise LookupError(f"{key!r} is not a candidate scene of {doc['case']}")


def case_policy(doc: dict) -> dict:
    return getattr(policy_mod, doc["covenant"]["policy"])


def scene_id(provider_scene_id: str) -> str:
    return clean_passes.keccak_text(provider_scene_id)


def first_plume_at(doc: dict) -> int:
    fp = doc["summary"]["firstPlumeAfterSince"]
    return clean_passes.ts(fp["sceneAtIso"]) if fp else 1 << 62


# ------------------------------------------------------------------------------------------------ bundle (Seal)
def build_bundle(doc: dict, rec: dict, *, covenant_id: str, claim_id: str) -> dict:
    """spec-v2 §4 bundle for one candidate scene. Deterministic: k observers reading the same records agree byte for
    byte. Origin points are those whose plume was published by the pass's first attestation time (no look-ahead)."""
    pool = case_policy(doc)["pool"]
    t_att = attestable_from(rec["sceneAt"], rec["publishedAt"], pool["publicationLag"])
    known = [pt for pt, pub in zip(doc["originPoints"], doc["originPointPublishedAt"])
             if clean_passes.ts(pub) <= t_att]
    floor = pool["instrumentFloorKgH"].get(rec["instrument"])
    return {
        "schemaVersion": SCHEMA_VERSION, "covenantId": covenant_id, "claimId": claim_id,
        "sourceId": doc["sourceId"], "sourceName": doc["case"], "providerId": PROVIDER_CM,
        "sceneProviderId": rec["sceneProviderId"], "sceneId": rec["sceneId"], "instrument": rec["instrument"],
        "platform": rec["platform"], "sceneAt": rec["sceneAt"], "publishedAt": rec["publishedAt"],
        "cloudCoverPct": rec["cloudCoverPct"], "cloudCoverPctAssessed": rec["cloudCoverPctAssessed"],
        "notCloudy": rec["notCloudy"], "bounds": rec["bounds"], "originPoints": known,
        "originIntersect": bool(rec["originIntersect"]),
        "perSourceQueries": [{k: q[k] for k in PER_SOURCE_KEYS} for q in rec["perSourceQueries"]],
        "plumesNearFacility": {"radiusKm": RADIUS_KM, "count": len(rec["plumesNearFacility"]),
                               "plumeIds": sorted(p["plumeId"] for p in rec["plumesNearFacility"]),
                               "query": rec["scenePlumesQuery"]},
        "marsNearby": rec["marsNearby"],
        "sensitivityMode": rec["sensitivityMode"], "solarZenithAngle": rec["solarZenithAngle"],
        "offNadir": rec["offNadir"], "gsd": rec["gsd"], "floorKgH": floor, "rateFloorKgH": floor,
        "sceneRecordSha256": rec["sceneRecordSha256"],
    }


def bundle_bytes(b: dict) -> bytes:
    return canonical_bytes(b)


def bundle_hash(b: dict) -> str:
    return "0x" + hashlib.sha256(bundle_bytes(b)).hexdigest()


def bundle_for(case: str, scene: str, *, covenant_id: str, claim_id: str) -> tuple[dict, bytes, str]:
    """Entry point for the demo and the page: the real observer bundle for a committed candidate scene, its JCS bytes and sceneHash."""
    doc = load_case(case)
    b = build_bundle(doc, find_scene(doc, scene), covenant_id=covenant_id, claim_id=claim_id)
    jcs = bundle_bytes(b)
    return b, jcs, "0x" + hashlib.sha256(jcs).hexdigest()


# ------------------------------------------------------------------------------------------------ Validate
def claim_context(doc: dict, *, now: int | None = None, seen=()) -> dict:
    """What a clean pass is checked against, from the case file (the demo passes the chain's `now`)."""
    pool = case_policy(doc)["pool"]
    return {"since": doc["since"], "now": doc["evaluatedAt"] if now is None else now,
            "publicationLag": pool["publicationLag"], "covenantEnd": doc["covenant"]["end"],
            "floors": pool["instrumentFloorKgH"], "rateKgH": doc["episode"]["rateKgH"],
            "marsWindowDays": pool["marsWindowDays"], "maxCloudBp": pool["maxCloudBp"], "seen": set(seen)}


def _schema(b: dict) -> None:
    if set(b) != set(BUNDLE_FIELDS):
        raise CleanPassRefused("SCHEMA", f"fields differ: extra {sorted(set(b) - set(BUNDLE_FIELDS))}, "
                                         f"missing {sorted(set(BUNDLE_FIELDS) - set(b))}")
    for k, t in BUNDLE_FIELDS.items():
        v = b[k]
        if t == _H32:
            ok = isinstance(v, str) and v.startswith("0x") and len(v) == 66 and all(c in "0123456789abcdef" for c in v[2:])
        else:
            ok = isinstance(v, t) and not (isinstance(v, bool) and bool not in t)
        if not ok:
            raise CleanPassRefused("SCHEMA", f"{k} has the wrong type")
    if b["schemaVersion"] != SCHEMA_VERSION or b["providerId"] != PROVIDER_CM:
        raise CleanPassRefused("SCHEMA", "unknown schemaVersion / providerId")
    if b["sceneId"] != scene_id(b["sceneProviderId"]):
        raise CleanPassRefused("SCHEMA", "sceneId != keccak256(sceneProviderId)")
    if any(set(q) != set(PER_SOURCE_KEYS) or not isinstance(q["plumeCount"], int) or isinstance(q["plumeCount"], bool)
           or q["plumeCount"] < 0 for q in b["perSourceQueries"]):
        raise CleanPassRefused("SCHEMA", "perSourceQueries entry malformed (keys, or a negative / non-integer count)")
    if not b["perSourceQueries"]:
        raise CleanPassRefused("SCHEMA", "no per-source query (rule 3 cannot be checked)")
    n = b["plumesNearFacility"]
    if set(n) != {"radiusKm", "count", "plumeIds", "query"} or not isinstance(n["count"], int) \
            or n["count"] != len(n["plumeIds"]):
        raise CleanPassRefused("SCHEMA", "plumesNearFacility malformed (count != len(plumeIds))")
    m = b["marsNearby"]
    if set(m) != {"windowDays", "count", "rows"} or m["count"] != len(m["rows"]):
        raise CleanPassRefused("SCHEMA", "marsNearby malformed")
    if b["originIntersect"] and not b["originPoints"]:
        raise CleanPassRefused("SCHEMA", "originIntersect without an origin point")


def validate(b: dict, ctx: dict) -> dict:
    """Schema, then rules 1-6 via cleanpass_rules.qualify. Raises CleanPassRefused(code); returns the check record."""
    _schema(b)
    floor = ctx["floors"].get(b["instrument"])
    if b["floorKgH"] != floor or b["rateFloorKgH"] != floor:
        raise CleanPassRefused("BUNDLE_POLICY_MISMATCH", f"bundle floor {b['floorKgH']} != policy floor {floor}")
    if b["marsNearby"]["windowDays"] != ctx["marsWindowDays"]:
        raise CleanPassRefused("BUNDLE_POLICY_MISMATCH",
                               f"marsNearby.windowDays {b['marsNearby']['windowDays']} != policy {ctx['marsWindowDays']}")
    scene = {"sceneProviderId": b["sceneProviderId"], "sceneAt": b["sceneAt"], "publishedAt": b["publishedAt"],
             "instrument": b["instrument"], "cloudCoverPct": b["cloudCoverPct"],
             "cloudCoverPctAssessed": b["cloudCoverPctAssessed"], "notCloudy": b["notCloudy"],
             "originIntersect": b["originIntersect"], "perSourceQueries": b["perSourceQueries"],
             "plumesNearFacility": b["plumesNearFacility"]["count"],
             "marsRows": [{"t": clean_passes.ts(r["tile_date"])} for r in b["marsNearby"]["rows"]]}
    q = qualify(scene, ctx)
    if not q["qualified"]:
        raise CleanPassRefused(q["refusal"], q["checks"][next(k for k, c in q["checks"].items()
                                                              if c["code"] == q["refusal"])]["rule"], q["refusals"])
    return q


# ------------------------------------------------------------------------------------------------ Sign
def message(b: dict, *, nonce: int, expiry: int) -> dict:
    """EIP-712 CleanPass message (spec-v2 §3.1 order), bytes32 fields as bytes."""
    cbp = cloud_bp(b["cloudCoverPct"], b["cloudCoverPctAssessed"])
    return {"claimId": attest.b32(b["claimId"]), "sourceId": attest.b32(b["sourceId"]),
            "sceneId": attest.b32(b["sceneId"]), "sceneHash": attest.b32(bundle_hash(b)),
            "sceneAt": b["sceneAt"], "publishedAt": b["publishedAt"], "instrumentId": INSTRUMENTS[b["instrument"]],
            "providerId": attest.b32(PROVIDER_CM), "cloudBp": cbp, "notCloudy": b["notCloudy"],
            "floorKgH": b["floorKgH"], "rateFloorKgH": b["rateFloorKgH"], "kind": attest.KIND_CLEAN,
            "nonce": nonce, "expiry": expiry}


def _keys(org: str, chain_id: int, pk: str | None):
    key = pk or observer_key(org, chain_id)
    seed = pq.seed_for(org, chain_id) if org in pq.ORGS else None
    return key, Account.from_key(key).address, (pq.keypair(seed) if seed else None)


def _expiry(now: int, expiry: int | None) -> int:
    e = now + 3600 if expiry is None else expiry
    if not now <= e <= now + MAX_EXPIRY_HORIZON:
        raise ValueError(f"expiry must be in [now, now + {MAX_EXPIRY_HORIZON}] (contract rule)")
    return e


def sign(b: dict, *, org: str, chain_id: int, pool: str, ctx: dict, nonce: int | None = None,
         expiry: int | None = None, pk: str | None = None, pq_deterministic: bool = False) -> dict:
    """Validate, then sign: EIP-712 CleanPass (ECDSA) + ML-DSA-44 over the JCS bundle. Refused scenes raise.
    `nonce` None = random 64-bit; `pq_deterministic` = FIPS 204 deterministic ML-DSA (demo repeatability only)."""
    require_anvil_for_determinism(chain_id, nonce, pq_deterministic)
    validate(b, ctx)
    msg = message(b, nonce=secrets.randbits(64) if nonce is None else nonce, expiry=_expiry(ctx["now"], expiry))
    key, addr, pqk = _keys(org, chain_id, pk)
    sig = attest.pool_sign("CleanPass", msg, key, chain_id, pool)
    out = {"org": org, "signer": addr, "bundle": b, "sceneHash": bundle_hash(b), "message": msg,
           "digest": "0x" + attest.pool_digest("CleanPass", msg, chain_id, pool).hex(), "signature": "0x" + sig.hex()}
    if pqk:
        out["mldsa44"] = {"publicKey": "0x" + pqk[0].hex(), "pkHash": pq.pk_hash(pqk[0]), "context": pq.CONTEXT.decode(),
                          "sig": "0x" + pq.sign(pqk[1], bundle_bytes(b), deterministic=pq_deterministic).hex()}
    return out


def verify(signed: dict, *, chain_id: int, pool: str) -> dict:
    """Both halves against the bundle as it is now: a bundle edited after signing fails both."""
    b = signed["bundle"]
    msg = dict(signed["message"], sceneHash=attest.b32(bundle_hash(b)))
    ecdsa = attest.pool_recover("CleanPass", msg, bytes.fromhex(signed["signature"][2:]), chain_id, pool) == \
        signed["signer"]
    m = signed.get("mldsa44")
    mldsa = None if m is None else pq.verify(bytes.fromhex(m["publicKey"][2:]), bundle_bytes(b),
                                             bytes.fromhex(m["sig"][2:]))
    return {"ecdsa": ecdsa, "mldsa": mldsa}


# ------------------------------------------------------------------------------------------------ Re-check
def _rebuild(case: str, live: bool) -> dict:
    """clean_passes.build from the raw-response cache (OFFLINE, no network) or, live, into a fresh temporary cache."""
    import tempfile

    keep = {k: os.environ.get(k) for k in ("OFFLINE", "CM_CASES_CACHE")}
    tmp = tempfile.TemporaryDirectory() if live else None
    try:
        if live:
            os.environ.pop("OFFLINE", None)
            os.environ["CM_CASES_CACHE"] = tmp.name
        else:
            os.environ["OFFLINE"] = "1"
        return clean_passes.build(case)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if tmp:
            tmp.cleanup()


PROVENANCE = "provider response hashes (perSourceQueries[].responseSha256, plumesNearFacility.query, sceneRecordSha256)"


def scene_view(b: dict, rec: dict) -> dict:
    """What Re-check compares (review finding 1): every rule-relevant field of the bundle, plus the plume ids
    and quantities that fall on this scene at the facility (per linked source, and within the facility radius) — but
    not the provenance hashes of whole provider responses, which change whenever Carbon Mapper adds a plume anywhere
    at the source or on the scene. Those stay in the bundle as provenance."""
    v = json.loads(json.dumps(b))
    for q in v["perSourceQueries"]:
        q.pop("responseSha256")
    v["plumesNearFacility"].pop("query")
    v.pop("sceneRecordSha256")
    on_scene = {q["cmSource"]: sorted((p["plumeId"], p["emissionKgH"], p["uncertaintyKgH"])
                                      for p in q.get("plumes", [])) for q in rec["perSourceQueries"]}
    near = sorted((p["plumeId"], p.get("emissionKgH")) for p in rec["plumesNearFacility"])
    return {"bundle": v, "plumesOnScene": on_scene, "plumesNearFacility": near}


def _differences(a: dict, b: dict) -> list[str]:
    out = [f"bundle.{k}" for k in sorted(set(a["bundle"]) | set(b["bundle"])) if a["bundle"].get(k) != b["bundle"].get(k)]
    return out + [k for k in ("plumesOnScene", "plumesNearFacility") if a[k] != b[k]]


def check_against_committed(doc: dict, rec: dict, bundle: dict) -> dict:
    """Committed-inputs re-check (no raw cache, no network): rebuild the honest bundle from the committed scene record
    (`data/derived/cases/<case>.clean_passes.json`, whose `sceneRecordSha256` pins the provider record) and compare it
    with a signed bundle, on `scene_view` and on `sceneHash`. This is what tests, the adversarial set and `make check`
    use; `recheck` (raw cache or LIVE=1) is the optional deeper step."""
    honest = build_bundle(doc, rec, covenant_id=bundle["covenantId"], claim_id=bundle["claimId"])
    a, b = scene_view(bundle, rec), scene_view(honest, rec)
    return {"mode": "committed", "equal": a == b, "differences": _differences(a, b),
            "sceneHashSigned": bundle_hash(bundle), "sceneHashCommitted": bundle_hash(honest),
            "sceneHashEqual": bundle_hash(bundle) == bundle_hash(honest)}


def recheck(doc: dict, rec: dict, *, covenant_id: str, claim_id: str, live: bool = False,
            bundle: dict | None = None) -> dict:
    """Rebuild this scene's record from the provider responses and decide EQUAL / DIFFERS on `scene_view` (the
    scene-relevant content; review finding 1). `bundle` = the signed bundle to check (default: the bundle
    built from the committed record). Default source: the cached raw responses (no network); live=True: keyless
    Carbon Mapper now. A cache whose bytes do not match their recorded hash is reported as DIFFERS."""
    from scenes import OfflineMiss

    signed = bundle if bundle is not None else build_bundle(doc, rec, covenant_id=covenant_id, claim_id=claim_id)
    mode = "live" if live else "cache"
    base = {"mode": mode, "sceneHashCommitted": bundle_hash(signed)}
    try:
        fresh = _rebuild(doc["case"], live)
    except OfflineMiss:  # the default re-check reads the raw cache only (_rebuild sets OFFLINE=1 itself)
        return {**base, "mode": "skipped", "equal": None,
                "why": "this checkout holds no raw Carbon Mapper responses to rebuild the scene from (a fresh clone "
                       "has none; step 2 checked the committed case file); LIVE=1 fetches Carbon Mapper now, "
                       "METHANECLAUSE_RAW=<dir> reads a cache outside this checkout"}
    except ValueError as e:
        return {**base, "equal": False, "why": f"raw-response cache corrupted: {e}", "differences": ["cache"]}
    try:
        rrec = find_scene(fresh, rec["sceneProviderId"])
    except LookupError:
        return {**base, "equal": False, "why": "scene no longer returned by the provider", "differences": ["scene"]}
    rebuilt = build_bundle(fresh, rrec, covenant_id=signed["covenantId"], claim_id=signed["claimId"])
    a, b = scene_view(signed, rec), scene_view(rebuilt, rrec)
    return {**base, "equal": a == b, "differences": _differences(a, b), "sceneHashRebuilt": bundle_hash(rebuilt),
            "sceneHashEqual": bundle_hash(rebuilt) == bundle_hash(signed), "provenanceNotCompared": PROVENANCE}


# ------------------------------------------------------------------------------------------------ the observer
class CleanPassObserver:
    def __init__(self, org: str, chain_id: int, pool: str, pk: str | None = None, live: bool = False):
        self.org, self.chain_id, self.pool, self._pk, self.live = org, chain_id, pool, pk, live
        self.seen: set[str] = set()

    @staticmethod
    def _say(i: int, text: str) -> None:
        print(f"[{i}/5 {STEPS[i - 1]}] {text}")

    def attest(self, case: str, scene: str, *, covenant_id: str, claim_id: str, now: int | None = None,
               nonce: int | None = None, expiry: int | None = None, pq_deterministic: bool = False) -> dict:
        """Five steps. Returns the signed pass only if Re-check is EQUAL (or skipped for want of cached responses);
        DIFFERS raises RECHECK_DIFFERS and no signature leaves the observer (review finding 3)."""
        require_anvil_for_determinism(self.chain_id, nonce, pq_deterministic)
        doc = load_case(case)
        rec = find_scene(doc, scene)
        q = doc["sceneQuery"]
        self._say(1, f"{case} scene {rec['sceneName']} ({rec['instrument']}, {rec['sceneAtIso']}) from the committed "
                     f"case file; scene query {q['url']} contentSha256 {q['responseSha256'][:16]}… (fetched "
                     f"{q['fetchedAt']}); {len(rec['perSourceQueries'])} per-source plume queries; "
                     f"{rec['marsNearby']['count']} MARS rows within {rec['marsNearby']['windowDays']} d")
        b = build_bundle(doc, rec, covenant_id=covenant_id, claim_id=claim_id)
        ctx = claim_context(doc, now=now, seen=self.seen)
        try:
            checks = validate(b, ctx)["checks"]
        except CleanPassRefused as e:
            self._say(2, f"REFUSED {e.code} ({e.detail}); all failing: {', '.join(e.refusals)} — nothing signed")
            raise
        self._say(2, f"{len(checks)} checks pass (spec-v2 §2 rules 1-6): "
                     + ", ".join(f"{c['code']} ok" for c in checks.values()))
        jcs = bundle_bytes(b)
        self._say(3, f"bundle {SCHEMA_VERSION}, {len(jcs)} B JCS -> sceneHash {bundle_hash(b)}")
        s = sign(b, org=self.org, chain_id=self.chain_id, pool=self.pool, ctx=ctx, nonce=nonce, expiry=expiry,
                 pk=self._pk, pq_deterministic=pq_deterministic)
        pq_line = (f"; ML-DSA-44 (ctx {s['mldsa44']['context']}) {len(s['mldsa44']['sig']) // 2 - 1} B, "
                   f"pk hash {s['mldsa44']['pkHash'][:18]}…") if "mldsa44" in s else "; no ML-DSA key (algId 1)"
        self._say(4, f"EIP-712 CleanPass digest {s['digest'][:18]}… signed by {s['signer']} ({self.org}){pq_line}")
        r = recheck(doc, rec, covenant_id=covenant_id, claim_id=claim_id, live=self.live)
        src = {"cache": "cached raw responses", "live": "Carbon Mapper now (keyless)"}.get(r["mode"])
        if r["equal"] is False:
            self._say(5, f"rebuilt from {src}: scene content DIFFERS ({', '.join(r['differences'])}"
                         f"{'; ' + r['why'] if r.get('why') else ''}) — signature withheld")
            raise CleanPassRefused("RECHECK_DIFFERS", ", ".join(r["differences"]))
        self._say(5, f"rebuilt from {src}: scene content equal (sceneHash "
                     f"{'equal' if r['sceneHashEqual'] else 'differs in provenance hashes only'})" if src
                  else f"skipped: {r['why']}")
        self.seen.add(rec["sceneProviderId"])
        return {**s, "recheck": r}


# ------------------------------------------------------------------------------------------------ Corroboration
def build_corroboration(doc: dict, *, claim_id: str, window_from: int | None = None,
                        window_to: int | None = None) -> dict:
    """spec-v2 §3 kind 7: UNEP MARS (other satellites) against the negative, over [since, latest counted sceneAt].
    MARS rows are the plumes MARS attributes to this facility (its own source), from the pinned snapshot."""
    counted = [s["sceneAt"] for s in doc["scenes"] if s["qualified"] and s["sceneAt"] < first_plume_at(doc)]
    if window_to is None:
        if not counted:
            raise CleanPassRefused("NO_COUNTED_PASS", f"{doc['case']}: no counted clean pass to corroborate")
        window_to = max(counted)
    window_from = doc["since"] if window_from is None else window_from
    case = json.loads((DERIVED / "cases" / f"{doc['case']}.json").read_text())
    rows = [r for r in case["mars_plumes"] if window_from <= clean_passes.ts(r["tile_date"]) <= window_to]
    return {"claimId": claim_id, "sourceId": doc["sourceId"], "providerId": PROVIDER_MARS, "claimSince": doc["since"],
            "marsSnapshotHash": "0x" + case["mars_snapshot_sha256"], "windowFrom": window_from,
            "windowTo": window_to, "marsPlumesInWindow": len(rows),
            "rows": [{k: r[k] for k in ("id_plume", "tile_date", "satellite", "ch4_fluxrate")} for r in rows]}


def sign_corroboration(c: dict, *, org: str, chain_id: int, pool: str, now: int, expiry: int,
                       nonce: int | None = None, pk: str | None = None) -> dict:
    """The chain's checks first (1-day expiry horizon, windowFrom <= since <= ..., windowFrom <= windowTo), then no
    MARS plume in the window. A pinned nonce is Anvil only."""
    require_anvil_for_determinism(chain_id, nonce, False)
    _expiry(now, expiry)
    if not c["windowFrom"] <= c["claimSince"] or c["windowFrom"] > c["windowTo"]:
        raise ValueError("Corroboration window must satisfy windowFrom <= since and windowFrom <= windowTo")
    if c["marsPlumesInWindow"] != 0:
        raise CleanPassRefused("MARS_PLUME_IN_WINDOW", f"{c['marsPlumesInWindow']} MARS plume(s) in the window")
    msg = {"claimId": attest.b32(c["claimId"]), "sourceId": attest.b32(c["sourceId"]),
           "providerId": attest.b32(PROVIDER_MARS), "marsSnapshotHash": attest.b32(c["marsSnapshotHash"]),
           "windowFrom": c["windowFrom"], "windowTo": c["windowTo"], "marsPlumesInWindow": 0,
           "kind": attest.KIND_CORROBORATE, "nonce": secrets.randbits(64) if nonce is None else nonce,
           "expiry": expiry}
    key, addr, _ = _keys(org, chain_id, pk)
    sig = attest.pool_sign("Corroboration", msg, key, chain_id, pool)
    return {"org": org, "signer": addr, "corroboration": c, "message": msg, "signature": "0x" + sig.hex(),
            "digest": "0x" + attest.pool_digest("Corroboration", msg, chain_id, pool).hex()}


def to_json(o) -> str:
    def enc(v):
        return "0x" + v.hex() if isinstance(v, bytes) else v
    return json.dumps(o, default=enc, indent=1, sort_keys=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", default="DZA_S_012")
    ap.add_argument("--scene", help='"YYYY-MM-DD <instrument>", scene name or provider id')
    ap.add_argument("--org", default="OBS_A", choices=["OBS_A", "OBS_B", "OBS_C"])
    ap.add_argument("--covenant", default=DEFAULT_COVENANT)
    ap.add_argument("--claim", default=DEFAULT_CLAIM)
    ap.add_argument("--pool", default=ANVIL_POOL)
    ap.add_argument("--chain-id", type=int, default=31337)
    ap.add_argument("--corroborate", action="store_true")
    a = ap.parse_args()
    if a.corroborate:
        doc = load_case(a.case)
        c = build_corroboration(doc, claim_id=a.claim)
        print(to_json(sign_corroboration(c, org=a.org, chain_id=a.chain_id, pool=a.pool, now=doc["evaluatedAt"],
                                         expiry=doc["evaluatedAt"] + 3600)))
        return
    if not a.scene:
        raise SystemExit("--scene is required (or --corroborate)")
    try:
        s = CleanPassObserver(a.org, a.chain_id, a.pool, live=os.environ.get("LIVE") == "1").attest(a.case, a.scene, covenant_id=a.covenant,
                                                                claim_id=a.claim)
    except CleanPassRefused as e:
        raise SystemExit(f"refused: {e.code}")
    print(to_json(s))


if __name__ == "__main__":
    main()
