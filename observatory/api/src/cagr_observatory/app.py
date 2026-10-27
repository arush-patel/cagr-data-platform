"""The FastAPI app: JSON under /api, and the built UI everywhere else."""

from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cagr import db
from cagr_observatory import __version__
from cagr_observatory.review import routes as review
from cagr_observatory.settings import Settings


class Health(BaseModel):
    version: str
    database: bool
    """True when the read login can reach the database."""
    detail: str | None = None


def create_app(
    settings: Settings | None = None,
    connect: Callable[[], db.Connection] = db.connect_read,
) -> FastAPI:
    """The app; connect opens a read-only connection (the toolkit's, unless a test swaps it)."""
    settings = settings or Settings()
    app = FastAPI(title="Observatory", version=__version__)
    api = APIRouter(prefix="/api")

    @api.get("/health")
    def health() -> Health:
        try:
            with connect() as conn:
                conn.execute("SELECT 1")
        except Exception as exc:  # any failure means the page can't read
            return Health(version=__version__, database=False, detail=type(exc).__name__)
        return Health(version=__version__, database=True)

    api.include_router(review.router(connect))
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
        if path.startswith("api/"):
            raise HTTPException(404)
        return FileResponse(index)
