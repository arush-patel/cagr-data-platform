"""Clean bars keyed on listing_id (spec 0003): daily bars from raw, the adjustment steps from
applied adjustments, and weekly and monthly bars from the daily ones.

    uv run python -m cagr_data.clean.bars 2010-01-01 2026-10-26

Every step deletes and inserts its rows in the caller's transaction, so a rerun leaves the same
rows. The command rebuilds the range a year at a time, committing after each, then the steps,
the weekly and monthly bars, and records the clean-layer checks.
"""

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from typing import Any

from cagr_data.core import runs
from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings
from cagr_data.quality import runner
from cagr_data.quality.checks import SAMPLE, Outcome
from cagr_data.reference.listings import LISTING_SERIES

CRORE = 10_000_000
PERIODS = {"clean.bar_1w": "week", "clean.bar_1mo": "month"}


def build_daily(conn: Connection, start: date, end: date) -> int:
    """Replace the daily bars from start to end with one bar per listing per raw row: the
    listing-series row of each stock (zero-volume rows included) and each index's close.
    Returns the number of bars."""
    conn.execute("DELETE FROM clean.bar_1d WHERE date BETWEEN %s AND %s", (start, end))
    stocks = conn.execute(
        """
        INSERT INTO clean.bar_1d (listing_id, date, open, high, low, close, volume, value_traded,
                                  trade_count, prev_close, series, source_file_id)
        SELECT s.listing_id, b.trade_date, b.open, b.high, b.low, b.close, b.volume,
               b.traded_value_inr, b.trade_count, b.prev_close, b.series, b.file_id
          FROM raw.nse_cm_bar_current b
          JOIN ref.listing_symbol s
            ON s.symbol = b.symbol AND s.valid_from <= b.trade_date
           AND (s.valid_to IS NULL OR s.valid_to > b.trade_date)
          JOIN ref.listing l ON l.listing_id = s.listing_id
         WHERE b.trade_date BETWEEN %s AND %s AND b.series = ANY(%s) AND b.close IS NOT NULL
           AND l.asset_class <> 'index' AND l.status <> 'merged'
        """,
        (start, end, sorted(LISTING_SERIES)),
    ).rowcount
    indices = conn.execute(
        """
        INSERT INTO clean.bar_1d (listing_id, date, open, high, low, close, volume, value_traded,
                                  series, source_file_id)
        SELECT s.listing_id, c.trade_date, c.open, c.high, c.low, c.close, c.volume,
               c.turnover_inr * %s, '', c.file_id
          FROM raw.nse_index_close_current c
          JOIN ref.listing_symbol s
            ON s.symbol = c.index_name AND s.valid_from <= c.trade_date
           AND (s.valid_to IS NULL OR s.valid_to > c.trade_date)
          JOIN ref.listing l ON l.listing_id = s.listing_id
         WHERE c.trade_date BETWEEN %s AND %s AND c.close IS NOT NULL
           AND l.asset_class = 'index' AND l.status <> 'merged'
        """,
        (CRORE, start, end),
    ).rowcount
    return stocks + indices


def build_steps(conn: Connection) -> int:
    """Rebuild clean.adjustment_step from the applied adjustments. The period before a listing's
    i-th ex-date carries the product of the i-th and every later factor."""
    conn.execute("DELETE FROM clean.adjustment_step")
    return conn.execute(
        """
        INSERT INTO clean.adjustment_step (listing_id, valid_from, valid_to, cum_factor)
        WITH per_day AS (
            SELECT listing_id, ex_date, sum(ln(factor)) AS log_factor
              FROM ref.price_adjustment WHERE status = 'applied'
             GROUP BY listing_id, ex_date)
        SELECT listing_id,
               coalesce(lag(ex_date) OVER (PARTITION BY listing_id ORDER BY ex_date),
                        '-infinity'::date),
               ex_date,
               exp(sum(log_factor) OVER (PARTITION BY listing_id ORDER BY ex_date DESC
                                         ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW))
          FROM per_day
        """
    ).rowcount


