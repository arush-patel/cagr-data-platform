from pathlib import Path

from fastapi.testclient import TestClient

from cagr_observatory.app import create_app
from cagr_observatory.settings import Settings


def client(tmp_path: Path, ok: bool = True) -> TestClient:
    settings = Settings(web_dist=tmp_path / "missing")
    return TestClient(create_app(settings, check_database=lambda: (ok, None if ok else "Boom")))


def test_health(tmp_path: Path) -> None:
    body = client(tmp_path).get("/api/health").json()
    assert body["database"] is True and body["version"]


def test_health_reports_database_failure(tmp_path: Path) -> None:
    body = client(tmp_path, ok=False).get("/api/health").json()
    assert body == {"version": body["version"], "database": False, "detail": "Boom"}


def test_serves_ui_for_any_path(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("x")
    (tmp_path / "index.html").write_text("<div id=root></div>")
    app = create_app(Settings(web_dist=tmp_path), check_database=lambda: (True, None))
    c = TestClient(app)
    assert c.get("/review").text == "<div id=root></div>"
    assert c.get("/assets/app.js").text == "x"
    assert c.get("/api/health").json()["database"] is True
