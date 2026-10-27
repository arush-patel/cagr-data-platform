"""The review API over spec 0002's tables, in a fresh migrated database read as cagr_read."""

import hashlib
import os
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.types.json import Jsonb

from cagr.db import Connection
from cagr_data.core import migrate
from cagr_observatory.app import create_app
from cagr_observatory.settings import Settings

pytestmark = pytest.mark.db
NAME = "cagr_observatory_test"
PASSWORD = "cagr_platform_test"  # test-only login, as in platform/tests/db/conftest.py
EX = date(2024, 2, 6)


def _url(admin: str, user: str | None = None) -> str:
    parts = urlsplit(admin)._replace(path="/" + NAME)
    if user:
        host = parts.hostname or "localhost"
        port = f":{parts.port}" if parts.port else ""
        parts = parts._replace(netloc=f"{user}:{PASSWORD}@{host}{port}")
    return urlunsplit(parts)


def _sessions() -> list[date]:
    days = (date(2024, 1, 1) + timedelta(n) for n in range(91))
    return [d for d in days if d.weekday() < 5]


def _one(conn: psycopg.Connection[Any], query: str, params: Any = None) -> int:
    row = conn.execute(query, params).fetchone()
    assert row is not None
    return int(row[0])


def _listing(conn: psycopg.Connection[Any], symbol: str, isins: list[tuple[str, date]]) -> int:
    first = isins[0][1]
    lid = _one(
        conn,
        "INSERT INTO ref.listing (venue_id, asset_class, board, name, first_session,"
        " last_session) VALUES ('XNSE', 'equity', 'main', %s, %s, '2024-03-29')"
        " RETURNING listing_id",
        (f"{symbol} Ltd", first),
    )
    change = _one(
        conn,
        "INSERT INTO ref.listing_change (kind, listing_id, effective_date, status, auto,"
        " proposed_by) VALUES ('new_listing', %s, %s, 'applied', true, 'rules')"
        " RETURNING change_id",
        (lid, first),
    )
    conn.execute(
        "INSERT INTO ref.listing_symbol VALUES (%s, %s, %s, NULL, %s)", (lid, symbol, first, change)
    )
    for n, (isin, start) in enumerate(isins):
        end = isins[n + 1][1] if n + 1 < len(isins) else None
        conn.execute(
            "INSERT INTO ref.listing_isin VALUES (%s, %s, %s, %s, %s)",
            (lid, isin, start, end, change),
        )
    return lid


def _fill(conn: psycopg.Connection[Any]) -> dict[str, int]:
    sessions = _sessions()
    conn.cursor().executemany(
        "INSERT INTO ref.calendar_session (venue_id, date, is_session, session_index, reason)"
        " VALUES ('XNSE', %s, true, %s, 'regular')",
        [(d, n + 1) for n, d in enumerate(sessions)],
    )
    kaush = _listing(conn, "KAUSH", [("INE000K01011", date(2024, 1, 1)), ("INE000K01029", EX)])
    old = _listing(conn, "OLDCO", [("INE000A01011", date(2024, 1, 1))])
    new = _listing(conn, "NEWCO", [("INE000B01011", date(2024, 3, 1))])
    file_id = 0
    for d in sessions:
        file_id = _one(
            conn,
            "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes,"
            " storage_uri, row_count) VALUES ('nse_cm_bhavcopy_udiff', %s, 'u', %s, 1, 's', 2)"
            " RETURNING file_id",
            (d, hashlib.sha256(str(d).encode()).hexdigest()),
        )
        close = 41.85 if d >= EX else 0.42
        conn.execute(
            "INSERT INTO raw.nse_cm_bar (file_id, trade_date, symbol, series, isin, close,"
            " source_format) VALUES (%s, %s, 'KAUSH', 'EQ', NULL, %s, 'udiff_v1'),"
            " (%s, %s, 'KAUSH', 'T0', NULL, 999, 'udiff_v1')",
            (file_id, d, close, file_id, d),
        )
    action = _one(
        conn,
        "INSERT INTO ref.corporate_action (listing_id, ex_date, kind, ratio_new, ratio_old,"
        " subject, parse_status, source_file_id) VALUES (%s, '2024-03-01', 'bonus', 1, 1,"
        " 'Bonus 1:1', 'parsed', %s) RETURNING action_id",
        (old, file_id),
    )
    jump = _one(
        conn,
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, factor, observed_factor,"
        " close_before, close_on, status, auto, proposed_by, created_at)"
        " VALUES (%s, %s, 100, 99.64, 0.42, 41.85, 'pending', false, 'rules', %s)"
        " RETURNING adjustment_id",
        (kaush, EX, EX),
    )
    bonus = _one(
        conn,
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, factor, expected_factor,"
        " observed_factor, action_id, status, auto, proposed_by, decided_by, decided_at,"
        " note, created_at) VALUES (%s, '2024-03-01', 0.5, 0.5, 0.97, %s, 'rejected', false,"
        " 'rules', 'arush', '2024-03-04', 'not a bonus', '2024-03-01') RETURNING adjustment_id",
        (old, action),
    )
    merge = _one(
        conn,
        "INSERT INTO ref.listing_change (kind, listing_id, effective_date, evidence, status,"
        " auto, proposed_by, created_at) VALUES ('new_listing', %s, '2024-03-01', %s,"
        " 'pending', false, 'rules', '2024-03-01') RETURNING change_id",
        (new, Jsonb({"rule": "5.2.2", "session": "2024-03-01", "candidates": [old]})),
    )
    return {"kaush": kaush, "old": old, "new": new, "jump": jump, "bonus": bonus, "merge": merge}


