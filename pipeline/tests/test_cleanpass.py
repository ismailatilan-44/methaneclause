"""Clean-pass observer (spec-v2 §2 rules 1-6, §4 bundle, §3.1 types): bundle, JCS hash, refusals, signatures,
corroboration, re-check, five steps. Real DZA / TKM scenes from the committed case files; no network."""
import copy
import hashlib

import pytest

from canon import canonical_bytes
from observer import attest, cleanpass as cp
from observer import pq

COV = "0x" + "00" * 31 + "07"
CLAIM = "0x" + "ab" * 32
POOL = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"
QUALIFIED = "2025-02-12 emi"  # DZA: counted pass
INSIDE_LINK_WINDOW = "2024-10-26 tan"  # DZA: before since
LOOK_AHEAD = "2025-07-08 tan"  # DZA: only the relapse plume's origin, published later
RELAPSE = "2026-02-01 tan"  # DZA: cloudy + plume


@pytest.fixture(scope="module")
def dza():
    return cp.load_case("DZA_S_012")


def bundle(dza, scene=QUALIFIED):
    return cp.build_bundle(dza, cp.find_scene(dza, scene), covenant_id=COV, claim_id=CLAIM)


def test_bundle_has_the_spec_fields(dza):
    b = bundle(dza)
    assert set(b) == set(cp.BUNDLE_FIELDS)
    for k in ("schemaVersion", "covenantId", "claimId", "sourceId", "sourceName", "providerId", "sceneProviderId",
              "sceneId", "instrument", "platform", "sceneAt", "publishedAt", "cloudCoverPct", "cloudCoverPctAssessed",
              "notCloudy", "bounds", "originPoints", "originIntersect", "perSourceQueries", "marsNearby",
              "sensitivityMode", "solarZenithAngle", "offNadir", "gsd", "floorKgH", "rateFloorKgH",
              "sceneRecordSha256"):
        assert k in b  # spec-v2 §4 list
    assert b["schemaVersion"] == "methaneclause.cleanpass/1" and b["providerId"] == "carbonmapper"
    assert b["sceneId"] == cp.scene_id(b["sceneProviderId"])
    assert b["floorKgH"] == b["rateFloorKgH"] == 700  # EMIT floor, POLICY_V2


def test_scene_hash_is_sha256_of_jcs(dza):
    b = bundle(dza)
    assert cp.bundle_hash(b) == "0x" + hashlib.sha256(canonical_bytes(b)).hexdigest()
    assert cp.bundle_hash(bundle(dza)) == cp.bundle_hash(b)  # deterministic: k observers agree


def test_origin_points_are_those_published_by_attestation(dza):
    b = bundle(dza, LOOK_AHEAD)
    assert b["originIntersect"] is False
    assert [5.999045, 31.772806] not in b["originPoints"]  # the relapse plume's origin is not yet published
    assert bundle(dza)["originIntersect"] is True


def test_validate_qualified_and_real_refusals(dza):
    ctx = cp.claim_context(dza)
    assert cp.validate(bundle(dza), ctx)["qualified"]
    for scene, code in ((INSIDE_LINK_WINDOW, "SCENE_BEFORE_SINCE"), (LOOK_AHEAD, "NO_ORIGIN_INTERSECT"),
                        (RELAPSE, "CLOUD_FLAG")):
        with pytest.raises(cp.CleanPassRefused) as e:
            cp.validate(bundle(dza, scene), ctx)
        assert e.value.code == code


@pytest.mark.parametrize("edit", ["extra", "missing", "sceneId", "provider", "type"])
def test_malformed_bundle_is_refused_schema(dza, edit):
    b = bundle(dza)
    if edit == "extra":
        b["note"] = "trust me"
    elif edit == "missing":
        del b["bounds"]
    elif edit == "sceneId":
        b["sceneId"] = "0x" + "11" * 32
    elif edit == "provider":
        b["providerId"] = "someone-else"
    else:
        b["sceneAt"] = str(b["sceneAt"])
    with pytest.raises(cp.CleanPassRefused) as e:
        cp.validate(b, cp.claim_context(dza))
    assert e.value.code == "SCHEMA"


def test_sign_and_verify_both_halves(dza):
    s = cp.sign(bundle(dza), org="OBS_A", chain_id=31337, pool=POOL, ctx=cp.claim_context(dza), nonce=1,
                expiry=cp.claim_context(dza)["now"] + 3600)
    m = s["message"]
    assert [k for k in m] == attest.CLEANPASS_ORDER and m["kind"] == attest.KIND_CLEAN
    assert "0x" + m["sceneHash"].hex() == s["sceneHash"] == cp.bundle_hash(s["bundle"])
    assert attest.pool_digest("CleanPass", m, 31337, POOL).hex() == s["digest"][2:]
    v = cp.verify(s, chain_id=31337, pool=POOL)
    assert v == {"ecdsa": True, "mldsa": True}
    assert attest.pool_recover("CleanPass", m, bytes.fromhex(s["signature"][2:]), 31337, POOL) == s["signer"]


