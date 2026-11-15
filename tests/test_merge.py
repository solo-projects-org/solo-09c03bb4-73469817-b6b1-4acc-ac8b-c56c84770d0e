"""Cell merge rules: quality first, then intensity, then radar A."""

from tests.conftest import HOUR


def _a(revision, points):
    return {"radar": "A", "hour": HOUR, "revision": revision, "points": points}


def _b(revision, points):
    return {"radar": "B", "hour": HOUR, "revision": revision, "points": points}


def _by_cell(merged):
    return {(p["row"], p["col"]): p for p in merged["points"]}


def test_higher_quality_wins(client):
    client.post("/reports", json=_a(1, [
        {"row": 0, "col": 0, "rain_intensity": 9.0, "quality": 40},
    ]))
    client.post("/reports", json=_b(1, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 80},
    ]))
    cell = _by_cell(client.get("/merge", params={"hour": HOUR}).json())[(0, 0)]
    assert cell["source"] == "B"
    assert cell["quality"] == 80
    assert cell["rain_intensity"] == 1.0


def test_quality_tie_higher_intensity_wins(client):
    client.post("/reports", json=_a(1, [
        {"row": 1, "col": 1, "rain_intensity": 3.0, "quality": 60},
    ]))
    client.post("/reports", json=_b(1, [
        {"row": 1, "col": 1, "rain_intensity": 7.5, "quality": 60},
    ]))
    cell = _by_cell(client.get("/merge", params={"hour": HOUR}).json())[(1, 1)]
    assert cell["source"] == "B"
    assert cell["rain_intensity"] == 7.5


def test_full_tie_goes_to_radar_a(client):
    client.post("/reports", json=_a(1, [
        {"row": 2, "col": 2, "rain_intensity": 4.0, "quality": 60},
    ]))
    client.post("/reports", json=_b(1, [
        {"row": 2, "col": 2, "rain_intensity": 4.0, "quality": 60},
    ]))
    cell = _by_cell(client.get("/merge", params={"hour": HOUR}).json())[(2, 2)]
    assert cell["source"] == "A"


def test_disjoint_cells_from_both_radars_all_appear(client):
    client.post("/reports", json=_a(1, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 50},
        {"row": 0, "col": 1, "rain_intensity": 2.0, "quality": 50},
    ]))
    client.post("/reports", json=_b(1, [
        {"row": 1, "col": 0, "rain_intensity": 3.0, "quality": 50},
    ]))
    merged = client.get("/merge", params={"hour": HOUR}).json()
    cells = _by_cell(merged)
    assert set(cells) == {(0, 0), (0, 1), (1, 0)}
    assert cells[(1, 0)]["source"] == "B"
    assert merged["radars"] == {
        "A": {"revision": 1, "point_count": 2},
        "B": {"revision": 1, "point_count": 1},
    }


def test_merge_reflects_live_revisions_after_replacement(client):
    # A rev1 quality 90, B rev1 quality 50 -> A wins.
    client.post("/reports", json=_a(1, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 90},
    ]))
    client.post("/reports", json=_b(1, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 50},
    ]))
    assert _by_cell(client.get("/merge", params={"hour": HOUR}).json())[(0, 0)][
        "source"
    ] == "A"

    # B rev2 jumps to quality 95 -> B now wins; A is untouched.
    client.post("/reports", json=_b(2, [
        {"row": 0, "col": 0, "rain_intensity": 1.0, "quality": 95},
    ]))
    merged = client.get("/merge", params={"hour": HOUR}).json()
    cell = _by_cell(merged)[(0, 0)]
    assert cell["source"] == "B"
    assert merged["radars"]["B"]["revision"] == 2

    # B rev3 reports an empty grid: its cell disappears and A shows through.
    client.post("/reports", json=_b(3, []))
    merged = client.get("/merge", params={"hour": HOUR}).json()
    cell = _by_cell(merged)[(0, 0)]
    assert cell["source"] == "A"
    assert merged["radars"]["B"] == {"revision": 3, "point_count": 0}
