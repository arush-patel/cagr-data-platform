"""Clean daily, weekly and monthly bars and the adjusted view (spec 0003)."""

from datetime import date

import pytest

from cagr_data.clean.bars import build_daily, build_periods, build_steps, run_checks
from cagr_data.core.db import Connection as Conn
from cagr_data.reference.calendar import refresh_calendar
from cagr_data.reference.listing_build import PgStore
from cagr_data.reference.listings import Book, Row

pytestmark = pytest.mark.db

# Mon 1 Jan to Mon 8 Jan 2024: one full week and the first session of the next.
DAYS = [date(2024, 1, d) for d in (1, 2, 3, 4, 5, 8)]
CLOSES = [100, 102, 51, 52, 50, 53]  # a 2:1 split on 3 Jan


def _file(db: Conn, feed_id: str, day: date) -> int:
    row = db.execute(
        "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes, storage_uri,"
        " row_count) VALUES (%s, %s, 'u', %s, 1, 's', 1) RETURNING file_id",
        (feed_id, day, f"{hash((feed_id, day)) & (2**64 - 1):064x}"),
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def _seed(db: Conn) -> dict[str, int]:
    refresh_calendar(db, "XNSE", date(2024, 1, 31))
    book = Book(PgStore(db))
    r = book.apply_session(DAYS[0], 1, [Row("RELIANCE", "EQ", "INE002A01018")], [], {})
    ids = {symbol: lid for (symbol, _), lid in r.mapped.items()}
    r = book.apply_indices(DAYS[0], 1, ["Nifty 50"], [])
    ids["Nifty 50"] = r.mapped[("Nifty 50", "")]
    for n, (day, close) in enumerate(zip(DAYS, CLOSES, strict=True)):
        cm = _file(db, "nse_cm_bhavcopy_legacy", day)
        db.execute(
            "INSERT INTO raw.nse_cm_bar (file_id, trade_date, symbol, series, isin, open, high,"
            " low, close, volume, traded_value_inr, source_format)"
            " VALUES (%s, %s, 'RELIANCE', 'EQ', 'INE002A01018', %s, %s, %s, %s, %s, %s,"
            " 'legacy_v1')",
            (cm, day, close, close + 1, close - 1, close, 0 if n == 1 else 1000, close * 1000),
        )
        # A debt row on the same symbol never becomes a bar.
        db.execute(
            "INSERT INTO raw.nse_cm_bar (file_id, trade_date, symbol, series, close, volume,"
            " source_format) VALUES (%s, %s, 'RELIANCE', 'N1', 99, 1, 'legacy_v1')",
            (cm, day),
        )
        ix = _file(db, "nse_index_close", day)
        db.execute(
            "INSERT INTO raw.nse_index_close (file_id, trade_date, index_name, open, high, low,"
            " close, turnover_inr) VALUES (%s, %s, 'Nifty 50', 21000, 21100, 20900, 21050, 2.5)",
            (ix, day),
        )
    return ids


def _adjustment(db: Conn, listing_id: int, ex_date: date, factor: float, status: str) -> None:
    applied = status == "applied"
    db.execute(
        "INSERT INTO ref.price_adjustment (listing_id, ex_date, factor, observed_factor, status,"
        " auto, proposed_by, applied_at) VALUES (%s, %s, %s, %s, %s, %s, 'rules', %s)",
        (listing_id, ex_date, factor if applied else None, factor, status, applied,
         "now" if applied else None),
    )  # fmt: skip


def test_daily_bars_keep_zero_volume_and_scale_index_turnover(db: Conn) -> None:
    ids = _seed(db)
    assert build_daily(db, DAYS[0], DAYS[-1]) == 12
    assert build_daily(db, DAYS[0], DAYS[-1]) == 12  # a rerun replaces, never duplicates
    stock = db.execute(
        "SELECT date, close, volume, series FROM clean.bar_1d WHERE listing_id = %s ORDER BY 1",
        (ids["RELIANCE"],),
    ).fetchall()
    assert [r[1] for r in stock] == CLOSES
    assert stock[1][2] == 0
    assert {r[3] for r in stock} == {"EQ"}
    index = db.execute(
        "SELECT value_traded, series FROM clean.bar_1d WHERE listing_id = %s AND date = %s",
        (ids["Nifty 50"], DAYS[0]),
    ).fetchone()
    assert index == (25_000_000, "")


def test_adjusted_view_applies_steps_and_holds_pending(db: Conn) -> None:
    ids = _seed(db)
    build_daily(db, DAYS[0], DAYS[-1])
    rel = ids["RELIANCE"]
    _adjustment(db, rel, DAYS[2], 0.5, "applied")
    assert build_steps(db) == 1
    adjusted = "SELECT date, close FROM clean.bar_1d_adjusted WHERE listing_id = %s ORDER BY 1"
    assert db.execute(adjusted, (rel,)).fetchall() == [
        (d, c * 0.5 if d < DAYS[2] else float(c)) for d, c in zip(DAYS, CLOSES, strict=True)
    ]
    # The unadjusted view never changes.
    unadjusted = db.execute(
        "SELECT close FROM clean.bar_1d_unadjusted WHERE listing_id = %s ORDER BY date", (rel,)
    ).fetchall()
    assert [r[0] for r in unadjusted] == CLOSES

    # A pending adjustment holds the adjusted bars from its ex-date on.
    _adjustment(db, rel, DAYS[4], 0.9, "pending")
    assert [r[0] for r in db.execute(adjusted, (rel,)).fetchall()] == DAYS[:4]

    # Two factors compound before the first ex-date.
    db.execute("UPDATE ref.price_adjustment SET status = 'applied', factor = 0.9,"
               " applied_at = now(), auto = true WHERE ex_date = %s", (DAYS[4],))  # fmt: skip
    assert build_steps(db) == 2
    first = db.execute(adjusted + " LIMIT 1", (rel,)).fetchone()
    assert first is not None and first[1] == pytest.approx(100 * 0.5 * 0.9)


def test_weekly_and_monthly_bars_agree_with_daily(db: Conn) -> None:
    ids = _seed(db)
    build_daily(db, DAYS[0], DAYS[-1])
    rel = ids["RELIANCE"]
    _adjustment(db, rel, DAYS[2], 0.5, "applied")
    build_steps(db)
    counts = build_periods(db, DAYS[0], DAYS[-1])
    assert counts == {"clean.bar_1w": 4, "clean.bar_1mo": 2}
    weeks = db.execute(
        "SELECT period_start, period_end, open, high, low, close, volume, session_count,"
        " adj_open, adj_close, complete FROM clean.bar_1w WHERE listing_id = %s ORDER BY 1",
        (rel,),
    ).fetchall()
    assert weeks[0][:8] == (DAYS[0], DAYS[4], 100, 103, 49, 50, 4000, 5)
    assert weeks[0][8:] == (50.0, 50.0, True)
    # The week of 8 Jan ends on 12 Jan, which has no bars yet.
    assert weeks[1][:2] == (DAYS[5], date(2024, 1, 12)) and weeks[1][10] is False
    month = db.execute(
        "SELECT period_start, period_end, close, session_count, complete FROM clean.bar_1mo"
        " WHERE listing_id = %s",
        (rel,),
    ).fetchone()
    assert month == (DAYS[0], date(2024, 1, 31), 53, 6, False)

    # A pending adjustment leaves the adjusted columns of its periods empty.
    _adjustment(db, rel, DAYS[5], 0.9, "pending")
    build_periods(db, DAYS[5], DAYS[5], [rel])
    held = db.execute(
        "SELECT period_start, adj_close FROM clean.bar_1w WHERE listing_id = %s ORDER BY 1",
        (rel,),
    ).fetchall()
    assert held == [(DAYS[0], 50.0), (DAYS[5], None)]

    outcomes = {o.check_id: o for o in run_checks(db, DAYS[0], DAYS[-1], {})}
    assert all(o.passed for o in outcomes.values()), outcomes


def test_checks_catch_a_missing_bar_and_an_unexplained_jump(db: Conn) -> None:
    ids = _seed(db)
    build_daily(db, DAYS[0], DAYS[-1])
    build_periods(db, DAYS[0], DAYS[-1])
    db.execute("DELETE FROM clean.bar_1d WHERE listing_id = %s AND date = %s",
               (ids["Nifty 50"], DAYS[1]))  # fmt: skip
    outcomes = {o.check_id: o for o in run_checks(db, DAYS[0], DAYS[-1], {})}
    assert not outcomes["adjusted_jump"].passed  # the split has no adjustment yet
    assert not outcomes["index_benchmark_present"].passed
    assert not outcomes["period_bars_match"].passed
    assert outcomes["bars_complete"].passed and outcomes["bar_on_session"].passed
