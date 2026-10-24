"""Observer: canonical bundle, validation before signing (spec §10 bad inputs), EIP-712 signing."""
from __future__ import annotations

import copy
import json

import pytest

from canon import evidence_hash_text
from common import ROOT
from observer import attest
from observer.bundle import ValidationError, bundle_hash, build_bundle, validate
from observer.service import Observer, load_facility

CASE, PLUME = "TKM_S_276", "tan20260611t083132c23s4001-AB"
COV = "0x" + "00" * 31 + "01"
START, NOW = 1767225600, 1783765160  # 2026-01-01; 1 h after the plume was published
CONTRACT = "0x5FbDB2315678afecb367f032d7b4F0b2b6aE8bF2"

FACILITY, COVERED, CASE_FILE = load_facility(CASE)
PLUME_ROW = next(p for p in CASE_FILE["cm_plumes_within_radius"] if p["plume_id"] == PLUME)
CONTENT = CASE_FILE["cm_sources"][PLUME_ROW["source_name"]]["content_sha256"]


def bundle(**plume_edits) -> dict:
    row = {**PLUME_ROW, **plume_edits}
    return build_bundle(row, covenant_id=COV, source_id=FACILITY["sourceId"], source_name=FACILITY["source_name"],
                        content_sha256=CONTENT)


def check(b: dict, seen: set[str] | None = None) -> None:
    validate(b, facility=FACILITY, covered_cm=COVERED, covenant_start=START, now=NOW, seen_plumes=seen)


def test_lead_bundle_is_valid_and_matches_the_case():
    b = bundle()
    check(b)
    assert (b["emissionKgH"], b["uncertaintyKgH"], b["instrument"]) == (2548, 483, "tan")
    assert b["sourceId"] == CASE_FILE["sourceId"]


# Spec §10 adversarial inputs that the observer must refuse to sign: one list, shared with the audited report.
from observer.adversarial import BAD  # noqa: E402


@pytest.mark.parametrize("label,edits,code", BAD, ids=[b[0] for b in BAD])
def test_bad_input_refused(label, edits, code):
    with pytest.raises(ValidationError) as e:
        check(bundle(**edits))
    assert e.value.code == code


def test_source_not_in_set_refused():
    b = bundle()
    b["sourceId"] = "0x" + "11" * 32
    with pytest.raises(ValidationError) as e:
        check(b)
    assert e.value.code == "NOT_COVERED"


def test_extra_field_refused():
    b = bundle()
    b["note"] = "trust me"
    with pytest.raises(ValidationError) as e:
        check(b)
    assert e.value.code == "SCHEMA"


def test_duplicate_plume_refused():
    with pytest.raises(ValidationError) as e:
        check(bundle(), seen={PLUME})
    assert e.value.code == "DUPLICATE"


def test_non_canonical_json_same_hash():
    b = bundle()
    shuffled = json.dumps(dict(reversed(list(b.items()))), indent=3)
    assert evidence_hash_text(shuffled) == bundle_hash(b)


@pytest.mark.parametrize("field,value", [("emissionKgH", 548), ("uncertaintyKgH", 400), ("plumeId", "x"),
                                         ("providerContentSha256", "0" * 64)])
def test_any_edit_changes_hash(field, value):
    b = bundle()
    t = copy.deepcopy(b)
    t[field] = value
    assert bundle_hash(t) != bundle_hash(b)


def test_two_observers_agree_and_sign_recoverably():
    a, b = Observer("OBS_A", 31337, CONTRACT), Observer("OBS_B", 31337, CONTRACT)
    kw = {"covenant_id": COV, "covenant_start": START, "now": NOW}
    sa, sb = a.attest(CASE, PLUME, **kw), b.attest(CASE, PLUME, **kw)
    assert sa["evidenceHash"] == sb["evidenceHash"]
    assert sa["signer"] != sb["signer"]
    for s in (sa, sb):
        assert attest.recover(s["attestation"], bytes.fromhex(s["signature"][2:]), 31337, CONTRACT) == s["signer"]
        assert len(bytes.fromhex(s["signature"][2:])) == 65
    # an edited attestation recovers to a different (unregistered) address -> InvalidSignature on-chain
    edited = dict(sa["attestation"], emissionKgH=548)
    assert attest.recover(edited, bytes.fromhex(sa["signature"][2:]), 31337, CONTRACT) != sa["signer"]
    # a signature for another chain does not verify here (cross-chain replay)
    assert attest.recover(sa["attestation"], bytes.fromhex(sa["signature"][2:]), 84532, CONTRACT) != sa["signer"]


def test_observer_disagreement_gives_different_hash():
    a, c = Observer("OBS_A", 31337, CONTRACT), Observer("OBS_C", 31337, CONTRACT)
    kw = {"covenant_id": COV, "covenant_start": START, "now": NOW}
    assert (a.attest(CASE, PLUME, **kw)["evidenceHash"]
            != c.attest(CASE, PLUME, **kw, override={"emission_auto": 2610.0})["evidenceHash"])


def test_same_observer_will_not_sign_a_plume_twice():
    a = Observer("OBS_A", 31337, CONTRACT)
    kw = {"covenant_id": COV, "covenant_start": START, "now": NOW}
    a.attest(CASE, PLUME, **kw)
    with pytest.raises(ValidationError):
        a.attest(CASE, PLUME, **kw)


def test_anvil_keys_refused_off_anvil(monkeypatch):
    monkeypatch.delenv("OBSERVER_A_PK", raising=False)
    with pytest.raises(SystemExit):
        Observer("OBS_A", 84532, CONTRACT)


def test_only_facilities_are_leaves():
    """A Carbon Mapper twin source must not be coverable on its own (it would get its own linkWindow anchor)."""
    from common import DERIVED
    from merkle import hx, source_id

    ss = json.loads((DERIVED / "sourceset_v2.json").read_text())
    leaves = {s["sourceId"] for s in ss["sources"]}
    assert {s["source_name"] for s in ss["sources"]} == {"DZA_S_012", "TKM_S_276", "USA_S_077"}
    for s in ss["sources"]:
        for cm in s["linked_cm_sources"]:
            assert hx(source_id(cm)) not in leaves
    linked = [cm for s in ss["sources"] for cm in s["linked_cm_sources"]]
    assert len(linked) == len(set(linked))  # one CM source never counts for two facilities


def test_sandbox_keys_match_the_deploy_script():
    """The judge sandbox's published keys are derived identically in Python and in Deploy.s.sol."""
    from observer import sandbox

    sol = (ROOT / "contracts" / "script" / "SandboxBase.sol").read_text()
    assert f'SANDBOX_KEY_PREFIX = "{sandbox.PREFIX}"' in sol
    for role in (*sandbox.ROLES, sandbox.JUDGE_CONTROLLER):
        assert f'sandboxKey("{role}")' in sol
    assert f'setOrgController("{sandbox.JUDGE_ORG}"' in sol
    roles = (*sandbox.ROLES, sandbox.JUDGE_CONTROLLER)
    assert len({sandbox.address(r) for r in roles}) == len(roles)


def test_adversarial_set_all_caught_and_slow_drift_is_a_hash_catch():
    """data/derived/adversarial.json: every case caught; slow drift passes validation and dies on the re-fetch hash."""
    from observer.adversarial import run

    r = run()
    assert r["caught"] == r["n"] >= 18
    assert all(s["validation"] == "passes" and not s["hashMatchesRefetch"] for s in r["slowDrift"])