def test_editing_cloud_fields_after_signing_breaks_both_signatures(dza):
    """Tamper C (spec-v2 §8.4): edit a scene's cloud fields -> sceneHash changes -> ECDSA and ML-DSA both fail."""
    ctx = cp.claim_context(dza)
    s = cp.sign(bundle(dza), org="OBS_B", chain_id=31337, pool=POOL, ctx=ctx, nonce=2, expiry=ctx["now"] + 3600)
    t = copy.deepcopy(s)
    t["bundle"]["cloudCoverPct"] = 80
    t["bundle"]["notCloudy"] = False
    assert cp.verify(t, chain_id=31337, pool=POOL) == {"ecdsa": False, "mldsa": False}


def test_refused_scene_is_never_signed(dza):
    ctx = cp.claim_context(dza)
    with pytest.raises(cp.CleanPassRefused):
        cp.sign(bundle(dza, RELAPSE), org="OBS_A", chain_id=31337, pool=POOL, ctx=ctx, nonce=3,
                expiry=ctx["now"] + 3600)


def test_expiry_horizon(dza):
    ctx = cp.claim_context(dza)
    with pytest.raises(ValueError):
        cp.sign(bundle(dza), org="OBS_A", chain_id=31337, pool=POOL, ctx=ctx, nonce=4, expiry=ctx["now"] + 2 * 86400)


def test_corroboration_dza_window_clean_and_signed(dza):
    c = cp.build_corroboration(dza, claim_id=CLAIM)
    assert c["marsPlumesInWindow"] == 0 and c["windowFrom"] == dza["since"]
    assert c["windowTo"] == max(s["sceneAt"] for s in dza["scenes"]
                                if s["qualified"] and s["sceneAt"] < cp.first_plume_at(dza))
    s = cp.sign_corroboration(c, org="OBS_C", chain_id=31337, pool=POOL, nonce=5, now=dza["evaluatedAt"],
                              expiry=dza["evaluatedAt"] + 3600)
    m = s["message"]
    assert [k for k in m] == attest.CORROBORATION_ORDER and m["kind"] == attest.KIND_CORROBORATE
    assert m["providerId"] == attest.b32("unep-mars")
    assert attest.pool_recover("Corroboration", m, bytes.fromhex(s["signature"][2:]), 31337, POOL) == s["signer"]


def test_corroboration_refused_when_mars_saw_a_plume(dza):
    c = cp.build_corroboration(dza, claim_id=CLAIM, window_from=dza["since"] - 30 * 86400)  # includes 2024-10-12
    assert c["marsPlumesInWindow"] > 0
    with pytest.raises(cp.CleanPassRefused) as e:
        cp.sign_corroboration(c, org="OBS_C", chain_id=31337, pool=POOL, nonce=6, now=dza["evaluatedAt"],
                              expiry=dza["evaluatedAt"] + 3600)
    assert e.value.code == "MARS_PLUME_IN_WINDOW"


def test_corroboration_needs_a_counted_pass():
    tkm = cp.load_case("TKM_S_276")
    with pytest.raises(cp.CleanPassRefused) as e:
        cp.build_corroboration(tkm, claim_id=CLAIM)
    assert e.value.code == "NO_COUNTED_PASS"


def test_recheck_rebuilds_the_same_bundle(dza):
    r = cp.recheck(dza, cp.find_scene(dza, QUALIFIED), covenant_id=COV, claim_id=CLAIM)
    if r["mode"] == "skipped":
        pytest.skip(r["why"])
    assert r["equal"] is True and r["differences"] == [] and r["sceneHashCommitted"] == r["sceneHashRebuilt"]


def test_five_steps_printed_in_order(dza, capsys):
    obs = cp.CleanPassObserver("OBS_A", 31337, POOL)
    obs.attest("DZA_S_012", QUALIFIED, covenant_id=COV, claim_id=CLAIM, nonce=7)
    out = capsys.readouterr().out
    pos = [out.index(f"[{i}/5 {label}]") for i, label in enumerate(cp.STEPS, 1)]
    assert pos == sorted(pos) and cp.STEPS == ("Fetch", "Validate", "Seal", "Sign", "Re-check")


def test_pq_context_is_the_v1_context():
    assert pq.CONTEXT == b"MethaneClause/v1"


def test_lane_c_entry_point(dza):
    b, jcs, h = cp.bundle_for("DZA_S_012", QUALIFIED, covenant_id=COV, claim_id=CLAIM)
    assert jcs == canonical_bytes(b) and h == "0x" + hashlib.sha256(jcs).hexdigest()


