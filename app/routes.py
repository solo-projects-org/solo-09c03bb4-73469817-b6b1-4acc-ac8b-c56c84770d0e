"""HTTP routes."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from .database import db_session
from .models import (
    ErrorBody,
    MergeResponse,
    ReportAccepted,
    ReportRequest,
)
from .services import (
    RevisionConflictError,
    StaleRevisionError,
    get_merged_grid,
    submit_report,
)
from .time_utils import HourValidationError, canonical_hour, normalize_utc_hour

router = APIRouter()


def get_conn(request: Request):
    """Per-request SQLite connection (sync app, so it is reused safely)."""
    with db_session(request.app.state.db_path) as conn:
        yield conn


@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")


@router.get("/health", tags=["ops"])
def health():
    return {"status": "ok"}


@router.post(
    "/reports",
    response_model=ReportAccepted,
    status_code=201,
    tags=["reports"],
    responses={
        409: {"model": ErrorBody, "description": "Stale revision or same-revision content conflict"},
    },
    summary="Submit one full sparse-grid report for a radar/hour",
)
def post_report(payload: ReportRequest, conn=Depends(get_conn)):
    # Whole-report validation: duplicated (row, col) rejects the entire report
    # before any database work begins (nothing is partially written).
    seen: set[tuple[int, int]] = set()
    for point in payload.points:
        key = (point.row, point.col)
        if key in seen:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"duplicate cell (row={point.row}, col={point.col}) in report: "
                    "the whole report is rejected"
                ),
            )
        seen.add(key)

    try:
        hour_key = canonical_hour(payload.hour)
    except HourValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    try:
        result = submit_report(
            conn=conn,
            radar=payload.radar.value,
            hour_key=hour_key,
            revision=payload.revision,
            points=payload.points,
        )
    except StaleRevisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RevisionConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


@router.get(
    "/merge",
    response_model=MergeResponse,
    tags=["merge"],
    summary="Merged grid for one UTC hour (best quality per cell)",
)
def get_merge(hour: datetime, conn=Depends(get_conn)):
    try:
        hour_key = canonical_hour(normalize_utc_hour(hour))
    except HourValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return get_merged_grid(conn, hour_key)
