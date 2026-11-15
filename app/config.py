"""Runtime configuration."""

import os
from pathlib import Path

# SQLite database location. Overridable for tests and for the Docker volume.
DB_PATH = Path(os.environ.get("RADAR_DB_PATH", "data/radar.db"))

# Grid is fixed at 32x32, valid row/col indices are 0..31.
GRID_SIZE = int(os.environ.get("RADAR_GRID_SIZE", "32"))

RADARS = ("A", "B")
