"""Evidence bundle: build from a Carbon Mapper plume record, validate, canonicalise (JCS) and hash.

The bundle carries only what every honest observer derives identically from the same provider data, so k observers
fetching independently produce the same evidenceHash. Observer-specific facts (retrievedAt, raw bytes hash of the
exact response they got) live in each observer's local receipt, not in the hashed bundle (spec §4 deviation: two
observers can never agree on a bundle that contains their own fetch time).

sourceId is the covered *facility* (keccak256 of the MARS source_name). The Carbon Mapper source the plume belongs
to must be one of the facility's linked CM sources in the committed source set. One facility = one linkWindow
anchor, so several CM twin sources seeing the same overpass cannot pay twice (they record as LINKED).
"""
from __future__ import annotations

from datetime import datetime, timezone

from canon import canonical_bytes, evidence_hash
from common import RADIUS_KM, haversine_km

SCHEMA_VERSION = "methaneclause.bundle/1"
PROVIDER_CM = "carbonmapper"
# uint8 instrumentId in the attestation. Unknown instruments are rejected, not guessed.
INSTRUMENTS = {"tan": 1, "emi": 2, "av3": 3, "ang": 4, "gao": 5}
MAX_RATE_KGH = 100_000  # 100 t/h: above every satellite plume on record; a bigger number is a unit error
MIN_RATE_KGH = 1

FIELDS = {  # name -> python type(s) the canonical bundle must carry
    "schemaVersion": str, "covenantId": str, "sourceId": str, "sourceName": str, "cmSourceName": str,
    "plumeId": str, "providerId": str, "instrument": str, "platform": str, "detectedAt": int,
    "sceneTimestamp": str, "emissionKgH": int, "uncertaintyKgH": int, "emissionVersion": str,
    "processingSoftware": str, "publishedAt": str, "plumeLat": float, "plumeLon": float,
    "providerContentSha256": str,
}


class ValidationError(ValueError):
    """Raised before signing. `code` is stable and used by the adversarial tests."""

    def __init__(self, code: str, msg: str):
        super().__init__(f"{code}: {msg}")
        self.code = code


def iso_to_unix(ts: str) -> int:
    return int(datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def build_bundle(plume: dict, *, covenant_id: str, source_id: str, source_name: str, content_sha256: str) -> dict:
    """Map one Carbon Mapper plume row (PLUME_FIELDS + lat/lon) to the canonical bundle. No validation here."""
    def rnd(x):
        return None if x is None else int(round(float(x)))

    return {
        "schemaVersion": SCHEMA_VERSION,
        "covenantId": covenant_id,
        "sourceId": source_id,
        "sourceName": source_name,
        "cmSourceName": plume.get("source_name"),
        "plumeId": plume.get("plume_id"),
        "providerId": PROVIDER_CM,
        "instrument": plume.get("instrument"),
        "platform": plume.get("platform"),
        "detectedAt": iso_to_unix(plume["scene_timestamp"]) if plume.get("scene_timestamp") else None,
        "sceneTimestamp": plume.get("scene_timestamp"),
        "emissionKgH": rnd(plume.get("emission_auto")),
        "uncertaintyKgH": rnd(plume.get("emission_uncertainty_auto")),
        "emissionVersion": plume.get("emission_version"),
        "processingSoftware": plume.get("processing_software"),
        "publishedAt": plume.get("published_at"),
        "plumeLat": None if plume.get("lat") is None else float(plume["lat"]),
        "plumeLon": None if plume.get("lon") is None else float(plume["lon"]),
        "providerContentSha256": content_sha256,
    }


def validate(b: dict, *, facility: dict, covered_cm: set[str], covenant_start: int, now: int,
             min_rate_kgh: int = 100, seen_plumes: set[str] | None = None) -> None:
    """Checks before signing (spec §4 observer validation). Raises ValidationError; returns None if signable.

    facility: {"sourceId", "source_name", "lat", "lon"} of the covered MARS facility.
    covered_cm: Carbon Mapper source names linked to that facility in the committed source set.
    """
    # schema: exact key set, types, no nulls
    if set(b) != set(FIELDS):
        raise ValidationError("SCHEMA", f"keys differ: missing {set(FIELDS) - set(b)}, extra {set(b) - set(FIELDS)}")
    for k, t in FIELDS.items():
        v = b[k]
        if v is None or isinstance(v, bool) or not isinstance(v, t):
            raise ValidationError("SCHEMA", f"{k}={v!r} is not {t.__name__}")
    if b["schemaVersion"] != SCHEMA_VERSION or b["providerId"] != PROVIDER_CM:
        raise ValidationError("SCHEMA", "unknown schemaVersion/providerId")
    if b["instrument"] not in INSTRUMENTS:
        raise ValidationError("SCHEMA", f"unknown instrument {b['instrument']!r}")
    if not b["emissionVersion"]:
        raise ValidationError("SCHEMA", "missing emission_version")

    # physical range (unit swaps t/h<->kg/h land outside it or fail the uncertainty check)
    if not MIN_RATE_KGH <= b["emissionKgH"] < MAX_RATE_KGH:
        raise ValidationError("RANGE", f"emissionKgH={b['emissionKgH']} outside [{MIN_RATE_KGH}, {MAX_RATE_KGH})")
    if b["emissionKgH"] < min_rate_kgh:  # also catches t/h written as kg/h (2,548 -> 3)
        raise ValidationError("BELOW_FLOOR", f"emissionKgH={b['emissionKgH']} < covenant minRate {min_rate_kgh}")
    if not 0 <= b["uncertaintyKgH"] < MAX_RATE_KGH:
        raise ValidationError("RANGE", f"uncertaintyKgH={b['uncertaintyKgH']}")

    # time sanity: detected inside the covenant, published after the scene, nothing from the future
    t = b["detectedAt"]
    if iso_to_unix(b["sceneTimestamp"]) != t:
        raise ValidationError("TIME", "detectedAt != sceneTimestamp")
    if t <= covenant_start:
        raise ValidationError("TIME", "detected before the covenant started")
    pub = iso_to_unix(b["publishedAt"])
    if t > now or pub > now:
        raise ValidationError("TIME", "detection or publication is in the future")
    if pub < t:
        raise ValidationError("TIME", "published before the scene was acquired")

    # coverage: facility is the covenant's, CM source is linked to it, plume origin within 1.5 km of the facility
    if b["sourceId"] != facility["sourceId"] or b["sourceName"] != facility["source_name"]:
        raise ValidationError("NOT_COVERED", "sourceId is not the covered facility")
    if b["cmSourceName"] not in covered_cm:
        raise ValidationError("NOT_COVERED", f"{b['cmSourceName']} is not linked to {facility['source_name']}")
    d = float(haversine_km(facility["lat"], facility["lon"], b["plumeLat"], b["plumeLon"]))
    if d > RADIUS_KM:
        raise ValidationError("DISTANCE", f"plume origin {d:.2f} km from {facility['source_name']} > {RADIUS_KM} km")

    if seen_plumes is not None and b["plumeId"] in seen_plumes:
        raise ValidationError("DUPLICATE", f"plume {b['plumeId']} already attested")


def bundle_bytes(b: dict) -> bytes:
    return canonical_bytes(b)


def bundle_hash(b: dict) -> str:
    return evidence_hash(b)
