"""Revision ordering, replacement, idempotency and conflicts."""

from tests.conftest import HOUR


def _report(radar, revision, points):
    return {"radar": radar, "hour": HOUR, "revision": revision, "points": points}


def test_first_accept_then_higher_revision_replaces_all_points(client):
    r1 = client.post(
        "/reports",
        json=_report("A", 1, [
            {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 50},
            {"row": 0, "col": 1, "rain_intensity": 2.0, "quality": 50},
        ]),
    )
    assert r1.status_code == 201
    assert r1.json()["status"] == "accepted"
    assert r1.json()["replaced_revision"] is None

    r2 = client.post(
        "/reports",
        json=_report("A", 2, [
            # Only one cell: the unreported cell from rev 1 must disappear.
            {"row": 0, "col": 0, "rain_intensity": 9.0, "quality": 90},
        ]),
    )
    assert r2.status_code == 201
    body = r2.json()
    assert body["status"] == "accepted"
    assert body["replaced_revision"] == 1

    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert len(merged["points"]) == 1
    cell = merged["points"][0]
    assert (cell["row"], cell["col"]) == (0, 0)
    assert cell["rain_intensity"] == 9.0
    assert cell["quality"] == 90
    assert cell["source"] == "A"
    assert merged["radars"]["A"] == {"revision": 2, "point_count": 1}


def test_lower_revision_is_stale_and_does_not_roll_back(client):
    client.post("/reports", json=_report("A", 2, [
        {"row": 0, "col": 0, "rain_intensity": 9.0, "quality": 90},
    ]))
    stale = client.post("/reports", json=_report("A", 1, [
        {"row": 0, "col": 0, "rain_intensity": 0.1, "quality": 1},
    ]))
    assert stale.status_code == 409
    assert "stale revision" in stale.json()["detail"]

    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert merged["radars"]["A"]["revision"] == 2
    assert merged["points"][0]["rain_intensity"] == 9.0


def test_same_revision_same_content_is_idempotent(client):
    payload = _report("B", 3, [
        {"row": 5, "col": 5, "rain_intensity": 4.0, "quality": 77},
    ])
    first = client.post("/reports", json=payload)
    assert first.status_code == 201
    first_hash = first.json()["content_hash"]

    # Reordering points must still count as identical content.
    reordered = _report("B", 3, [
        {"row": 5, "col": 5, "quality": 77, "rain_intensity": 4.0},
    ])
    second = client.post("/reports", json=reordered)
    assert second.status_code == 201
    body = second.json()
    assert body["status"] == "idempotent"
    assert body["content_hash"] == first_hash

    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert merged["radars"]["B"] == {"revision": 3, "point_count": 1}


def test_same_revision_different_content_conflicts(client):
    client.post("/reports", json=_report("A", 1, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 50},
    ]))
    conflict = client.post("/reports", json=_report("A", 1, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 51},
    ]))
    assert conflict.status_code == 409
    assert "conflict" in conflict.json()["detail"]

    # Original content is untouched.
    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert merged["points"][0]["quality"] == 50


def test_radars_and_hours_are_independent(client):
    other_hour = "2026-11-15T09:00:00Z"
    client.post("/reports", json=_report("A", 5, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 50},
    ]))
    client.post("/reports", json={
        "radar": "B", "hour": other_hour, "revision": 2, "points": [
            {"row": 1, "col": 1, "rain_intensity": 2.0, "quality": 60},
        ],
    })

    at_8 = client.get("/merge", params={"hour": HOUR}).json()
    assert at_8["radars"]["A"]["revision"] == 5
    assert at_8["radars"]["B"]["revision"] is None

    at_9 = client.get("/merge", params={"hour": other_hour}).json()
    assert at_9["radars"]["A"]["revision"] is None
    assert at_9["radars"]["B"]["revision"] == 2


def test_unreported_radar_revision_is_null(client):
    client.post("/reports", json=_report("A", 1, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 50},
    ]))
    merged = client.get("/merge", params={"hour": HOUR}).json()
    assert merged["radars"]["A"]["revision"] == 1
    assert merged["radars"]["B"] == {"revision": None, "point_count": 0}
