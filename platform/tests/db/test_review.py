"""Review decisions on pending listing changes and adjustments (spec 0002, section 7.4)."""

from datetime import date

import pytest

from cagr_data.core.db import Connection as Conn
from cagr_data.reference import review
from cagr_data.reference.listing_build import PgStore
from cagr_data.reference.listings import Book, Row

pytestmark = pytest.mark.db


def _split_after_silence(db: Conn) -> tuple[int, int, int]:
    """ABC trades, goes quiet for 40 sessions and comes back with a new ISIN: a new listing and
    a pending merge. Returns (old, new, change_id)."""
    book = Book(PgStore(db))
    book.apply_session(date(2024, 1, 1), 1, [Row("ABC", "EQ", "INE000A01011")], [], {})
    book.apply_session(date(2024, 3, 1), 41, [Row("ABC", "EQ", "INE000A01029")], [], {})
    for listing in book.listings.values():
        db.execute(
            "UPDATE ref.listing SET last_session = %s WHERE listing_id = %s",
            (listing.last_session, listing.listing_id),
        )
    row = db.execute(
        "SELECT change_id, listing_id, (payload->>'into_id')::int FROM ref.listing_change"
        " WHERE kind = 'merge' AND status = 'pending'"
    ).fetchone()
    assert row is not None
    return row[2], row[1], row[0]


def test_approving_a_merge_moves_symbols_and_isins(db: Conn) -> None:
    old, new, change_id = _split_after_silence(db)
    assert [p.item_id for p in review.pending(db)] == [change_id]
    review.approve(db, "listing_change", change_id, "arush", "same company, split")

    current = db.execute(
        "SELECT listing_id, symbol, isin, status, first_session, last_session"
        " FROM ref.listing_current ORDER BY listing_id"
    ).fetchall()
    assert current == [
        (old, "ABC", "INE000A01029", "active", date(2024, 1, 1), date(2024, 3, 1)),
        (new, None, None, "merged", date(2024, 3, 1), date(2024, 3, 1)),
    ]
    isins = db.execute(
        "SELECT isin, valid_from, valid_to FROM ref.listing_isin WHERE listing_id = %s"
        " ORDER BY valid_from",
        (old,),
    ).fetchall()
    assert isins == [
        ("INE000A01011", date(2024, 1, 1), date(2024, 3, 1)),
        ("INE000A01029", date(2024, 3, 1), None),
    ]
    decided = db.execute(
        "SELECT status, decided_by, note FROM ref.listing_change WHERE change_id = %s",
        (change_id,),
    ).fetchone()
    assert decided == ("applied", "arush", "same company, split")
    assert review.pending(db) == []


def test_rejecting_a_merge_keeps_two_listings(db: Conn) -> None:
    _, new, change_id = _split_after_silence(db)
    review.reject(db, "listing_change", change_id, "arush")
    status = db.execute("SELECT status FROM ref.listing WHERE listing_id = %s", (new,)).fetchone()
    assert status == ("active",)
    with pytest.raises(review.NotPending, match="already rejected"):
        review.approve(db, "listing_change", change_id, "arush")


def test_nobody_decides_their_own_proposal(db: Conn) -> None:
    old, new, change_id = _split_after_silence(db)
    review.reject(db, "listing_change", change_id, "arush")
    proposal = review.propose_merge(db, new, old, "arush")
    with pytest.raises(review.SelfDecision):
        review.approve(db, "listing_change", proposal, "arush")
    review.approve(db, "listing_change", proposal, "coworker")


def test_index_rename_needs_its_old_index(db: Conn) -> None:
    book = Book(PgStore(db))
    book.apply_indices(date(2024, 1, 1), 1, ["CNX IT", "CNX Bank"], [])
    book.apply_indices(date(2024, 1, 2), 2, ["Nifty IT", "CNX Bank"], [])
    change_id, old = db.execute(
        "SELECT change_id, (evidence->'candidates'->>0)::int FROM ref.listing_change"
        " WHERE kind = 'index_rename' AND status = 'pending'"
    ).fetchone()  # type: ignore[misc]
    with pytest.raises(review.MissingTarget):
        review.approve(db, "listing_change", change_id, "arush")
    review.approve(db, "listing_change", change_id, "arush", into_id=old)
    names = db.execute(
        "SELECT symbol, valid_to FROM ref.listing_symbol WHERE listing_id = %s ORDER BY valid_from",
        (old,),
    ).fetchall()
    assert names == [("CNX IT", date(2024, 1, 2)), ("Nifty IT", None)]


def test_approving_an_adjustment_applies_its_expected_factor(db: Conn) -> None:
    book = Book(PgStore(db))
    book.apply_session(date(2024, 10, 25), 1, [Row("RELIANCE", "EQ", "INE002A01018")], [], {})
    [listing_id] = book.listings
    adjustment = db.execute(
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, observed_factor, expected_factor,"
        " status, auto, proposed_by) VALUES (%s, '2024-10-28', 0.502, 0.5, 'pending', false,"
        " 'rules') RETURNING adjustment_id",
        (listing_id,),
    ).fetchone()
    assert adjustment is not None
    review.approve(db, "price_adjustment", adjustment[0], "arush")
    applied = db.execute(
        "SELECT status, factor, applied_at IS NOT NULL FROM ref.price_adjustment"
        " WHERE adjustment_id = %s",
        (adjustment[0],),
    ).fetchone()
    assert applied == ("applied", 0.5, True)


def test_unknown_items(db: Conn) -> None:
    with pytest.raises(review.NotFound):
        review.reject(db, "listing_change", 999_999, "arush")


def test_writer_can_decide(db: Conn) -> None:
    _, _, change_id = _split_after_silence(db)
    db.execute("SET ROLE cagr_writer")
    review.approve(db, "listing_change", change_id, "arush")