def build_periods(
    conn: Connection, start: date, end: date, listing_ids: Sequence[int] | None = None
) -> dict[str, int]:
    """Replace the weekly and monthly bars of every period that touches start to end (all
    listings, or only listing_ids). A period's start and end are its first and last XNSE
    session; it is complete once its last session has daily bars."""
    counts: dict[str, int] = {}
    for table, unit in PERIODS.items():
        bounds = conn.execute(
            f"""
            SELECT min(date), max(date) FROM ref.calendar_session
             WHERE venue_id = 'XNSE' AND is_session
               AND date_trunc('{unit}', date) BETWEEN date_trunc('{unit}', %s::date)
                                                  AND date_trunc('{unit}', %s::date)
            """,
            (start, end),
        ).fetchone()
        if bounds is None or bounds[0] is None:
            counts[table] = 0
            continue
        first, last = bounds
        only = "AND listing_id = ANY(%(ids)s)" if listing_ids is not None else ""
        day_only = only.replace("listing_id", "u.listing_id")
        params: dict[str, Any] = {"first": first, "last": last, "ids": list(listing_ids or [])}
        conn.execute(
            f"DELETE FROM {table} WHERE period_start BETWEEN %(first)s AND %(last)s {only}",
            params,
        )
        counts[table] = conn.execute(
            f"""
            WITH periods AS (
                SELECT date_trunc('{unit}', date)::date AS k, min(date) AS period_start,
                       max(date) AS period_end
                  FROM ref.calendar_session
                 WHERE venue_id = 'XNSE' AND is_session AND date BETWEEN %(first)s AND %(last)s
                 GROUP BY 1),
            built AS (SELECT max(date) AS d FROM clean.bar_1d),
            state AS (SELECT max(adjustment_id) AS id FROM ref.price_adjustment
                       WHERE status = 'applied'),
            days AS (
                SELECT u.listing_id, u.date, u.open, u.high, u.low, u.close, u.volume,
                       u.value_traded, u.trade_count, a.open AS a_open, a.high AS a_high,
                       a.low AS a_low, a.close AS a_close, a.volume AS a_volume,
                       a.listing_id IS NULL AS held
                  FROM clean.bar_1d u
                  LEFT JOIN clean.bar_1d_adjusted a USING (listing_id, date)
                 WHERE u.date BETWEEN %(first)s AND %(last)s {day_only})
            INSERT INTO {table} (listing_id, period_start, period_end, open, high, low, close,
                   volume, value_traded, trade_count, session_count, adj_open, adj_high, adj_low,
                   adj_close, adj_volume, complete, adjustment_state)
            SELECT d.listing_id, p.period_start, p.period_end,
                   (array_agg(d.open ORDER BY d.date))[1], max(d.high), min(d.low),
                   (array_agg(d.close ORDER BY d.date DESC))[1], sum(d.volume),
                   sum(d.value_traded), sum(d.trade_count), count(*),
                   CASE WHEN bool_or(d.held) THEN NULL
                        ELSE (array_agg(d.a_open ORDER BY d.date))[1] END,
                   CASE WHEN bool_or(d.held) THEN NULL ELSE max(d.a_high) END,
                   CASE WHEN bool_or(d.held) THEN NULL ELSE min(d.a_low) END,
                   CASE WHEN bool_or(d.held) THEN NULL
                        ELSE (array_agg(d.a_close ORDER BY d.date DESC))[1] END,
                   CASE WHEN bool_or(d.held) THEN NULL ELSE sum(d.a_volume) END,
                   p.period_end <= (SELECT d FROM built),
                   (SELECT id FROM state)
              FROM days d
              JOIN periods p ON p.k = date_trunc('{unit}', d.date)::date
             GROUP BY d.listing_id, p.period_start, p.period_end
            """,
            params,
        ).rowcount
    return counts


# Checks (spec 0003, section 7).


def _outcome(check_id: str, rows: Sequence[tuple[Any, ...]], what: str) -> Outcome:
    if not rows:
        return Outcome(check_id, True)
    sample = ["/".join(str(v) for v in r) for r in rows[:SAMPLE]]
    return Outcome(check_id, False, {"count": len(rows), "sample": sample}, f"{len(rows)} {what}")


