"""ref.corporate_action from raw responses (spec 0002, sections 6.2 and 7.2)."""

from datetime import date

import pytest

from cagr_data.core.db import Connection as Conn
from cagr_data.reference.corporate_actions import build_actions, corporate_action_parsed
from cagr_data.reference.listing_build import PgStore
from cagr_data.reference.listings import Book, Row

pytestmark = pytest.mark.db


def _response(db: Conn, rows: list[tuple[str, str, str, str]], revision: int = 1) -> int:
    """A corporate-actions file for 2024 with rows (symbol, isin, subject, ex_date)."""
    previous = db.execute(
        "SELECT max(file_id) FROM raw.vendor_file WHERE feed_id = 'nse_corporate_actions'"
    ).fetchone()
    file_id = db.execute(
        "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes, storage_uri,"
        " row_count, revision, supersedes_file_id)"
        " VALUES ('nse_corporate_actions', '2024-01-01', 'u', %s, 1, 's', %s, %s, %s)"
        " RETURNING file_id",
        (f"{revision:064x}", len(rows), revision, previous[0] if revision > 1 else None),
    ).fetchone()
    assert file_id is not None and previous is not None
    for n, (symbol, isin, subject, ex_date) in enumerate(rows, start=1):
        db.execute(
            "INSERT INTO raw.nse_corporate_action (file_id, row_no, symbol, series, isin,"
            " subject, ex_date, face_value, board) VALUES (%s, %s, %s, 'EQ', %s, %s, %s, 10,"
            " 'main')",
            (file_id[0], n, symbol, isin, subject, ex_date),
        )
    return int(file_id[0])


def _listings(db: Conn) -> dict[str, int]:
    book = Book(PgStore(db))
    r = book.apply_session(
        date(2024, 1, 1), 1,
        [Row("RELIANCE", "EQ", "INE002A01018"), Row("TCS", "EQ", "INE467B01029")], [], {},
    )  # fmt: skip
    return {symbol: lid for (symbol, _), lid in r.mapped.items()}


def test_actions_are_parsed_mapped_and_rerun_cleanly(db: Conn) -> None:
    ids = _listings(db)
    _response(
        db,
        [
            ("RELIANCE", "INE002A01018", "Bonus 1:1", "2024-10-28"),
            ("TCS", "-", "Interim Dividend - Rs 9 Per Share Special Dividend - Rs 18 Per Share",
             "2024-07-19"),
            ("UNKNOWN", "INE999Z01011", "Bonus 1:1", "2024-05-01"),
        ],
    )  # fmt: skip
    result = build_actions(db)
    assert (result.rows, result.actions, len(result.unmapped)) == (3, 3, 1)
    rows = db.execute(
        "SELECT listing_id, ex_date, kind, seq, ratio_new, ratio_old, cash_per_share"
        " FROM ref.corporate_action ORDER BY ex_date, seq"
    ).fetchall()
    assert [(r[0], r[2], r[3]) for r in rows] == [
        (ids["TCS"], "dividend", 1), (ids["TCS"], "dividend", 2), (ids["RELIANCE"], "bonus", 1),
    ]  # fmt: skip
    assert [r[6] for r in rows[:2]] == [9, 18]
    first_ids = db.execute("SELECT action_id FROM ref.corporate_action ORDER BY 1").fetchall()

    again = build_actions(db)
    assert again.deleted == 0
    assert db.execute("SELECT action_id FROM ref.corporate_action ORDER BY 1").fetchall() == (
        first_ids
    )


def test_a_withdrawn_action_is_deleted_unless_an_adjustment_uses_it(db: Conn) -> None:
    ids = _listings(db)
    _response(
        db,
        [("RELIANCE", "INE002A01018", "Bonus 1:1", "2024-10-28"),
         ("TCS", "INE467B01029", "Bonus 1:1", "2024-11-01")],
    )  # fmt: skip
    build_actions(db)
    used = db.execute(
        "SELECT action_id FROM ref.corporate_action WHERE listing_id = %s", (ids["RELIANCE"],)
    ).fetchone()
    assert used is not None
    db.execute(
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, factor, action_id, status, auto,"
        " proposed_by, applied_at) VALUES (%s, '2024-10-28', 0.5, %s, 'applied', true, 'rules',"
        " now())",
        (ids["RELIANCE"], used[0]),
    )
    _response(db, [], revision=2)  # both announcements gone from the newest response
    result = build_actions(db)
    assert result.deleted == 1
    left = db.execute("SELECT listing_id FROM ref.corporate_action").fetchall()
    assert left == [(ids["RELIANCE"],)]


def test_unparsed_actions_about_to_take_effect_are_flagged(db: Conn) -> None:
    from cagr_data.reference.calendar import refresh_calendar

    refresh_calendar(db, "XNSE", date(2024, 12, 31))
    _listings(db)
    _response(db, [("RELIANCE", "INE002A01018", "Rights 1:5", "2024-10-29")])
    build_actions(db)
    assert not corporate_action_parsed(db, date(2024, 10, 28)).passed
    assert corporate_action_parsed(db, date(2024, 10, 1)).passed
