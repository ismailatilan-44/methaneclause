"""B7 scene records (pipeline/scene_records.py): the pure derivation on synthetic provider responses, the fetcher's
fail-closed paths without network, and the committed data/derived/scenes_v1/ files (no raw data needed)."""
from __future__ import annotations

import json
import re

import pytest

import scene_records as sr
from common import DERIVED, sha256_file

FAC = {"name": "TST_S_001", "lat": 30.0, "lon": 10.0}


def _plume(pid, lon, lat, published="2025-01-10T00:00:00Z", scene_id="sc1", when="2025-01-01T10:00:00Z", inst="tan"):
    return {"plume_id": pid, "geometry_json": {"type": "Point", "coordinates": [lon, lat]}, "published_at": published,
            "scene_id": scene_id, "scene_timestamp": when, "instrument": inst, "gas": "CH4"}


def test_origin_points_within_radius_earliest_publication():
    src = {"A": [_plume("p1", 10.001, 30.001, "2025-03-01T00:00:00Z"),
                 _plume("p2", 10.001, 30.001, "2025-02-01T00:00:00Z"),   # same point, earlier publication
                 _plume("p3", 10.2, 30.0)],                              # ~19 km away: not an origin of this facility
           "B": [_plume("p4", 9.999, 30.0, None)]}                     # no publication time: never "known"
    pts, pub = sr.origin_points(FAC, src)
    assert pts == [[9.999, 30.0], [10.001, 30.001]]
    assert pub == [None, sr.ts("2025-02-01T00:00:00Z")]


def test_near_plumes_and_source_hits():
    items = [_plume("n1", 10.005, 30.005, scene_id="sc1"), _plume("far", 10.05, 30.0, scene_id="sc1")]
    near = sr.near_plumes(FAC, items)
    assert [p["plumeId"] for p in near] == ["n1"] and near[0]["distKm"] < 1.5
    scene = {"id": "sc1", "timestamp": "2025-01-01T10:00:00Z", "instrument": "tan"}
    plumes = [_plume("a", 10, 30, scene_id="sc1"), _plume("b", 10, 30, scene_id=None),  # same date + instrument
              _plume("c", 10, 30, scene_id="other", when="2025-01-02T10:00:00Z")]
    assert sr.source_hits(plumes, scene) == ["a", "b"]


def test_compact_scene_keeps_only_rule_fields_and_hashes_the_record():
    item = {"id": "sc1", "name": "tan20250101t100000", "timestamp": "2025-01-01T10:00:00Z",
            "published_at": "2025-01-20T00:00:00Z", "instrument": "tan", "cloud_cover_pct": 5,
            "cloud_cover_pct_assessed": 10, "not_cloudy": True, "published_plume_count": 1, "bounds": [0, 0, 1, 1],
            "png": "https://x/y.png?Signature=abc"}
    near = sr.near_plumes(FAC, [_plume("n1", 10.005, 30.005, scene_id="sc1")])
    s = sr.compact_scene(item, [1, 0], {"A": [_plume("a", 10, 30, scene_id="sc1")], "B": []}, near)
    assert s["originPointsInside"] == [0, 1] and s["perSourcePlumeIds"] == {"A": ["a"]}
    assert s["plumesNearFacility"] == ["n1"] and s["publishedAt"] == sr.ts("2025-01-20T00:00:00Z")
    assert re.fullmatch(r"[0-9a-f]{64}", s["sceneRecordSha256"])
    assert "bounds" not in s and not any("Signature" in str(v) for v in s.values())
    from clean_passes import record_sha256
    assert s["sceneRecordSha256"] == record_sha256({k: v for k, v in item.items() if k != "png"})  # signed URL dropped


def test_cache_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sr, "CACHE", tmp_path)
    monkeypatch.setenv("OFFLINE", "1")
    with pytest.raises(sr.OfflineMiss):
        sr.cached("source", "X", "https://example.invalid/")
    (tmp_path / "source").mkdir()
    (tmp_path / "source" / "X.json").write_bytes(b'{"plumes": []}')
    (tmp_path / "source" / "X.meta.json").write_text(json.dumps({"sha256": "00", "contentSha256": "00"}))
    with pytest.raises(ValueError, match="cache corrupted"):
        sr.cached("source", "X", "https://example.invalid/")


def test_polite_get_does_not_retry_a_bad_query(monkeypatch, tmp_path):
    calls = []

    class R:
        status_code, content, url = 422, b"{}", "https://example.invalid/?q"

    monkeypatch.setattr(sr, "LOG", tmp_path / "requests.log")
    monkeypatch.setattr(sr, "DELAY_S", 0)
    monkeypatch.setattr(sr.requests, "get", lambda *a, **k: calls.append(1) or R())
    with pytest.raises(RuntimeError, match="HTTP 422"):
        sr.polite_get("https://example.invalid/")
    assert len(calls) == 1 and (tmp_path / "requests.log").read_text().count("\t422\t") == 1


# ------------------------------------------------------------------------------------------------ committed files
def _index() -> dict:
    return json.loads((DERIVED / "scenes_v1" / "INDEX.json").read_text())


def test_index_hashes_every_committed_scene_file():
    files = sorted(p.stem for p in (DERIVED / "scenes_v1").glob("*.json") if p.name != "INDEX.json")
    assert files == sorted(_index()["files"]) and _index()["written"] == len(files)
    for name, h in _index()["files"].items():
        assert sha256_file(DERIVED / "scenes_v1" / f"{name}.json") == h


def test_committed_scene_files_carry_the_rule_fields_only():
    keys = {"sceneProviderId", "sceneName", "sceneAt", "publishedAt", "instrument", "cloudCoverPct",
            "cloudCoverPctAssessed", "notCloudy", "publishedPlumeCount", "originPointsInside", "perSourcePlumeIds",
            "plumesNearFacility", "sceneRecordSha256"}
    for name in _index()["files"]:
        doc = json.loads((DERIVED / "scenes_v1" / f"{name}.json").read_text())
        assert doc["schema"] == sr.SCHEMA and doc["facility"] == name and doc["claims"]
        assert len(doc["originPoints"]) == len(doc["originPointPublishedAt"]) and doc["pointQueries"]
        for s in doc["scenes"]:
            assert set(s) == keys and s["instrument"] in sr.INSTRUMENTS
            assert re.fullmatch(r"[0-9a-f]{64}", s["sceneRecordSha256"])
            assert s["originPointsInside"] and max(s["originPointsInside"]) < len(doc["originPoints"])
        assert not any(x in json.dumps(doc) for x in ("Signature", "X-Amz", "Key-Pair-Id"))
