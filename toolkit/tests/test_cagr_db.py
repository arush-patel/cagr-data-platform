import os
from collections.abc import Iterator
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from cagr import db

pytestmark = pytest.mark.db
NAME = "cagr_toolkit_test"


@pytest.fixture(scope="module")
def url() -> Iterator[str]:
    admin = os.environ.get("CAGR_TEST_DATABASE_URL")
    if not admin:
        pytest.skip("CAGR_TEST_DATABASE_URL is not set")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(NAME))
        )
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(NAME)))
    target = urlunsplit(urlsplit(admin)._replace(path="/" + NAME))
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute("CREATE SCHEMA ref")
        conn.execute(
            "CREATE TABLE ref.calendar_session (venue_id text, date date, is_session boolean)"
        )
        conn.execute(
            "INSERT INTO ref.calendar_session VALUES"
            " ('XNSE', '2024-01-01', true), ('XNSE', '2024-01-02', true),"
            " ('XNSE', '2024-01-06', false), ('XBOM', '2024-01-03', true)"
        )
    yield target


def test_connections_are_read_only(url: str) -> None:
    with db.connect_read(url) as conn, pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
        conn.execute("CREATE TABLE ref.nope (x int)")


def test_read_frame(url: str) -> None:
    with db.connect_read(url) as conn:
        frame = db.read_frame(conn, "SELECT 1 AS a, %s::text AS b", ("x",))
        empty = db.read_frame(conn, "SELECT 1 AS a WHERE false")
    assert frame.to_dicts() == [{"a": 1, "b": "x"}]
    assert empty.columns == ["a"] and empty.height == 0


def test_load_calendar(url: str) -> None:
    with db.connect_read(url) as conn:
        cal = db.load_calendar(conn)
        with pytest.raises(ValueError):
            db.load_calendar(conn, "XNYS")
    assert cal.sessions == [date(2024, 1, 1), date(2024, 1, 2)]


def test_settings_read_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAGR_READ_DATABASE_URL", "postgresql://r:secret@localhost/cagr_test")
    settings = db.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.read_database_url is not None and "secret" not in repr(settings)
