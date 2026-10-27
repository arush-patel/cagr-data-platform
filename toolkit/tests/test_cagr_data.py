"""cagr.data against the platform's real schema: every migration applied, read as cagr_read.

The rows are written straight into ref and clean (the platform's builders have their own
tests), so these check the read contract of specs 0002, 0003 and 0007.
"""

import os
from collections.abc import Iterator
from datetime import date
from urllib.parse import urlsplit, urlunsplit

import polars as pl
import psycopg
import pytest
from psycopg import sql

from cagr import data, db, fs, ind
from cagr_data.core import migrate
from cagr_data.reference.calendar import refresh_calendar
from cagr_data.reference.listing_build import PgStore
from cagr_data.reference.listings import Book, Row

pytestmark = pytest.mark.db
NAME = "cagr_toolkit_data_test"
PASSWORD = "cagr_platform_test"  # test-only login, as in the platform's fixtures

DAYS = [date(2024, 1, d) for d in (1, 2, 3, 4, 5, 8)]
ALPHA = [100, 102, 51, 52, 50, 53]  # a 2:1 split on 3 Jan, applied
BETA = {1: 10.0, 2: 11.0, 4: 12.0, 5: 6.0, 8: 6.5}  # no trade on 3 Jan; a pending split on 5 Jan


def _url(admin: str, db_name: str, user: str | None = None) -> str:
    parts = urlsplit(admin)
    netloc = parts.netloc
    if user:
        host = parts.hostname or "localhost"
        netloc = f"{user}:{PASSWORD}@{host}" + (f":{parts.port}" if parts.port else "")
    return urlunsplit(parts._replace(netloc=netloc, path="/" + db_name))


def _seed(conn: psycopg.Connection[tuple[object, ...]]) -> dict[str, int]:
    refresh_calendar(conn, "XNSE", date(2024, 1, 31))
    book = Book(PgStore(conn))
    rows = [Row("ALPHA", "EQ", "INE002A01018"), Row("BETA", "EQ", "INE003B01014")]
    mapped = book.apply_session(DAYS[0], 1, rows, [], {}).mapped
    ids = {symbol: lid for (symbol, _), lid in mapped.items()}
    file_id = conn.execute(
        "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes, storage_uri,"
        " row_count) VALUES ('nse_cm_bhavcopy_legacy', '2024-01-01', 'u', repeat('a', 64), 1,"
        " 's', 1) RETURNING file_id"
    ).fetchone()
    assert file_id is not None
    bars = [(ids["ALPHA"], d, c) for d, c in zip(DAYS, ALPHA, strict=True)]
    bars += [(ids["BETA"], date(2024, 1, d), c) for d, c in BETA.items()]
    for lid, day, close in bars:
        conn.execute(
            "INSERT INTO clean.bar_1d (listing_id, date, open, high, low, close, volume,"
            " value_traded, trade_count, series, source_file_id)"
            " VALUES (%s, %s, %s, %s, %s, %s, 1000, %s, 10, 'EQ', %s)",
            (lid, day, close, close + 1, close - 1, close, close * 1000, file_id[0]),
        )
    conn.execute(
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, factor, status, auto,"
        " proposed_by, applied_at) VALUES (%s, '2024-01-03', 0.5, 'applied', true, 'rules', now()),"
        " (%s, '2024-01-05', NULL, 'pending', false, 'rules', NULL)",
        (ids["ALPHA"], ids["BETA"]),
    )
    conn.execute(
        "INSERT INTO clean.adjustment_step VALUES (%s, '-infinity', '2024-01-03', 0.5)",
        (ids["ALPHA"],),
    )
    conn.execute(
        "INSERT INTO clean.bar_1w (listing_id, period_start, period_end, open, high, low, close,"
        " volume, value_traded, trade_count, session_count, adj_open, adj_high, adj_low,"
        " adj_close, adj_volume, complete, adjustment_state) VALUES"
        " (%s, '2024-01-01', '2024-01-05', 100, 103, 49, 50, 5000, 1, 50, 5, 50, 51.5, 49, 50,"
        "  7000, true, 1),"
        " (%s, '2024-01-01', '2024-01-05', 10, 13, 5, 6, 4000, 1, 40, 4, NULL, NULL, NULL, NULL,"
        "  NULL, true, 1)",
        (ids["ALPHA"], ids["BETA"]),
    )
    conn.execute(
        "INSERT INTO ref.listing_shares (listing_id, valid_from, shares, source)"
        " VALUES (%s, '2024-01-01', 1000, 'nse_mcap')",
        (ids["ALPHA"],),
    )
    return ids


@pytest.fixture(scope="module")
def setup() -> Iterator[tuple[str, dict[str, int]]]:
    admin = os.environ.get("CAGR_TEST_DATABASE_URL")
    if not admin:
        pytest.skip("CAGR_TEST_DATABASE_URL is not set")
    with psycopg.connect(admin, autocommit=True) as conn:
        for role in ("cagr_platform", "cagr_writer", "cagr_read", "cagr_reviewer"):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
        conn.execute(
            sql.SQL("ALTER ROLE cagr_platform LOGIN PASSWORD {}").format(sql.Literal(PASSWORD))
        )
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(NAME))
        )
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER cagr_platform").format(sql.Identifier(NAME)))
    target = _url(admin, NAME)
    with psycopg.connect(target, autocommit=True) as conn:
        conn.execute("ALTER SCHEMA public OWNER TO CURRENT_USER")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    migrate.apply(_url(admin, NAME, user="cagr_platform"))
    with psycopg.connect(target) as conn:
        ids = _seed(conn)
    yield target, ids


