"""The tracked universe and the scoped review queue (spec 0008)."""

from datetime import date
from pathlib import Path

import pytest

from cagr_data.core.db import Connection as Conn
from cagr_data.reference import universe
from cagr_data.reference.listing_build import PgStore
from cagr_data.reference.listings import Book, Row

pytestmark = pytest.mark.db

DAYS = [date(2024, 1, d) for d in (1, 2, 3)]
CRORE = 10_000_000


def _seed(db: Conn) -> dict[str, int]:
    """BIG and SMALL trade in EQ, BIG once in BE; ONLYBE never trades in EQ; TINY is on SME."""
    book = Book(PgStore(db))
    rows = [Row("BIG", "EQ", "INE000B01011"), Row("SMALL", "EQ", "INE000S01011"),
            Row("ONLYBE", "BE", "INE000O01011"), Row("TINY", "SM", "INE000T01011")]  # fmt: skip
    r = book.apply_session(DAYS[0], 1, rows, [], {})
    ids = {symbol: lid for (symbol, _), lid in r.mapped.items()}
    r = book.apply_indices(DAYS[0], 1, ["Nifty 50", "Nifty IT"], [])
    ids |= {name: lid for (name, _), lid in r.mapped.items()}
    file_id = db.execute(
        "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes, storage_uri,"
        " row_count) VALUES ('nse_cm_bhavcopy_legacy', '2024-01-01', 'u', repeat('a', 64), 1,"
        " 's', 1) RETURNING file_id"
    ).fetchone()
    assert file_id is not None
    series = {"BIG": ["EQ", "EQ", "BE"], "SMALL": ["EQ"] * 3, "ONLYBE": ["BE"] * 3,
              "TINY": ["SM"] * 3}  # fmt: skip
    for symbol, by_day in series.items():
        for day, s in zip(DAYS, by_day, strict=True):
            db.execute(
                "INSERT INTO clean.bar_1d (listing_id, date, close, series, source_file_id)"
                " VALUES (%s, %s, 100, %s, %s)",
                (ids[symbol], day, s, file_id[0]),
            )
    # BIG is worth ₹2,000 crore at a close of 100; the rest ₹100 crore.
    for symbol in series:
        shares = 2_000 * CRORE // 100 if symbol == "BIG" else 100 * CRORE // 100
        db.execute(
            "INSERT INTO ref.listing_shares (listing_id, valid_from, shares, source)"
            " VALUES (%s, '2024-01-01', %s, 'walk_back')",
            (ids[symbol], shares),
        )
    return ids


def _pending(db: Conn, listing_id: int, ex_date: date) -> int:
    row = db.execute(
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, observed_factor, status, auto,"
        " proposed_by, note) VALUES (%s, %s, 0.5, 'pending', false, 'rules',"
        " 'price jump, no action explains it') RETURNING adjustment_id",
        (listing_id, ex_date),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _status(db: Conn, adjustment_id: int) -> tuple[object, ...]:
    row = db.execute(
        "SELECT status, decided_by, note FROM ref.price_adjustment WHERE adjustment_id = %s",
        (adjustment_id,),
    ).fetchone()
    assert row is not None
    return tuple(row)


def test_tracks_main_board_eq_listings_and_the_listed_indices(db: Conn) -> None:
    ids = _seed(db)
    assert universe.build_tracked(db, ["Nifty 50"]) == (2, 1)
    tracked = db.execute(
        "SELECT symbol, reason FROM ref.tracked_current ORDER BY symbol"
    ).fetchall()
    assert tracked == [("BIG", "eq_main"), ("Nifty 50", "index"), ("SMALL", "eq_main")]
    # A rebuild replaces the table whole.
    assert universe.build_tracked(db, []) == (2, 0)
    tracked = db.execute("SELECT listing_id FROM ref.tracked_listing").fetchall()
    assert (ids["Nifty 50"],) not in tracked


def test_an_unknown_index_name_stops_the_build(db: Conn) -> None:
    _seed(db)
    universe.build_tracked(db, ["Nifty 50"])
    with pytest.raises(universe.UnknownIndex, match="Nifty Bank"):
        universe.build_tracked(db, ["Nifty 50", "Nifty Bank"])
    row = db.execute("SELECT count(*) FROM ref.tracked_listing").fetchone()
    assert row == (3,)  # the old list stays


def test_only_big_eq_items_stay_pending(db: Conn) -> None:
    ids = _seed(db)
    big_eq = _pending(db, ids["BIG"], DAYS[1])
    big_be = _pending(db, ids["BIG"], DAYS[2])
    small = _pending(db, ids["SMALL"], DAYS[1])
    only_be = _pending(db, ids["ONLYBE"], DAYS[1])
    tiny = _pending(db, ids["TINY"], DAYS[1])
    universe.build_tracked(db, [])
    assert universe.scope_adjustments(db, floor_inr=1_500 * CRORE) == (4, 0)
    assert _status(db, big_eq) == ("pending", None, "price jump, no action explains it")
    for item in (big_be, small, only_be, tiny):
        assert _status(db, item) == ("out_of_scope", "scope", "price jump, no action explains it")
    assert universe.scope_adjustments(db, floor_inr=1_500 * CRORE) == (0, 0)  # a rerun is a no-op


def test_a_wider_scope_brings_items_back(db: Conn) -> None:
    ids = _seed(db)
    small = _pending(db, ids["SMALL"], DAYS[1])
    universe.build_tracked(db, [])
    universe.scope_adjustments(db, floor_inr=1_500 * CRORE)
    assert universe.scope_adjustments(db, floor_inr=50 * CRORE) == (0, 1)
    assert _status(db, small) == ("pending", None, "price jump, no action explains it")


def test_the_index_list_reads_names_and_skips_comments(tmp_path: Path) -> None:
    path = tmp_path / "indices.txt"
    path.write_text("# header\n\nNifty 50\nNifty Oil & Gas  # sector\n")
    assert universe.read_index_names(path) == ["Nifty 50", "Nifty Oil & Gas"]


def test_every_listed_index_name_is_unique() -> None:
    names = universe.read_index_names()
    assert len(names) == len(set(names))
    assert "Nifty 50" in names
