"""Pydantic request/response models."""

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .config import GRID_SIZE


class Radar(str, Enum):
    A = "A"
    B = "B"


class GridPoint(BaseModel):
    """One sparse observation inside a report."""

    model_config = ConfigDict(extra="forbid")

    row: int = Field(ge=0, lt=GRID_SIZE, description="Grid row index, 0..31")
    col: int = Field(ge=0, lt=GRID_SIZE, description="Grid column index, 0..31")
    rain_intensity: float = Field(
        ge=0, description="Non-negative rain intensity (mm/h)"
    )
    quality: int = Field(ge=0, le=100, description="Integer quality score 0..100")


class ReportRequest(BaseModel):
    """A full sparse-grid report for one radar/hour at one revision."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "radar": "A",
                    "hour": "2026-11-15T08:00:00Z",
                    "revision": 1,
                    "points": [
                        {"row": 0, "col": 1, "rain_intensity": 3.5, "quality": 80},
                        {"row": 10, "col": 12, "rain_intensity": 12.0, "quality": 95},
                    ],
                }
            ]
        },
    )

    radar: Radar
    hour: datetime = Field(
        description="UTC whole hour, timezone-aware, e.g. 2026-11-15T08:00:00Z"
    )
    revision: int = Field(ge=1, description="Strictly increasing per radar/hour")
    points: List[GridPoint] = Field(
        description="Full sparse grid for this revision; supersedes all older points"
    )


class MergedPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row: int
    col: int
    rain_intensity: float
    quality: int
    source: Radar = Field(description="Winning radar after quality/intensity/A rules")


class RadarRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: Optional[int] = Field(
        default=None, description="Latest accepted revision, null if never reported"
    )
    point_count: int = Field(description="Number of live points in that revision")


class MergeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hour: str
    grid_size: int
    radars: dict
    points: List[MergedPoint] = Field(
        description="Merged sparse grid; one winning observation per cell"
    )


class ReportAccepted(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    radar: Radar
    hour: str
    revision: int
    point_count: int
    replaced_revision: Optional[int] = None
    content_hash: str


class ErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: str
