"""FastAPI application entry point: uvicorn app.main:app."""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import DB_PATH
from .database import init_db
from .routes import router


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = Path(DB_PATH)
    init_db(db_path)
    app.state.db_path = db_path
    yield


app = FastAPI(
    title="Radar Rain-Intensity Merge API",
    version=__version__,
    lifespan=lifespan,
    description=(
        "Local API that merges two radars' (A/B) hourly sparse rain-intensity "
        "grids into one warning map. Revisions replace the full previous grid "
        "of one radar/hour; each cell keeps the highest-quality observation "
        "(ties: higher intensity, then radar A)."
    ),
)

app.include_router(router)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    # Never leak stack traces; the service layer already rolls back its work.
    return JSONResponse(status_code=500, content={"detail": "internal server error"})
