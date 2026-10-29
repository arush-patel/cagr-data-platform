"""Clean daily, weekly and monthly bars and the adjusted view (spec 0003)."""

from datetime import date

import pytest

from cagr_data.clean.bars import build_daily, build_periods, build_steps, run_checks
from cagr_data.core.db import Connection as Conn
from cagr_data.reference import shares
from cagr_data.reference.adjustments import adjustment_explained, build_adjustments
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


def _bonus(db: Conn, listing_id: int, ex_date: date) -> None:
    file_id = _file(db, "nse_corporate_actions", date(2024, 1, 1))
    db.execute(
        "INSERT INTO ref.corporate_action (listing_id, ex_date, subject, seq, kind, ratio_new,"
        " ratio_old, parse_status, source_file_id) VALUES (%s, %s, 'Bonus 1:1', 1, 'bonus', 1, 1,"
        " 'parsed', %s)",
        (listing_id, ex_date, file_id),
    )


def test_adjustments_from_actions_and_jumps(db: Conn) -> None:
    ids = _seed(db)
    build_daily(db, DAYS[0], DAYS[-1])
    rel = ids["RELIANCE"]
    assert not adjustment_explained(db, DAYS[0], DAYS[-1]).passed

    # A jump with no action waits for review; once rejected it is never proposed again.
    result = build_adjustments(db, DAYS[0], DAYS[-1])
    assert (result.events, result.applied, result.pending) == (1, 0, 1)
    assert adjustment_explained(db, DAYS[0], DAYS[-1]).passed
    db.execute("UPDATE ref.price_adjustment SET status = 'rejected', decided_by = 'arush',"
               " decided_at = now()")  # fmt: skip
    assert build_adjustments(db, DAYS[0], DAYS[-1]).events == 0

    # With the bonus announced, a fresh build applies it from the action.
    db.execute("DELETE FROM ref.price_adjustment")
    _bonus(db, rel, DAYS[2])
    result = build_adjustments(db, DAYS[0], DAYS[-1])
    assert (result.applied, result.pending) == (1, 0)
    row = db.execute(
        "SELECT ex_date, factor, observed_factor, close_before, close_on, status, auto,"
        " proposed_by, action_id IS NOT NULL FROM ref.price_adjustment"
    ).fetchone()
    assert row == (DAYS[2], 0.5, 0.5, 102, 51, "applied", True, "rules", True)
    assert build_adjustments(db, DAYS[0], DAYS[-1]).events == 0  # a rerun adds nothing
    build_steps(db)
    adjusted = db.execute(
        "SELECT close FROM clean.bar_1d_adjusted WHERE listing_id = %s ORDER BY date", (rel,)
    ).fetchall()
    assert [r[0] for r in adjusted] == [50.0, 51.0, 51.0, 52.0, 50.0, 53.0]


@pytest.mark.parametrize("status", ["pending", "out_of_scope"])
def test_an_undecided_item_is_applied_once_its_action_is_known(db: Conn, status: str) -> None:
    ids = _seed(db)
    build_daily(db, DAYS[0], DAYS[-1])
    rel = ids["RELIANCE"]
    assert build_adjustments(db, DAYS[0], DAYS[-1]).pending == 1  # a jump, no action yet
    if status == "out_of_scope":
        db.execute("UPDATE ref.price_adjustment SET status = 'out_of_scope',"
                   " decided_by = 'scope', decided_at = now()")  # fmt: skip
    again = build_adjustments(db, DAYS[0], DAYS[-1])
    assert (again.applied, again.pending, again.redecided) == (0, 0, 0)  # still undecided

    _bonus(db, rel, DAYS[2])  # e.g. a parser fix reads the announcement
    result = build_adjustments(db, DAYS[0], DAYS[-1])
    assert (result.applied, result.pending, result.redecided) == (0, 0, 1)
    rows = db.execute(
        "SELECT ex_date, factor, status, auto, decided_by, note, action_id IS NOT NULL"
        " FROM ref.price_adjustment"
    ).fetchall()
    assert rows == [(DAYS[2], 0.5, "applied", True, None, "confirmed by the close", True)]
    assert build_adjustments(db, DAYS[0], DAYS[-1]).events == 0


def _mcap(db: Conn, day: date, rows: list[tuple[str, int, float]]) -> None:
    file_id = _file(db, "nse_mcap", day)
    for symbol, issue_size, market_cap in rows:
        db.execute(
            "INSERT INTO raw.nse_mcap (file_id, trade_date, symbol, series, category,"
            " last_trade_date, issue_size, close, market_cap_inr)"
            " VALUES (%s, %s, %s, 'EQ', 'Listed', %s, %s, 53, %s)",
            (file_id, day, symbol, day, issue_size, market_cap),
        )


def test_share_periods_walk_back_from_mcap(db: Conn) -> None:
    ids = _seed(db)
    build_daily(db, DAYS[0], DAYS[-1])
    rel = ids["RELIANCE"]
    _bonus(db, rel, DAYS[2])
    build_adjustments(db, DAYS[0], DAYS[-1])
    _mcap(db, DAYS[5], [("RELIANCE", 1000, 53000), ("UNKNOWN", 10, 10)])

    result = shares.build_shares(db)
    assert (result.listings, result.periods) == (1, 3)
    periods = db.execute(
        "SELECT valid_from, valid_to, shares, source FROM ref.listing_shares ORDER BY 1"
    ).fetchall()
    assert periods == [
        (DAYS[0], DAYS[2], 500, "walk_back"),  # before the 1:1 bonus
        (DAYS[2], DAYS[5], 1000, "walk_back"),
        (DAYS[5], None, 1000, "nse_mcap"),
    ]
    caps = db.execute(
        "SELECT market_cap_inr FROM clean.market_cap_1d WHERE listing_id = %s ORDER BY date",
        (rel,),
    ).fetchall()
    assert [float(r[0]) for r in caps] == [50000, 51000, 51000, 52000, 50000, 53000]
    assert shares.build_shares(db).periods == 3  # a rebuild replaces, never duplicates

    outcomes = {o.check_id: o for o in shares.run_checks(db, DAYS[0], DAYS[-1], {})}
    assert not outcomes["mcap_rows_mapped"].passed  # UNKNOWN has no listing
    assert outcomes["shares_present"].passed
    assert outcomes["walk_back_vs_index"].passed
    assert shares.mcap_matches_nse(db, DAYS[0], DAYS[-1], 0.01).passed
