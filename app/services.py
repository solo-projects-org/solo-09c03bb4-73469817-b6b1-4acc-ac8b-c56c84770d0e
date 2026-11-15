"""Domain logic: revision control, atomic replacement and grid merging.

All write work for one report happens inside a single SQLite transaction so a
failure can never leave a half-written revision behind. Reads open their own
deferred transaction, which in WAL mode yields a point-in-time snapshot that
cannot mix two revisions of the same radar into one answer.
"""

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from .config import RADARS
from .models import GridPoint


class StaleRevisionError(Exception):
    """A lower revision arrived after a higher one was already accepted."""


class RevisionConflictError(Exception):
    """Same revision re-sent with different content."""


@dataclass(frozen=True)
class StoredPoint:
    radar: str
    revision: int
    row: int
    col: int
    rain_intensity: float
    quality: int


def _content_hash(radar: str, hour_key: str, revision: int,
                  points: List[GridPoint]) -> str:
    """Deterministic hash of the full report payload.

    Point order in the JSON body must not matter for idempotency, so points are
    sorted by (row, col) before hashing.
    """
    canonical = {
        "radar": radar,
        "hour": hour_key,
        "revision": revision,
        "points": sorted(
            (
                {
                    "row": p.row,
                    "col": p.col,
                    "rain_intensity": p.rain_intensity,
                    "quality": p.quality,
                }
                for p in points
            ),
            key=lambda p: (p["row"], p["col"]),
        ),
    }
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def submit_report(
    conn: sqlite3.Connection,
    radar: str,
    hour_key: str,
    revision: int,
    points: List[GridPoint],
) -> dict:
    """Validate and store one full report atomically.

    Returns a summary dict on success. Raises StaleRevisionError /
    RevisionConflictError on rejected submissions; any other sqlite3 error is
    propagated after rollback, leaving the previous revision fully intact.
    """
    content_hash = _content_hash(radar, hour_key, revision, points)

    conn.execute("BEGIN IMMEDIATE")
    transaction_open = True
    try:
        row = conn.execute(
            "SELECT revision, content_hash FROM reports "
            "WHERE radar = ? AND hour = ? "
            "ORDER BY revision DESC LIMIT 1",
            (radar, hour_key),
        ).fetchone()
        latest_revision: Optional[int] = row["revision"] if row else None
        latest_hash: Optional[str] = row["content_hash"] if row else None

        if latest_revision is not None and revision < latest_revision:
            raise StaleRevisionError(
                f"stale revision {revision} for radar {radar} hour {hour_key}: "
                f"latest accepted revision is {latest_revision}"
            )

        replaced_revision: Optional[int] = None
        if latest_revision is not None and revision == latest_revision:
            if content_hash == latest_hash:
                # Identical re-delivery: no-op, report as idempotent accept.
                conn.execute("ROLLBACK")
                transaction_open = False
                return {
                    "status": "idempotent",
                    "radar": radar,
                    "hour": hour_key,
                    "revision": revision,
                    "point_count": len(points),
                    "replaced_revision": None,
                    "content_hash": content_hash,
                }
            raise RevisionConflictError(
                f"conflict: radar {radar} hour {hour_key} revision {revision} "
                "already exists with different content"
            )

        # New higher revision: remove every old row for this radar/hour and
        # insert the complete replacement set in the same transaction.
        if latest_revision is not None:
            replaced_revision = latest_revision
            conn.execute(
                "DELETE FROM points WHERE radar = ? AND hour = ?",
                (radar, hour_key),
            )
            conn.execute(
                "DELETE FROM reports WHERE radar = ? AND hour = ?",
                (radar, hour_key),
            )

        conn.execute(
            "INSERT INTO reports (radar, hour, revision, content_hash, "
            "point_count) VALUES (?, ?, ?, ?, ?)",
            (radar, hour_key, revision, content_hash, len(points)),
        )
        conn.executemany(
            "INSERT INTO points (radar, hour, revision, row, col, "
            "rain_intensity, quality) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    radar,
                    hour_key,
                    revision,
                    p.row,
                    p.col,
                    p.rain_intensity,
                    p.quality,
                )
                for p in points
            ],
        )
        conn.execute("COMMIT")
        transaction_open = False
        return {
            "status": "accepted",
            "radar": radar,
            "hour": hour_key,
            "revision": revision,
            "point_count": len(points),
            "replaced_revision": replaced_revision,
            "content_hash": content_hash,
        }
    finally:
        if transaction_open:
            conn.execute("ROLLBACK")


def _load_radar_snapshot(conn: sqlite3.Connection, radar: str,
                         hour_key: str) -> tuple[Optional[int], List[StoredPoint]]:
    """Read latest revision and its points for one radar under one snapshot."""
    latest = conn.execute(
        "SELECT revision FROM reports WHERE radar = ? AND hour = ? "
        "ORDER BY revision DESC LIMIT 1",
        (radar, hour_key),
    ).fetchone()
    if latest is None:
        return None, []
    revision = latest["revision"]
    rows = conn.execute(
        "SELECT radar, revision, row, col, rain_intensity, quality "
        "FROM points WHERE radar = ? AND hour = ? AND revision = ?",
        (radar, hour_key, revision),
    ).fetchall()
    return revision, [StoredPoint(**dict(r)) for r in rows]


def get_merged_grid(
    conn: sqlite3.Connection, hour_key: str
) -> dict:
    """Return the merged grid plus per-radar revision metadata.

    A single deferred read transaction gives one WAL snapshot for both radars,
    so a writer committing mid-query cannot make the answer mix old and new
    revisions.
    """
    conn.execute("BEGIN")
    transaction_open = True
    try:
        per_radar: dict[str, dict] = {}
        candidates: list[StoredPoint] = []
        for radar in RADARS:
            revision, pts = _load_radar_snapshot(conn, radar, hour_key)
            per_radar[radar] = {
                "revision": revision,
                "point_count": len(pts),
            }
            candidates.extend(pts)
        conn.execute("COMMIT")
        transaction_open = False
    finally:
        if transaction_open:
            conn.execute("ROLLBACK")

    # Cell-level merge: highest quality wins; ties -> higher intensity;
    # remaining ties -> radar A. Dict keeps at most one winner per cell.
    winners: dict[tuple[int, int], StoredPoint] = {}
    for point in candidates:
        key = (point.row, point.col)
        current = winners.get(key)
        if current is None:
            winners[key] = point
            continue
        if (point.quality, point.rain_intensity, 1 if point.radar == "A" else 0) > (
            current.quality,
            current.rain_intensity,
            1 if current.radar == "A" else 0,
        ):
            winners[key] = point

    merged_points = [
        {
            "row": row,
            "col": col,
            "rain_intensity": p.rain_intensity,
            "quality": p.quality,
            "source": p.radar,
        }
        for (row, col), p in sorted(winners.items())
    ]
    return {
        "hour": hour_key,
        "grid_size": 32,
        "radars": per_radar,
        "points": merged_points,
    }
