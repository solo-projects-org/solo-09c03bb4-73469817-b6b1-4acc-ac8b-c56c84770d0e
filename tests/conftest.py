"""Pytest fixtures: isolated SQLite file per test and a FastAPI test client."""

import os
import pathlib
import tempfile

import pytest

# Point the app at a throwaway database before it is imported.
_TMP_DIR = pathlib.Path(tempfile.mkdtemp(prefix="radar-api-tests-"))
os.environ["RADAR_DB_PATH"] = str(_TMP_DIR / "test.db")
os.environ.setdefault("RADAR_GRID_SIZE", "32")

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    db_path = pathlib.Path(config.DB_PATH)
    for suffix in ("", "-wal", "-shm"):
        p = pathlib.Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def db_path():
    return pathlib.Path(config.DB_PATH)


HOUR = "2026-11-15T08:00:00Z"
HOUR_KEY = HOUR
