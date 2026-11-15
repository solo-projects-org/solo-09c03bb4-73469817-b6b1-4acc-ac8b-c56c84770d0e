"""Input validation: duplicate cells, ranges, UTC whole-hour rules."""

from tests.conftest import HOUR


def test_duplicate_cell_rejects_whole_report(client):
    payload = {
        "radar": "A",
        "hour": HOUR,
        "revision": 1,
        "points": [
            {"row": 1, "col": 2, "rain_intensity": 1.0, "quality": 50},
            {"row": 1, "col": 2, "rain_intensity": 2.0, "quality": 60},
        ],
    }
    resp = client.post("/reports", json=payload)
    assert resp.status_code == 422
    assert "duplicate cell" in resp.json()["detail"]

    # Nothing was written: the merge for that hour is completely empty.
    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert merged["points"] == []
    assert merged["radars"] == {
        "A": {"revision": None, "point_count": 0},
        "B": {"revision": None, "point_count": 0},
    }


def test_out_of_range_row_col_quality_intensity(client):
    base = {
        "radar": "A",
        "hour": HOUR,
        "revision": 1,
    }
    bad_cases = [
        [{"row": 32, "col": 0, "rain_intensity": 1.0, "quality": 50}],
        [{"row": -1, "col": 0, "rain_intensity": 1.0, "quality": 50}],
        [{"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 101}],
        [{"row": 0, "col": 0, "rain_intensity": -0.1, "quality": 50}],
    ]
    for points in bad_cases:
        resp = client.post("/reports", json={**base, "points": points})
        assert resp.status_code == 422


def test_naive_hour_rejected(client):
    payload = {
        "radar": "A",
        "hour": "2026-11-15T08:00:00",
        "revision": 1,
        "points": [],
    }
    resp = client.post("/reports", json=payload)
    assert resp.status_code == 422
    assert "timezone-aware" in resp.json()["detail"]


def test_non_whole_hour_rejected(client):
    payload = {
        "radar": "A",
        "hour": "2026-11-15T08:30:00Z",
        "revision": 1,
        "points": [],
    }
    resp = client.post("/reports", json=payload)
    assert resp.status_code == 422
    assert "whole hour" in resp.json()["detail"]


def test_offset_timezone_normalized_to_utc(client):
    # 16:00+08:00 is exactly 08:00 UTC and must be accepted.
    payload = {
        "radar": "A",
        "hour": "2026-11-15T16:00:00+08:00",
        "revision": 1,
        "points": [
            {"row": 0, "col": 0, "rain_intensity": 1.5, "quality": 70}
        ],
    }
    resp = client.post("/reports", json=payload)
    assert resp.status_code == 201
    assert resp.json()["hour"] == HOUR

    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert len(merged["points"]) == 1


def test_revision_must_be_positive(client):
    payload = {
        "radar": "A",
        "hour": HOUR,
        "revision": 0,
        "points": [],
    }
    assert client.post("/reports", json=payload).status_code == 422


def test_empty_sparse_grid_is_allowed(client):
    payload = {"radar": "A", "hour": HOUR, "revision": 1, "points": []}
    resp = client.post("/reports", json=payload)
    assert resp.status_code == 201
    assert resp.json()["point_count"] == 0
    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert merged["radars"]["A"] == {"revision": 1, "point_count": 0}
