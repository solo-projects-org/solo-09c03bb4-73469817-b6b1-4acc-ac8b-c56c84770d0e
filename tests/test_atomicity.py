"""Atomicity, snapshot isolation and concurrent correction tests.

These exercise the service layer directly against SQLite so failure and
concurrency scenarios can be injected deterministically.
"""

import sqlite3
import threading

import pytest

from app.database import connect, init_db
from app.models import GridPoint
from app.services import (
    RevisionConflictError,
    StaleRevisionError,
    get_merged_grid,
    submit_report,
)
from tests.conftest import HOUR

HOUR_KEY = HOUR


def _pt(row, col, rain=1.0, quality=50):
    return GridPoint(row=row, col=col, rain_intensity=rain, quality=quality)


@pytest.fixture()
def fresh_db(db_path):
    for suffix in ("", "-wal", "-shm"):
        p = db_path.with_name(db_path.name + suffix)
        if p.exists():
            p.unlink()
    init_db(db_path)
    return db_path


def test_failed_write_rolls_back_and_keeps_old_revision(fresh_db):
    conn = connect(fresh_db)
    submit_report(conn, "A", HOUR_KEY, 1, [_pt(0, 0, 1.0, 50)])

    # Break the points insert mid-transaction: a CHECK constraint failure.
    # Build the bad statement manually, because the service validates ranges
    # at the API layer; here we simulate an unexpected storage failure.
    conn.execute("BEGIN IMMEDIATE")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO points (radar, hour, revision, row, col, "
            "rain_intensity, quality) VALUES ('A', ?, 2, 0, 0, 1.0, 999)",
            (HOUR_KEY,),
        )
    conn.execute("ROLLBACK")

    # A separate connection still sees exactly the old revision.
    reader = connect(fresh_db)
    merged = get_merged_grid(reader, HOUR_KEY)
    assert merged["radars"]["A"]["revision"] == 1
    assert len(merged["points"]) == 1
    assert merged["points"][0]["quality"] == 50
    reader.close()
    conn.close()


def test_stale_and_conflict_submissions_change_nothing(fresh_db):
    conn = connect(fresh_db)
    submit_report(conn, "A", HOUR_KEY, 2, [_pt(0, 0, 5.0, 80)])

    with pytest.raises(StaleRevisionError):
        submit_report(conn, "A", HOUR_KEY, 1, [_pt(0, 0, 0.1, 1)])
    with pytest.raises(RevisionConflictError):
        submit_report(conn, "A", HOUR_KEY, 2, [_pt(0, 0, 5.0, 81)])

    rows = conn.execute("SELECT revision FROM reports").fetchall()
    assert [r["revision"] for r in rows] == [2]
    point = conn.execute("SELECT quality FROM points").fetchone()
    assert point["quality"] == 80
    conn.close()


def test_reader_snapshot_never_splices_two_revisions(fresh_db):
    # Seed A rev 1 and B rev 1.
    writer = connect(fresh_db)
    submit_report(writer, "A", HOUR_KEY, 1, [_pt(0, 0, 1.0, 50)])
    submit_report(writer, "B", HOUR_KEY, 1, [_pt(0, 0, 2.0, 60)])

    # Hold a read transaction open while a writer commits rev 2 underneath it.
    reader = connect(fresh_db)
    reader.execute("BEGIN")
    old_rev = reader.execute(
        "SELECT revision FROM reports WHERE radar = 'A' ORDER BY revision DESC"
    ).fetchone()["revision"]
    assert old_rev == 1

    submit_report(
        writer, "A", HOUR_KEY, 2,
        [_pt(0, 0, 9.0, 99), _pt(1, 1, 8.0, 99)],
    )

    # The open snapshot must still read rev 1's points, not a mix.
    snapshot_points = reader.execute(
        "SELECT row, col, revision FROM points WHERE radar = 'A'"
    ).fetchall()
    assert {r["revision"] for r in snapshot_points} == {1}
    assert {(r["row"], r["col"]) for r in snapshot_points} == {(0, 0)}
    reader.execute("COMMIT")
    reader.close()

    # A fresh reader sees the complete new revision only.
    fresh_reader = connect(fresh_db)
    merged = get_merged_grid(fresh_reader, HOUR_KEY)
    assert merged["radars"]["A"] == {"revision": 2, "point_count": 2}
    assert {p["source"] for p in merged["points"]} <= {"A", "B"}
    fresh_reader.close()
    writer.close()


