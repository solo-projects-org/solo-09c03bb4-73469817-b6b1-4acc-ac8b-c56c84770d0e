"""SQLite connection management and schema initialization."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import DB_PATH

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS reports (
    radar        TEXT NOT NULL CHECK (radar IN ('A', 'B')),
    hour         TEXT NOT NULL,
    revision     INTEGER NOT NULL CHECK (revision >= 1),
    content_hash TEXT NOT NULL,
    point_count  INTEGER NOT NULL CHECK (point_count >= 0),
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    PRIMARY KEY (radar, hour, revision)
);

-- Only the newest revision per radar/hour is kept live; old rows are deleted
-- atomically inside the same write transaction as the new insert.
CREATE TABLE IF NOT EXISTS points (
    radar          TEXT NOT NULL CHECK (radar IN ('A', 'B')),
    hour           TEXT NOT NULL,
    revision       INTEGER NOT NULL,
    row            INTEGER NOT NULL CHECK (row >= 0 AND row < 32),
    col            INTEGER NOT NULL CHECK (col >= 0 AND col < 32),
    rain_intensity REAL NOT NULL CHECK (rain_intensity >= 0),
    quality        INTEGER NOT NULL CHECK (quality >= 0 AND quality <= 100),
    PRIMARY KEY (radar, hour, revision, row, col),
    FOREIGN KEY (radar, hour, revision)
        REFERENCES reports (radar, hour, revision) ON DELETE CASCADE
);
"""


def get_db_path() -> Path:
    return Path(DB_PATH)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a SQLite connection tuned for safe concurrent API access."""
    path = Path(db_path) if db_path is not None else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(path),
        timeout=30.0,
        isolation_level=None,  # autocommit; transactions are managed explicitly
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    # WAL lets readers keep a consistent snapshot while a writer commits.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA synchronous = FULL")
    return conn


def init_db(db_path: Path | None = None) -> None:
    """Create tables (idempotent). Safe to call on every startup."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
    finally:
        conn.close()


@contextmanager
def db_session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()