def test_adversarial_set_covers_every_clean_pass_refusal():
    from cleanpass_rules import REFUSAL_ORDER
    from observer import adversarial

    r = adversarial.run()
    cpc = [c for c in r["cases"] if c["subject"] == "clean pass"]
    assert all(c["caught"] for c in r["cases"]), [c for c in r["cases"] if not c["caught"]]
    assert set(REFUSAL_ORDER) <= {c["expected"] for c in cpc}
    assert {"SCHEMA", "MARS_PLUME_IN_WINDOW"} <= {c["expected"] for c in cpc}
    layers = {c["layer"] for c in cpc}
    assert {"validation", "hash", "re-check"} <= layers
    assert r["by_subject"]["clean pass"]["n"] == len(cpc)
    assert sum(v["n"] for v in r["by_layer"].values()) == r["n"]


# ---------------------------------------------------------------- clean-pass observer review notes (2026-10-16)
def _view(dza, scene=QUALIFIED, bundle_edit=None, rec_edit=None):
    rec = copy.deepcopy(cp.find_scene(dza, scene))
    if rec_edit:
        rec_edit(rec)
    b = cp.build_bundle(dza, rec, covenant_id=COV, claim_id=CLAIM)
    if bundle_edit:
        bundle_edit(b)
    return cp.scene_view(b, rec)


def test_view_ignores_a_plume_elsewhere_at_the_source(dza):
    def elsewhere(rec):
        rec["perSourceQueries"][0]["responseSha256"] = "f" * 64  # the source response changed ...
        rec["scenePlumesQuery"] = dict(rec["scenePlumesQuery"], responseSha256="e" * 64)
        rec["sceneRecordSha256"] = "d" * 64  # ... and the scene-wide record (a plume 50 km away)
    assert _view(dza, rec_edit=elsewhere) == _view(dza)


def test_view_flips_on_a_plume_on_this_scene_at_the_source(dza):
    def on_scene(rec):
        q = rec["perSourceQueries"][0]
        q["plumeCount"], q["plumeIds"] = 1, ["emi20250212t134700p09015-Z"]
        q["plumes"] = [{"plumeId": "emi20250212t134700p09015-Z", "emissionKgH": 900.0, "uncertaintyKgH": 300.0}]
    assert _view(dza, rec_edit=on_scene) != _view(dza)


def test_view_flips_on_a_changed_quantity(dza):
    rec = cp.find_scene(dza, RELAPSE)
    assert any(q.get("plumes") for q in rec["perSourceQueries"])

    def requantify(r):
        for q in r["perSourceQueries"]:
            for p in q["plumes"]:
                p["emissionKgH"] = (p["emissionKgH"] or 0) + 1
    assert _view(dza, RELAPSE, rec_edit=requantify) != _view(dza, RELAPSE)


def _tamper_cache(tmp_path, scene_provider_id, scene_ts, fix_meta):
    import json
    import shutil

    from scenes import CACHE

    if not CACHE.exists():
        pytest.skip("raw responses not cached in this checkout")
    dst = tmp_path / "cache"
    shutil.copytree(CACHE, dst)
    body = dst / "source" / "CH4_1B2_1000m_5.99636_31.77708.json"
    d = json.loads(body.read_text())
    d["plumes"].append({"plume_id": "added-by-test", "scene_id": scene_provider_id, "scene_timestamp": scene_ts,
                        "instrument": "emi", "emission_auto": 900.0, "emission_uncertainty_auto": 300.0})
    body.write_text(json.dumps(d))
    if fix_meta:
        meta_p = body.with_name(body.stem + ".meta.json")
        m = json.loads(meta_p.read_text())
        from common import content_sha256

        m["sha256"] = hashlib.sha256(body.read_bytes()).hexdigest()
        m["contentSha256"] = content_sha256(body.read_bytes())
        meta_p.write_text(json.dumps(m))
    return dst


def test_recheck_tampered_cache_without_meta_fix_is_refused(dza, tmp_path, monkeypatch):
    rec = cp.find_scene(dza, QUALIFIED)
    monkeypatch.setenv("CM_CASES_CACHE", str(_tamper_cache(tmp_path, rec["sceneProviderId"], rec["sceneAtIso"],
                                                           fix_meta=False)))
    r = cp.recheck(dza, rec, covenant_id=COV, claim_id=CLAIM)
    assert r["equal"] is False and "corrupted" in r["why"]


def test_recheck_plume_added_on_the_scene_differs(dza, tmp_path, monkeypatch):
    rec = cp.find_scene(dza, QUALIFIED)
    monkeypatch.setenv("CM_CASES_CACHE", str(_tamper_cache(tmp_path, rec["sceneProviderId"], rec["sceneAtIso"],
                                                           fix_meta=True)))
    r = cp.recheck(dza, rec, covenant_id=COV, claim_id=CLAIM)
    assert r["mode"] == "cache" and r["equal"] is False and "bundle.perSourceQueries" in r["differences"]


