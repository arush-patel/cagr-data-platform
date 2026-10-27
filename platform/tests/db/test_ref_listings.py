"""The listing tables of migration 0007 (spec 0002, sections 6 and 9)."""

from datetime import date

import psycopg
import pytest
from psycopg import errors

pytestmark = pytest.mark.db

Conn = psycopg.Connection[tuple[object, ...]]


def _listing(db: Conn, symbol: str, isin: str, start: date, **kw: object) -> int:
    row = db.execute(
        "INSERT INTO ref.listing (venue_id, asset_class, board, first_session, last_session)"
        " VALUES ('XNSE', %s, %s, %s, %s) RETURNING listing_id",
        (kw.get("asset_class", "equity"), kw.get("board", "main"), start, start),
    ).fetchone()
    assert row is not None
    listing_id = int(str(row[0]))
    change = db.execute(
        "INSERT INTO ref.listing_change (kind, listing_id, effective_date, status, auto,"
        " proposed_by) VALUES ('new_listing', %s, %s, 'applied', true, 'rules')"
        " RETURNING change_id",
        (listing_id, start),
    ).fetchone()
    assert change is not None
    db.execute(
        "INSERT INTO ref.listing_symbol VALUES (%s, %s, %s, NULL, %s)",
        (listing_id, symbol, start, change[0]),
    )
    db.execute(
        "INSERT INTO ref.listing_isin VALUES (%s, %s, %s, NULL, %s)",
        (listing_id, isin, start, change[0]),
    )
    return listing_id


def _rename(db: Conn, listing_id: int, symbol: str, on: date) -> None:
    change = db.execute(
        "INSERT INTO ref.listing_change (kind, listing_id, effective_date, status, auto,"
        " proposed_by) VALUES ('symbol_change', %s, %s, 'applied', true, 'rules')"
        " RETURNING change_id",
        (listing_id, on),
    ).fetchone()
    assert change is not None
    db.execute(
        "UPDATE ref.listing_symbol SET valid_to = %s WHERE listing_id = %s AND valid_to IS NULL",
        (on, listing_id),
    )
    db.execute(
        "INSERT INTO ref.listing_symbol VALUES (%s, %s, %s, NULL, %s)",
        (listing_id, symbol, on, change[0]),
    )


def test_a_rename_keeps_the_listing(db: Conn) -> None:
    zomato = _listing(db, "ZOMATO", "INE758T01015", date(2021, 7, 23))
    _rename(db, zomato, "ETERNAL", date(2025, 3, 20))
    by = "SELECT ref.listing_by_symbol(%s, %s)"
    assert db.execute(by, ("ZOMATO", date(2025, 3, 19))).fetchone() == (zomato,)
    assert db.execute(by, ("ZOMATO", date(2025, 3, 20))).fetchone() == (None,)
    assert db.execute(by, ("ETERNAL", date(2025, 3, 20))).fetchone() == (zomato,)
    as_of = db.execute(
        "SELECT symbol, isin FROM ref.listings_as_of(%s) WHERE listing_id = %s",
        (date(2024, 1, 1), zomato),
    ).fetchall()
    assert as_of == [("ZOMATO", "INE758T01015")]
    current = db.execute(
        "SELECT symbol, isin, status FROM ref.listing_current WHERE listing_id = %s", (zomato,)
    ).fetchall()
    assert current == [("ETERNAL", "INE758T01015", "active")]


def test_listings_as_of_skips_the_not_yet_listed_and_the_merged(db: Conn) -> None:
    old = _listing(db, "OLDCO", "INE000A01011", date(2010, 1, 4))
    new = _listing(db, "NEWCO", "INE000B01011", date(2020, 1, 1))
    db.execute(
        "UPDATE ref.listing SET status = 'merged', merged_into = %s WHERE listing_id = %s",
        (new, old),
    )
    rows = db.execute(
        "SELECT listing_id FROM ref.listings_as_of(%s) WHERE listing_id IN (%s, %s)",
        (date(2015, 1, 1), old, new),
    ).fetchall()
    assert rows == []


def test_a_listing_has_one_symbol_at_a_time(db: Conn) -> None:
    listing = _listing(db, "ABC", "INE000C01011", date(2020, 1, 1))
    change = db.execute("SELECT max(change_id) FROM ref.listing_change").fetchone()
    assert change is not None
    with pytest.raises(errors.ExclusionViolation):
        db.execute(
            "INSERT INTO ref.listing_symbol VALUES (%s, 'ABCD', %s, NULL, %s)",
            (listing, date(2022, 1, 1), change[0]),
        )


def test_someone_else_decides(db: Conn) -> None:
    listing = _listing(db, "XYZ", "INE000D01011", date(2020, 1, 1))
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO ref.listing_change (kind, listing_id, effective_date, status, auto,"
            " proposed_by, decided_by, decided_at)"
            " VALUES ('merge', %s, %s, 'applied', false, 'arush', 'arush', now())",
            (listing, date(2021, 1, 1)),
        )


def test_an_index_has_no_board(db: Conn) -> None:
    with pytest.raises(errors.CheckViolation):
        db.execute(
            "INSERT INTO ref.listing (venue_id, asset_class, board, first_session, last_session)"
            " VALUES ('XNSE', 'index', 'main', '2012-02-21', '2012-02-21')"
        )


def test_one_live_adjustment_per_ex_date(db: Conn) -> None:
    listing = _listing(db, "RELIANCE", "INE002A01018", date(2010, 1, 4))
    insert = (
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, factor, status, auto,"
        " proposed_by, applied_at) VALUES (%s, '2024-10-28', 0.5, %s, true, 'rules', %s)"
    )
    db.execute(insert, (listing, "applied", "2026-10-27"))
    with pytest.raises(errors.UniqueViolation):
        db.execute(insert, (listing, "applied", "2026-10-27"))


def test_index_aliases_seeded(db: Conn) -> None:
    rows = db.execute("SELECT name, canonical FROM ref.index_alias ORDER BY name").fetchall()
    assert rows == [("CNX Nifty", "Nifty 50"), ("S&P CNX Nifty", "Nifty 50")]


def test_writer_builds_listings_but_never_deletes_them(db: Conn) -> None:
    db.execute("SET ROLE cagr_writer")
    _listing(db, "WRT", "INE000E01011", date(2020, 1, 1))
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("DELETE FROM ref.listing")


def test_reader_sees_the_stock_master(db: Conn) -> None:
    db.execute("SET ROLE cagr_read")
    db.execute("SELECT * FROM ref.listing_current")
    db.execute("SELECT * FROM ref.listings_as_of(current_date)")
    with pytest.raises(errors.InsufficientPrivilege):
        db.execute("INSERT INTO ref.index_alias VALUES ('x', 'y')")