def run_checks(
    conn: Connection, start: date, end: date, params: dict[str, dict[str, Any]]
) -> list[Outcome]:
    out = []
    rows = conn.execute(
        """
        SELECT b.date, b.listing_id FROM clean.bar_1d b
          LEFT JOIN ref.calendar_session c ON c.venue_id = 'XNSE' AND c.date = b.date
         WHERE b.date BETWEEN %s AND %s AND c.is_session IS NOT TRUE ORDER BY 1, 2
        """,
        (start, end),
    ).fetchall()
    out.append(_outcome("bar_on_session", rows, "bars on days that are not sessions"))

    rows = conn.execute(
        """
        SELECT b.trade_date, b.symbol, b.series FROM raw.nse_cm_bar_current b
         WHERE b.trade_date BETWEEN %s AND %s AND b.series = ANY(%s) AND b.close IS NOT NULL
           AND (SELECT count(*) FROM clean.bar_1d c JOIN ref.listing_symbol s
                    ON s.listing_id = c.listing_id AND s.symbol = b.symbol
                   AND s.valid_from <= b.trade_date
                   AND (s.valid_to IS NULL OR s.valid_to > b.trade_date)
                 WHERE c.date = b.trade_date) <> 1
         ORDER BY 1, 2
        """,
        (start, end, sorted(LISTING_SERIES)),
    ).fetchall()
    out.append(_outcome("bars_complete", rows, "raw rows without exactly one bar"))

    rows = conn.execute(
        """
        SELECT date, listing_id FROM clean.bar_1d
         WHERE date BETWEEN %s AND %s AND series <> ''
           AND (low > open OR low > close OR high < open OR high < close)
         ORDER BY 1, 2
        """,
        (start, end),
    ).fetchall()
    out.append(_outcome("bar_ohlc_order", rows, "bars out of order"))

    max_move = params.get("adjusted_jump", {}).get("max_move", 0.25)
    rows = conn.execute(
        """
        WITH moves AS (
            SELECT listing_id, date, close,
                   lag(close) OVER (PARTITION BY listing_id ORDER BY date) AS prev
              FROM clean.bar_1d_adjusted WHERE date BETWEEN %s::date - 30 AND %s)
        SELECT m.date, m.listing_id, round((m.close / m.prev - 1)::numeric, 3) FROM moves m
         WHERE m.date BETWEEN %s AND %s AND m.prev > 0 AND abs(m.close / m.prev - 1) > %s
           AND NOT EXISTS (SELECT 1 FROM ref.price_adjustment p
                            WHERE p.listing_id = m.listing_id AND p.ex_date = m.date)
         ORDER BY 1, 2
        """,
        (start, end, start, end, max_move),
    ).fetchall()
    out.append(_outcome("adjusted_jump", rows, "adjusted moves with no adjustment event"))

    benchmark = params.get("index_benchmark_present", {})
    since = max(start, date.fromisoformat(benchmark.get("from", "2012-02-21")))
    rows = conn.execute(
        """
        SELECT c.date FROM ref.calendar_session c
         WHERE c.venue_id = 'XNSE' AND c.is_session AND c.date BETWEEN %s AND %s
           AND NOT EXISTS (
               SELECT 1 FROM clean.bar_1d b JOIN ref.listing_symbol s
                   ON s.listing_id = b.listing_id AND s.valid_to IS NULL AND s.symbol = %s
                WHERE b.date = c.date)
         ORDER BY 1
        """,
        (since, end, benchmark.get("index", "Nifty 50")),
    ).fetchall()
    out.append(_outcome("index_benchmark_present", rows, "sessions without a Nifty 50 bar"))

    mismatched: list[tuple[Any, ...]] = []
    for table in PERIODS:
        mismatched += conn.execute(
            f"""
            SELECT '{table}', p.listing_id, p.period_start FROM {table} p
              JOIN LATERAL (
                  SELECT count(*) AS n, max(high) AS h, min(low) AS l, sum(volume) AS v,
                         (array_agg(close ORDER BY date DESC))[1] AS c
                    FROM clean.bar_1d b
                   WHERE b.listing_id = p.listing_id
                     AND b.date BETWEEN p.period_start AND p.period_end) d ON true
             WHERE p.period_end >= %s AND p.period_start <= %s
               AND (p.session_count, p.close, p.volume, p.high, p.low)
                   IS DISTINCT FROM (d.n, d.c, d.v, d.h, d.l)
            """,
            (start, end),
        ).fetchall()
    out.append(_outcome("period_bars_match", mismatched, "period bars that disagree"))
    return out


def check(conn: Connection, run_id: int, start: date, end: date) -> list[Outcome]:
    """Run and record the clean checks. Returns the critical failures."""
    checks = runner.load_checks(conn, "clean")
    params = {k: c.params for k, c in checks.items()}
    return runner.record(
        conn, checks, run_id=run_id, day=end, outcomes=run_checks(conn, start, end, params)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        run_id = runs.start_run(conn, "manual")
        conn.commit()
        bars = 0
        for year in range(args.start.year, args.end.year + 1):
            lo, hi = max(args.start, date(year, 1, 1)), min(args.end, date(year, 12, 31))
            bars += build_daily(conn, lo, hi)
            conn.commit()
        conn.execute("ANALYZE clean.bar_1d")
        steps = build_steps(conn)
        conn.commit()
        periods = build_periods(conn, args.start, args.end)
        conn.commit()
        failed = check(conn, run_id, args.start, args.end)
        runs.finish_run(conn, run_id, succeeded=not failed)
        conn.commit()
    print(f"{bars} daily bars, {steps} adjustment steps, {periods}")
    for o in failed:
        print(f"critical: {o.check_id}: {o.detail}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