def test_recheck_plume_added_elsewhere_at_the_source_stays_equal(dza, tmp_path, monkeypatch):
    rec = cp.find_scene(dza, QUALIFIED)
    monkeypatch.setenv("CM_CASES_CACHE", str(_tamper_cache(tmp_path, "other-scene-uuid", "2026-09-30T10:00:00Z",
                                                           fix_meta=True)))
    r = cp.recheck(dza, rec, covenant_id=COV, claim_id=CLAIM)
    assert r["mode"] == "cache" and r["equal"] is True and r["sceneHashEqual"] is False  # provenance moved only


def test_attest_returns_no_signature_when_recheck_differs(dza, tmp_path, monkeypatch, capsys):
    rec = cp.find_scene(dza, QUALIFIED)
    monkeypatch.setenv("CM_CASES_CACHE", str(_tamper_cache(tmp_path, rec["sceneProviderId"], rec["sceneAtIso"],
                                                           fix_meta=True)))
    with pytest.raises(cp.CleanPassRefused) as e:
        cp.CleanPassObserver("OBS_A", 31337, POOL).attest("DZA_S_012", QUALIFIED, covenant_id=COV, claim_id=CLAIM)
    assert e.value.code == "RECHECK_DIFFERS" and "DIFFERS" in capsys.readouterr().out


@pytest.mark.parametrize("edit, code", [
    (lambda b: b["perSourceQueries"][0].update(plumeCount=-1), "SCHEMA"),
    (lambda b: (b["perSourceQueries"][0].update(plumeCount=1), b["perSourceQueries"][1].update(plumeCount=-1)),
     "SCHEMA"),
    (lambda b: b["plumesNearFacility"].update(count=0, plumeIds=["x"]), "SCHEMA"),
    (lambda b: b["marsNearby"].update(windowDays=1), "BUNDLE_POLICY_MISMATCH"),
    (lambda b: b.update(floorKgH=100, rateFloorKgH=100), "BUNDLE_POLICY_MISMATCH"),
])
def test_validate_is_strict_about_counts_and_policy(dza, edit, code):
    b = bundle(dza)
    edit(b)
    with pytest.raises(cp.CleanPassRefused) as e:
        cp.validate(b, cp.claim_context(dza))
    assert e.value.code == code


def test_each_per_source_query_must_be_zero():
    from cleanpass_rules import qualify

    scene = {"sceneProviderId": "s", "sceneAt": 100, "publishedAt": 200, "instrument": "tan", "cloudCoverPct": 0,
             "cloudCoverPctAssessed": 0, "notCloudy": True, "originIntersect": True, "plumesNearFacility": 0,
             "perSourceQueries": [{"plumeCount": 1}, {"plumeCount": -1}], "marsRows": []}
    ctx = {"since": 0, "now": 10**9, "publicationLag": 0, "covenantEnd": None, "floors": {"tan": 180},
           "rateKgH": 2000, "marsWindowDays": 7, "maxCloudBp": 2500, "seen": set()}
    assert qualify(scene, ctx)["refusal"] == "PLUME_ON_SCENE"


def test_attestation_time_formula_is_shared():
    import clean_passes
    from cleanpass_rules import attestable_from

    assert cp.attestable_from is attestable_from and clean_passes.attestable_from is attestable_from
    assert attestable_from(100, 500, 60) == 500 and attestable_from(100, 120, 60) == 160
    assert attestable_from(100, None, 60) == 160


def test_corroboration_expiry_horizon_and_window(dza):
    c = cp.build_corroboration(dza, claim_id=CLAIM)
    now = dza["evaluatedAt"]
    with pytest.raises(ValueError):
        cp.sign_corroboration(c, org="OBS_C", chain_id=31337, pool=POOL, now=now, expiry=now + 2 * 86400)
    with pytest.raises(ValueError):
        cp.sign_corroboration(dict(c, windowFrom=c["claimSince"] + 1), org="OBS_C", chain_id=31337, pool=POOL,
                              now=now, expiry=now + 60)
    assert cp.sign_corroboration(c, org="OBS_C", chain_id=31337, pool=POOL, now=now, expiry=now + 60)["signer"]


def test_check_against_committed_needs_no_raw_cache(dza):
    rec = cp.find_scene(dza, "2025-11-11 tan")
    honest = cp.build_bundle(dza, rec, covenant_id=COV, claim_id=CLAIM)
    assert cp.check_against_committed(dza, rec, honest)["equal"] is True
    lie = dict(honest, notCloudy=True, cloudCoverPct=0)
    r = cp.check_against_committed(dza, rec, lie)
    assert r["equal"] is False and "bundle.notCloudy" in r["differences"] and not r["sceneHashEqual"]
