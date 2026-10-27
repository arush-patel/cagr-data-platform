"""The listing builder against the database: raw rows in, ref.listing* out (spec 0002)."""

from datetime import date

import pytest

from cagr_data.core import runs
from cagr_data.core.db import Connection as Conn
from cagr_data.reference.calendar import refresh_calendar
from cagr_data.reference.listing_build import (
    MissingSessionError,
    build,
    built_through,
    check,
)

pytestmark = pytest.mark.db

# 1 Jan 2010 is a Friday and, with no exceptions recorded, a session.
DAYS = [date(2010, 1, 1), date(2010, 1, 4), date(2010, 1, 5), date(2010, 1, 6)]


def _file(db: Conn, day: date, n: int) -> int:
    row = db.execute(
        "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes, storage_uri,"
        " row_count) VALUES ('nse_cm_bhavcopy_legacy', %s, 'u', %s, 1, 's', 1)"
        " RETURNING file_id",
        (day, f"{n:064x}"),
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def _bars(db: Conn, day: date, rows: list[tuple[str, str, str | None]]) -> None:
    file_id = _file(db, day, day.toordinal())
    for symbol, series, isin in rows:
        db.execute(
            "INSERT INTO raw.nse_cm_bar (file_id, trade_date, symbol, series, isin, close, volume,"
            " source_format) VALUES (%s, %s, %s, %s, %s, 100, 10, 'legacy_v1')",
            (file_id, day, symbol, series, isin),
        )


def _seed(db: Conn) -> None:
    refresh_calendar(db, "XNSE", date(2010, 1, 31))
    _bars(db, DAYS[0], [("RELIANCE", "EQ", None), ("NIFTYBEES", "EQ", None), ("GSEC", "GS", None)])
    _bars(db, DAYS[1], [("RELIANCE", "EQ", None), ("TCS", "EQ", None)])
    _bars(db, DAYS[2], [("RELIANCE", "EQ", "INE002A01018"), ("TCS", "BE", "INE467B01029")])
    _bars(db, DAYS[3], [("RIL", "EQ", "INE002A01018"), ("TCS", "BE", "INE467B01029")])


def test_build_maps_every_row_and_reruns_to_nothing(db: Conn) -> None:
    _seed(db)
    result = build(db, DAYS[-1], commit=lambda: None)
    assert result.sessions == 4
    assert result.new_listings == 3  # RELIANCE, NIFTYBEES, TCS; GS is debt
    assert result.symbol_changes == 1  # RELIANCE to RIL on its ISIN
    assert built_through(db) == DAYS[-1]

    current = db.execute(
        "SELECT symbol, isin, asset_class, board, first_session, last_session"
        " FROM ref.listing_current ORDER BY first_session, symbol"
    ).fetchall()
    assert current == [
        ("NIFTYBEES", None, "equity", "main", DAYS[0], DAYS[0]),
        ("RIL", "INE002A01018", "equity", "main", DAYS[0], DAYS[3]),
        ("TCS", "INE467B01029", "equity", "main", DAYS[1], DAYS[3]),
    ]
    # The first ISIN is dated from the listing's first session.
    reliance = db.execute(
        "SELECT valid_from FROM ref.listing_isin WHERE isin = 'INE002A01018'"
    ).fetchall()
    assert reliance == [(DAYS[0],)]
    by_symbol = "SELECT ref.listing_by_symbol(%s, %s)"
    assert db.execute(by_symbol, ("RELIANCE", DAYS[2])).fetchone() == (
        db.execute(by_symbol, ("RIL", DAYS[3])).fetchone()
    )
    assert db.execute(by_symbol, ("RELIANCE", DAYS[3])).fetchone() == (None,)

    again = build(db, DAYS[-1], commit=lambda: None)
    assert again.sessions == 0

    run_id = runs.start_run(db, "manual")
    failed = check(db, run_id, DAYS[0], DAYS[-1])
    assert failed == []
    passed = dict(
        db.execute(
            "SELECT check_id, passed FROM ops.dq_result WHERE run_id = %s", (run_id,)
        ).fetchall()
    )
    assert passed["listing_rows_mapped"] is True
    assert passed["listing_unique_symbol"] is True
    assert passed["symbol_change_confirmed"] is False  # no symbolchange.csv loaded


def test_build_stops_at_a_session_without_rows(db: Conn) -> None:
    refresh_calendar(db, "XNSE", date(2010, 1, 31))
    _bars(db, DAYS[0], [("RELIANCE", "EQ", None)])
    with pytest.raises(MissingSessionError, match="2010-01-04"):
        build(db, DAYS[-1], commit=lambda: None)
    assert built_through(db) == DAYS[0]


def test_writer_can_build(db: Conn) -> None:
    _seed(db)
    db.execute("SET ROLE cagr_writer")
    assert build(db, DAYS[-1], commit=lambda: None).sessions == 4
