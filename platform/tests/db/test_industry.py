"""The cagr_industry seed (spec 0006, section 5.1) and ref.listing_classification."""

from datetime import date
from pathlib import Path

import pytest

from cagr_data.core.db import Connection as Conn
from cagr_data.reference.calendar import refresh_calendar
from cagr_data.reference.industry import industry_known, load_seed, read_seed
from cagr_data.reference.listing_build import PgStore
from cagr_data.reference.listings import Book, Row

pytestmark = pytest.mark.db

SEED = Path(__file__).parents[3] / "db" / "seeds" / "group3_2026-10-27.csv"
DAY = date(2024, 1, 1)


def _listings(db: Conn) -> dict[str, int]:
    refresh_calendar(db, "XNSE", date(2024, 1, 31))
    rows = [Row("RELIANCE", "EQ", "INE002A01018"), Row("TCS", "EQ", "INE467B01029"),
            Row("OLDCO", "EQ", "INE000X01011"), Row("DPSCLTD", "EQ", "INE000Y01011")]  # fmt: skip
    r = Book(PgStore(db)).apply_session(DAY, 1, rows, [], {})
    ids = {symbol: lid for (symbol, _), lid in r.mapped.items()}
    # OLDCO stopped trading in 2024: its symbol ends long before the export.
    db.execute(
        "UPDATE ref.listing_symbol SET valid_to = '2024-06-01' WHERE listing_id = %s",
        (ids["OLDCO"],),
    )
    return ids


def test_the_committed_seed_is_well_formed() -> None:
    rows = read_seed(SEED.read_text(encoding="utf-8"))
    assert len(rows) == 3040
    assert ("RELIANCE", "Oil and Gas") in rows


def test_the_migration_has_the_52_industries(db: Conn) -> None:
    assert db.execute(
        "SELECT count(*), count(DISTINCT sector_index) FROM ref.industry"
    ).fetchone() == (52, 18)
    known = {n for (n,) in db.execute("SELECT name FROM ref.industry").fetchall()}
    seeded = {v for _, v in read_seed(SEED.read_text(encoding="utf-8"))}
    assert known <= seeded


def test_seed_maps_symbols_and_reports_the_rest(db: Conn) -> None:
    ids = _listings(db)
    rows = [("RELIANCE", "Oil and Gas"), ("TCS", "Software"), ("OLDCO", "Textile"),
            ("DPSCLTD", "re"), ("TRADER", "Trading"), ("GONE", "Defence")]  # fmt: skip
    db.execute("SET ROLE cagr_writer")
    result = load_seed(db, rows, file="seed.csv")
    assert (result.applied, result.by_last_holder, result.already) == (3, 1, 0)
    assert result.not_an_industry == {"re": ["DPSCLTD"], "Trading": ["TRADER"]}
    assert result.no_listing == ["GONE"]
    assert load_seed(db, rows, file="seed.csv").already == 3  # a rerun adds nothing

    got = dict(db.execute(
        "SELECT listing_id, cagr_industry FROM ref.listing_classification('2024-01-02')"
        " WHERE cagr_industry IS NOT NULL"
    ).fetchall())  # fmt: skip
    assert got == {ids["RELIANCE"]: "Oil and Gas", ids["TCS"]: "Software",
                   ids["OLDCO"]: "Textile"}  # fmt: skip
    change = db.execute(
        "SELECT effective_date, status, proposed_by, evidence->>'matched'"
        " FROM ref.industry_change WHERE listing_id = %s", (ids["OLDCO"],),
    ).fetchone()  # fmt: skip
    assert change == (DAY, "applied", "seed", "last_holder")
    assert industry_known(db).passed


def test_industry_known_fails_on_an_inactive_industry(db: Conn) -> None:
    _listings(db)
    load_seed(db, [("TCS", "Software")], file="seed.csv")
    db.execute("UPDATE ref.industry SET active = false WHERE name = 'Software'")
    assert not industry_known(db).passed