@pytest.fixture
def conn(setup: tuple[str, dict[str, int]]) -> Iterator[db.Connection]:
    with db.connect_read(setup[0]) as c:
        c.execute("SET ROLE cagr_read")  # what the toolkit's login may read
        yield c


@pytest.fixture
def ids(setup: tuple[str, dict[str, int]]) -> dict[str, int]:
    return setup[1]


def test_listings_and_lookup(conn: db.Connection, ids: dict[str, int]) -> None:
    found = data.listings(conn, date(2024, 1, 5))
    assert found["symbol"].to_list() == ["ALPHA", "BETA"]
    assert found.schema["listing_id"] == pl.Int64
    assert data.listings(conn, date(2023, 12, 29)).height == 0  # not yet listed
    assert data.listings(conn, date(2024, 1, 5), asset_classes=["index"]).height == 0
    assert data.lookup(conn, ["BETA"], date(2024, 1, 5)) == {"BETA": ids["BETA"]}
    with pytest.raises(KeyError, match="GAMMA"):
        data.lookup(conn, ["ALPHA", "GAMMA"], date(2024, 1, 5))


def test_adjusted_bars_and_the_hold(conn: db.Connection, ids: dict[str, int]) -> None:
    bars = data.bars(conn, DAYS[0], DAYS[-1])
    alpha = bars.filter(pl.col("listing_id") == ids["ALPHA"])
    assert alpha["close"].to_list() == [50.0, 51.0, 51.0, 52.0, 50.0, 53.0]
    assert alpha["volume"].to_list()[:2] == [2000.0, 2000.0]
    beta = bars.filter(pl.col("listing_id") == ids["BETA"])
    assert beta["date"].to_list() == [date(2024, 1, d) for d in (1, 2, 4)]  # held from 5 Jan
    assert data.holds(conn).to_dicts() == [
        {"listing_id": ids["BETA"], "held_from": date(2024, 1, 5), "adjustment_id": 2}
    ]
    assert data.adjustment_state(conn) == 1


def test_unadjusted_bars_are_complete(conn: db.Connection, ids: dict[str, int]) -> None:
    bars = data.bars(conn, DAYS[0], DAYS[-1], listings=[ids["BETA"]], adjusted=False)
    assert bars["close"].to_list() == list(BETA.values())
    assert "adj_factor" not in bars.columns
    assert bars.schema["close"] == pl.Float64


def test_warmup_reads_bars_before_start(conn: db.Connection, ids: dict[str, int]) -> None:
    bars = data.bars(conn, DAYS[3], DAYS[-1], listings=[ids["ALPHA"]], warmup=2)
    assert bars["date"].to_list() == DAYS[1:]
    empty = data.bars(conn, date(2025, 1, 1), date(2025, 1, 31), listings=[ids["ALPHA"]])
    assert empty.height == 0 and empty.schema["close"] == pl.Float64


def test_weekly_bars(conn: db.Connection, ids: dict[str, int]) -> None:
    adjusted = data.bars(conn, DAYS[0], DAYS[-1], timeframe="1w")
    assert adjusted.select("listing_id", "date", "close").to_dicts() == [
        {"listing_id": ids["ALPHA"], "date": date(2024, 1, 5), "close": 50.0}
    ]  # BETA's week has a held day, so it has no adjusted bar
    raw = data.bars(conn, DAYS[0], DAYS[-1], timeframe="1w", adjusted=False)
    assert raw["close"].to_list() == [50.0, 6.0] and raw["complete"].all()
    with pytest.raises(ValueError, match="timeframe"):
        data.bars(conn, DAYS[0], DAYS[-1], timeframe="1y")  # type: ignore[arg-type]


def test_market_cap_and_classification(conn: db.Connection, ids: dict[str, int]) -> None:
    caps = data.market_cap(conn, DAYS[0], DAYS[1])
    assert caps["market_cap_inr"].to_list() == [100_000.0, 102_000.0]
    assert caps["listing_id"].unique().to_list() == [ids["ALPHA"]]
    classes = data.classification(conn, DAYS[0])
    assert classes["listing_id"].to_list() == [ids["ALPHA"], ids["BETA"]]


def test_preview(conn: db.Connection) -> None:
    @fs.feature(name="ret_2", version=1, inputs=["close"], min_bars=3)
    def ret_2() -> pl.Expr:
        return ind.returns(2)

    values = data.preview(ret_2, conn, ["ALPHA"], DAYS[3], DAYS[-1])
    assert values.columns == ["listing_id", "symbol", "date", "ret_2@1"]
    assert values["date"].to_list() == DAYS[3:]
    assert values["ret_2@1"].to_list() == pytest.approx([52 / 51 - 1, 50 / 51 - 1, 53 / 52 - 1])
