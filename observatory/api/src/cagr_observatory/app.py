"""The FastAPI app: JSON under /api, and the built UI everywhere else."""

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cagr import db
from cagr_observatory import __version__
from cagr_observatory.settings import Settings


class Health(BaseModel):
    version: str
    database: bool
    """True when the read login can reach the database."""
    detail: str | None = None


def database_ok() -> tuple[bool, str | None]:
    try:
        with db.connect_read() as conn:
            conn.execute("SELECT 1")
    except Exception as exc:  # any failure means the page can't read
        return False, type(exc).__name__
    return True, None


def create_app(
    settings: Settings | None = None,
    check_database: Callable[[], tuple[bool, str | None]] = database_ok,
) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Observatory", version=__version__)
    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health() -> Health:
        ok, detail = check_database()
        return Health(version=__version__, database=ok, detail=detail)

    app.include_router(api)
    if settings.web_dist.is_dir():
        serve_ui(app, settings.web_dist)
    return app


def serve_ui(app: FastAPI, dist: Path) -> None:
    """Static assets, and index.html for every other path so the UI's routes work on reload."""
    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str) -> FileResponse:
        return FileResponse(index)