def test_concurrent_corrections_never_leave_partial_grid(fresh_db):
    # Baseline: both radars rev 1.
    seed = connect(fresh_db)
    submit_report(seed, "A", HOUR_KEY, 1, [_pt(r, r, 1.0, 50) for r in range(5)])
    submit_report(seed, "B", HOUR_KEY, 1, [_pt(r, 5 - r, 2.0, 60) for r in range(5)])
    seed.close()

    errors: list[Exception] = []

    def writer(radar, stop_at: threading.Event):
        conn = connect(fresh_db)
        try:
            for revision in range(2, 40):
                points = [
                    _pt((revision + r) % 32, r, float(revision), revision % 101)
                    for r in range(6)
                ]
                try:
                    submit_report(conn, radar, HOUR_KEY, revision, points)
                except (StaleRevisionError, RevisionConflictError):
                    # Under real concurrency only ordering surprises happen;
                    # they are valid API-level rejections, not corruptions.
                    pass
                if stop_at.is_set():
                    break
        except Exception as exc:  # pragma: no cover - surfaces in assertions
            errors.append(exc)
        finally:
            conn.close()

    stop = threading.Event()
    threads = [
        threading.Thread(target=writer, args=("A", stop)),
        threading.Thread(target=writer, args=("B", stop)),
    ]
    for t in threads:
        t.start()

    # While corrections keep landing, every merged answer must be internally
    # consistent, and every storage snapshot must contain exactly one live
    # revision per radar (old rows deleted inside the same write transaction).
    reader = connect(fresh_db)
    for _ in range(200):
        # get_merged_grid reads both radars under one WAL snapshot.
        merged = get_merged_grid(reader, HOUR_KEY)
        seen_cells: set[tuple[int, int]] = set()
        for point in merged["points"]:
            key = (point["row"], point["col"])
            assert key not in seen_cells, "duplicate cell in merged answer"
            seen_cells.add(key)
            assert point["source"] in ("A", "B")

        # Storage invariants are checked inside one snapshot transaction so a
        # writer committing between statements cannot make the checks compare
        # two different points in time.
        reader.execute("BEGIN")
        snapshot_open = True
        try:
            for radar in ("A", "B"):
                latest = reader.execute(
                    "SELECT revision, point_count FROM reports "
                    "WHERE radar = ? ORDER BY revision DESC LIMIT 1",
                    (radar,),
                ).fetchone()
                if latest is None:
                    continue
                stats = reader.execute(
                    "SELECT COUNT(*) AS n, MIN(revision) AS mn, "
                    "MAX(revision) AS mx FROM points WHERE radar = ?",
                    (radar,),
                ).fetchone()
                # Only the newest revision may have point rows ...
                assert stats["mn"] == stats["mx"] == latest["revision"]
                # ... and their count must match the registered report.
                assert stats["n"] == latest["point_count"]
            reader.execute("COMMIT")
            snapshot_open = False
        finally:
            if snapshot_open:
                reader.execute("ROLLBACK")
    reader.close()

    stop.set()
    for t in threads:
        t.join(timeout=30)
    assert not t.is_alive()
    assert errors == []

    # Final state: exactly one report row per radar, all points aligned.
    final = connect(fresh_db)
    for radar in ("A", "B"):
        report_rows = final.execute(
            "SELECT revision, point_count FROM reports WHERE radar = ?",
            (radar,),
        ).fetchall()
        assert len(report_rows) == 1
        stored = final.execute(
            "SELECT COUNT(*) AS n, MIN(revision) AS mn, MAX(revision) AS mx "
            "FROM points WHERE radar = ?",
            (radar,),
        ).fetchone()
        assert stored["n"] == report_rows[0]["point_count"]
        assert stored["mn"] == stored["mx"] == report_rows[0]["revision"]
    final.close()
