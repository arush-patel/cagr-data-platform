from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from cagr_observatory.app import create_app
from cagr_observatory.settings import Settings


def ok() -> MagicMock:
    return MagicMock()


def failing() -> MagicMock:
    raise RuntimeError("CAGR_READ_DATABASE_URL is not set")


def client(tmp_path: Path, connect: Callable[[], MagicMock] = ok) -> TestClient:
    return TestClient(create_app(Settings(web_dist=tmp_path / "missing"), connect=connect))


def test_health(tmp_path: Path) -> None:
    body = client(tmp_path).get("/api/health").json()
    assert body["database"] is True and body["version"]


def test_health_reports_database_failure(tmp_path: Path) -> None:
    body = client(tmp_path, failing).get("/api/health").json()
    assert (body["database"], body["detail"], body["reviewer"]) == (
        False, "CAGR_READ_DATABASE_URL is not set", None
    )  # fmt: skip


def test_a_missing_setting_is_a_503_in_words(tmp_path: Path) -> None:
    response = client(tmp_path, failing).get("/api/review/items")
    assert response.status_code == 503
    assert response.json() == {"detail": "CAGR_READ_DATABASE_URL is not set"}


def test_serves_ui_for_any_path_but_api(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("x")
    (tmp_path / "index.html").write_text("<div id=root></div>")
    c = TestClient(create_app(Settings(web_dist=tmp_path), connect=ok))
    assert c.get("/review").text == "<div id=root></div>"
    assert c.get("/assets/app.js").text == "x"
    assert c.get("/api/health").json()["database"] is True
    assert c.get("/api/nothing").status_code == 404


def test_the_ui_types_are_current() -> None:
    """After an API change: uv run python -m cagr_observatory.openapi, npm run api-types."""
    from cagr_observatory import openapi

    assert openapi.SCHEMA.read_text() == openapi.text()


def test_the_server_reads_the_repository_env() -> None:
    from cagr_observatory.__main__ import REPO_ENV

    assert (REPO_ENV.parent / "pyproject.toml").read_text().startswith("[project]")
    assert (REPO_ENV.parent / "observatory").is_dir()