@pytest.fixture(scope="module")
def admin() -> str:
    url = os.environ.get("CAGR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CAGR_TEST_DATABASE_URL is not set")
    return url


@pytest.fixture(scope="module")
def ids(admin: str) -> dict[str, int]:
    with psycopg.connect(admin, autocommit=True) as conn:
        for role, login in (("cagr_platform", True), ("cagr_writer", False), ("cagr_read", False)):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
            if login:
                conn.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(PASSWORD)
                    )
                )
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(NAME))
        )
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER cagr_platform").format(sql.Identifier(NAME)))
    with psycopg.connect(_url(admin), autocommit=True) as conn:
        conn.execute("ALTER SCHEMA public OWNER TO CURRENT_USER")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    migrate.apply(_url(admin, "cagr_platform"))
    with psycopg.connect(_url(admin)) as conn:
        return _fill(conn)


@pytest.fixture(scope="module")
def client(
    admin: str, ids: dict[str, int], tmp_path_factory: pytest.TempPathFactory
) -> Iterator[TestClient]:
    def connect() -> Connection:
        conn = psycopg.connect(_url(admin), autocommit=True)
        conn.execute("SET ROLE cagr_read")  # read with exactly the grants the toolkit's login has
        conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
        return conn

    dist = Path(tmp_path_factory.mktemp("none")) / "dist"
    with TestClient(create_app(Settings(web_dist=dist), connect=connect)) as c:
        yield c


def test_queue_lists_pending_items_oldest_first(client: TestClient, ids: dict[str, int]) -> None:
    items = client.get("/api/review/items").json()
    assert [(i["kind"], i["id"]) for i in items] == [
        ("adjustment", ids["jump"]),
        ("change", ids["merge"]),
    ]
    jump, merge = items
    assert jump["symbol"] == "KAUSH" and jump["title"].startswith("Unexplained jump")
    assert "The ISIN changed" in jump["reason"]
    assert jump["waiting_sessions"] == 38 and jump["overdue"] is True
    assert merge["title"] == "Possible merge" and "OLDCO" in merge["reason"]


def test_history_lists_what_a_person_decided(client: TestClient, ids: dict[str, int]) -> None:
    items = client.get("/api/review/items", params={"status": "decided"}).json()
    assert [(i["id"], i["status"], i["decided_by"]) for i in items] == [
        (ids["bonus"], "rejected", "arush")
    ]
    assert items[0]["title"] == "Bonus 1:1" and items[0]["overdue"] is False


def test_adjustment_detail_has_the_chart(client: TestClient, ids: dict[str, int]) -> None:
    item = client.get(f"/api/review/items/adjustment/{ids['jump']}").json()
    assert (item["isin_before"], item["isin_on"]) == ("INE000K01011", "INE000K01029")
    assert item["chart_factor"] == 100 and item["action"] is None
    bars = item["bars"]
    assert len(bars) == 40  # 20 sessions either side; T0 rows never count
    before = [b for b in bars if b["date"] < str(EX)]
    assert len(before) == 20 and before[-1] == {
        "date": "2024-02-05",
        "close": 0.42,
        "adjusted": 42.0,
    }
    assert bars[20] == {"date": str(EX), "close": 41.85, "adjusted": 41.85}


def test_change_detail_shows_both_listings(client: TestClient, ids: dict[str, int]) -> None:
    item = client.get(f"/api/review/items/change/{ids['merge']}").json()
    assert [listing["symbol"] for listing in item["listings"]] == ["NEWCO", "OLDCO"]
    assert item["evidence"]["rule"] == "5.2.2"


def test_unknown_item_is_404(client: TestClient) -> None:
    assert client.get("/api/review/items/change/999999").status_code == 404


def test_listing_search(client: TestClient) -> None:
    found = client.get("/api/listings/search", params={"q": "kau"}).json()
    assert [f["symbol"] for f in found] == ["KAUSH"]
    assert (
        client.get("/api/listings/search", params={"q": "INE000A01011"}).json()[0]["symbol"]
        == "OLDCO"
    )
