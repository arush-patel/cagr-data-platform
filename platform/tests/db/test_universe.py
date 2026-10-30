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
    """BIG and SMALL trade in EQ, BIG once in BE; ONLYBE never trades in EQ; TINY is on SME.
    BIG trades ₹100 crore a day, the rest ₹10 crore."""
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
                "INSERT INTO clean.bar_1d (listing_id, date, close, value_traded, series,"
                " source_file_id) VALUES (%s, %s, 100, %s, %s, %s)",
                (ids[symbol], day, (100 if symbol == "BIG" else 10) * CRORE, s, file_id[0]),
            )
    # BIG is worth ₹2,000 crore at a close of 100; the rest ₹100 crore.
    for symbol in series:
        shares = 2_000 * CRORE // 100 if symbol == "BIG" else 100 * CRORE // 100
        db.execute(
            "INSERT INTO ref.listing_shares (listing_id, valid_from, shares, source)"
            " VALUES (%s, '2024-01-01', %s, 'walk_back')",
            (ids[symbol], shares),
        )
    db.execute(f"SET cagr.test_file_id = {int(file_id[0])}")
    return ids


def _pending(db: Conn, listing_id: int, ex_date: date, observed: float = 0.5,
             note: str = "price jump, no action explains it") -> int:  # fmt: skip
    row = db.execute(
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, observed_factor, status, auto,"
        " proposed_by, note) VALUES (%s, %s, %s, 'pending', false, 'rules', %s)"
        " RETURNING adjustment_id",
        (listing_id, ex_date, observed, note),
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
    db.execute("SET ROLE cagr_writer")  # as the pipeline runs it
    universe.build_tracked(db, [])
    assert universe.scope_adjustments(db, floor_inr=1_500 * CRORE) == (4, 0)
    assert _status(db, big_eq) == ("pending", None, "price jump, no action explains it")
    for item in (big_be, small, only_be, tiny):
        assert _status(db, item) == ("out_of_scope", "scope", "price jump, no action explains it")
    assert universe.scope_adjustments(db, floor_inr=1_500 * CRORE) == (0, 0)  # a rerun is a no-op


def test_a_wider_scope_brings_items_back(db: Conn) -> None:
    ids = _seed(db)
    small = _pending(db, ids["SMALL"], DAYS[1])
    db.execute("SET ROLE cagr_writer")
    universe.build_tracked(db, [])
    universe.scope_adjustments(db, floor_inr=1_500 * CRORE)
    assert universe.scope_adjustments(db, floor_inr=50 * CRORE) == (0, 1)
    assert _status(db, small) == ("pending", None, "price jump, no action explains it")


def _action(db: Conn, listing_id: int, kind: str, parsed: bool = True) -> None:
    db.execute(
        "INSERT INTO ref.corporate_action (listing_id, ex_date, subject, seq, kind,"
        " parse_status, source_file_id) VALUES (%s, %s, %s, 1, %s, %s,"
        " current_setting('cagr.test_file_id')::bigint)",
        (listing_id, DAYS[1], kind, kind, "parsed" if parsed else "unparsed"),
    )


def _reason(db: Conn, adjustment_id: int) -> object:
    universe.build_tracked(db, [])
    universe.scope_adjustments(db, floor_inr=1_500 * CRORE)
    row = db.execute(
        "SELECT scope_reason FROM ref.price_adjustment WHERE adjustment_id = %s",
        (adjustment_id,),
    ).fetchone()
    assert row is not None
    return row[0]


@pytest.mark.parametrize(
    ("observed", "reason"),
    [(0.64, "real_move"),  # POLICYBZR's crash, near 2:3 but nothing announced
     (1.45, "real_move"),
     (0.101, None),  # a drop to a tenth: an unannounced split stays for review
     (0.5, None)],
)  # fmt: skip
def test_a_jump_with_no_action_is_a_real_move(db: Conn, observed: float, reason: object) -> None:
    ids = _seed(db)
    assert _reason(db, _pending(db, ids["BIG"], DAYS[1], observed)) == reason


def test_an_isin_change_keeps_a_jump(db: Conn) -> None:
    ids = _seed(db)
    db.execute("UPDATE ref.listing_isin SET valid_to = %s WHERE listing_id = %s",
               (DAYS[1], ids["BIG"]))  # fmt: skip
    db.execute("INSERT INTO ref.listing_isin (listing_id, isin, valid_from, change_id)"
               " SELECT listing_id, 'INE000B01029', %s, change_id FROM ref.listing_isin"
               " WHERE listing_id = %s", (DAYS[1], ids["BIG"]))  # fmt: skip
    assert _reason(db, _pending(db, ids["BIG"], DAYS[1], 0.64)) is None


@pytest.mark.parametrize(
    ("observed", "reason"),
    [(0.973, "small_demerger"), (0.92, None)],  # TVSHLTD; Jio Financial
)
def test_a_small_demerger_is_not_priced(db: Conn, observed: float, reason: object) -> None:
    ids = _seed(db)
    _action(db, ids["BIG"], "demerger")
    note = "demerger: confirm the factor from prices"
    assert _reason(db, _pending(db, ids["BIG"], DAYS[1], observed, note)) == reason


@pytest.mark.parametrize(
    ("kind", "observed", "reason"),
    [("dividend", 0.99, "unparsed_dividend"), ("dividend", 0.9, None), ("split", 0.99, None)],
)
def test_an_unparsed_dividend_without_a_drop_is_harmless(
    db: Conn, kind: str, observed: float, reason: object
) -> None:
    ids = _seed(db)
    _action(db, ids["BIG"], kind, parsed=False)
    note = "an action on this date is unparsed"
    assert _reason(db, _pending(db, ids["BIG"], DAYS[1], observed, note)) == reason


@pytest.mark.parametrize(("kind", "reason"), [("rights", "illiquid"), ("split", None)])
def test_an_illiquid_stock_is_set_aside_unless_its_shares_changed(
    db: Conn, kind: str, reason: object
) -> None:
    ids = _seed(db)
    db.execute("UPDATE clean.bar_1d SET value_traded = 2 * %s WHERE listing_id = %s",
               (CRORE, ids["BIG"]))  # fmt: skip
    _action(db, ids["BIG"], kind)
    assert _reason(db, _pending(db, ids["BIG"], DAYS[1], 0.8, "the close disagrees")) == reason


def test_illiquid_merges_are_set_aside(db: Conn) -> None:
    ids = _seed(db)
    change = db.execute(
        "INSERT INTO ref.listing_change (kind, listing_id, effective_date, payload, status,"
        " auto, proposed_by) VALUES ('merge', %s, %s, jsonb_build_object('into_id', %s::int),"
        " 'pending', false, 'rules') RETURNING change_id",
        (ids["SMALL"], DAYS[0], ids["TINY"]),
    ).fetchone()
    assert change is not None
    assert universe.scope_merges(db, liquid_inr=50 * CRORE) == (1, 0)  # both trade ₹10 crore
    row = db.execute("SELECT status, scope_reason, decided_by FROM ref.listing_change"
                     " WHERE change_id = %s", (change[0],)).fetchone()  # fmt: skip
    assert row == ("out_of_scope", "illiquid", "scope")
    assert universe.scope_merges(db, liquid_inr=5 * CRORE) == (0, 1)  # a lower floor: back


def test_the_index_list_reads_names_and_skips_comments(tmp_path: Path) -> None:
    path = tmp_path / "indices.txt"
    path.write_text("# header\n\nNifty 50\nNifty Oil & Gas  # sector\n")
    assert universe.read_index_names(path) == ["Nifty 50", "Nifty Oil & Gas"]


def test_every_listed_index_name_is_unique() -> None:
    names = universe.read_index_names()
    assert len(names) == len(set(names))
    assert "Nifty 50" in names
