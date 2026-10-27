"""Build ref.listing* from raw, one session at a time (spec 0002, section 5), and run the
ref-layer checks (section 8).

    uv run python -m cagr_data.reference.listing_build [--through 2026-10-26]

Runs as the pipeline's login (CAGR_PIPELINE_DATABASE_URL). The build continues from the last
session already built, so a rerun over history changes nothing. It commits after every year, so
a dropped connection loses at most the year in progress.
"""

import argparse
import bisect
import sys
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from psycopg.types.json import Jsonb

from cagr_data.core import runs
from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings
from cagr_data.quality import runner
from cagr_data.quality.checks import SAMPLE, Outcome
from cagr_data.reference.listings import (
    FIRST_ISIN_DATE,
    LISTING_SERIES,
    AssetClass,
    Board,
    Book,
    ChangeKind,
    Listing,
    Row,
    SessionResult,
    Status,
    SymbolChange,
)

VENUE = "XNSE"
PROPOSED_BY = "rules"


class PgStore:
    """Writes the Book's changes to ref.listing* in the caller's transaction."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def new_listing(
        self, asset_class: AssetClass, board: Board | None, name: str | None, first_session: date
    ) -> int:
        row = self.conn.execute(
            "INSERT INTO ref.listing (venue_id, asset_class, board, name, first_session,"
            " last_session) VALUES (%s, %s, %s, %s, %s, %s) RETURNING listing_id",
            (VENUE, asset_class, board, name, first_session, first_session),
        ).fetchone()
        assert row is not None
        return int(row[0])

    def change(
        self,
        kind: ChangeKind,
        listing_id: int,
        effective_date: date,
        payload: Mapping[str, Any],
        evidence: Mapping[str, Any],
        status: Status,
    ) -> int:
        row = self.conn.execute(
            "INSERT INTO ref.listing_change (kind, listing_id, effective_date, payload, evidence,"
            " status, auto, proposed_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            " RETURNING change_id",
            (kind, listing_id, effective_date, Jsonb(dict(payload)), Jsonb(dict(evidence)),
             status, status == "applied", PROPOSED_BY),
        ).fetchone()  # fmt: skip
        assert row is not None
        return int(row[0])

    def open_period(
        self, attr: Literal["symbol", "isin"], listing_id: int, value: str, valid_from: date,
        change_id: int,
    ) -> None:  # fmt: skip
        self.conn.execute(
            f"INSERT INTO ref.listing_{attr} (listing_id, {attr}, valid_from, change_id)"
            " VALUES (%s, %s, %s, %s)",
            (listing_id, value, valid_from, change_id),
        )

    def close_period(
        self, attr: Literal["symbol", "isin"], listing_id: int, valid_to: date
    ) -> None:
        self.conn.execute(
            f"UPDATE ref.listing_{attr} SET valid_to = %s"
            " WHERE listing_id = %s AND valid_to IS NULL",
            (valid_to, listing_id),
        )


@dataclass
class Calendar:
    """XNSE sessions in order, with their session_index."""

    dates: list[date]
    indices: list[int]

    def index_of(self, day: date) -> int:
        """The session_index of day, or of the first session after it."""
        i = bisect.bisect_left(self.dates, day)
        return self.indices[i] if i < len(self.dates) else self.indices[-1] + 1


def load_calendar(conn: Connection) -> Calendar:
    rows = conn.execute(
        "SELECT date, session_index FROM ref.calendar_session"
        " WHERE venue_id = %s AND is_session ORDER BY date",
        (VENUE,),
    ).fetchall()
    return Calendar([r[0] for r in rows], [r[1] for r in rows])


def load_book(conn: Connection, store: PgStore, calendar: Calendar) -> Book:
    aliases: dict[str, str] = {
        r[0]: r[1] for r in conn.execute("SELECT name, canonical FROM ref.index_alias")
    }
    rows = conn.execute(
        """
        SELECT l.listing_id, l.asset_class, l.board, l.first_session, l.last_session, l.status,
               s.symbol, i.isin, fi.valid_from
          FROM ref.listing l
          LEFT JOIN ref.listing_symbol s ON s.listing_id = l.listing_id AND s.valid_to IS NULL
          LEFT JOIN ref.listing_isin i ON i.listing_id = l.listing_id AND i.valid_to IS NULL
          LEFT JOIN LATERAL (
              SELECT min(valid_from) AS valid_from FROM ref.listing_isin
               WHERE listing_id = l.listing_id
          ) fi ON true
         WHERE l.venue_id = %s
        """,
        (VENUE,),
    ).fetchall()
    listings = [
        Listing(
            listing_id=r[0], asset_class=r[1], board=r[2], first_session=r[3],
            last_session=r[4], last_index=calendar.index_of(r[4]), symbol=r[6], isin=r[7],
            status=r[5], first_isin_date=r[8],
        )
        for r in rows
    ]  # fmt: skip
    return Book(store, listings, aliases)


def load_symbol_changes(conn: Connection, calendar: Calendar) -> dict[str, list[SymbolChange]]:
    """symbolchange.csv from its latest snapshot, which holds the full history, by new symbol."""
    rows = conn.execute(
        """
        SELECT old_symbol, new_symbol, change_date FROM raw.nse_symbol_change
         WHERE file_id = (SELECT file_id FROM raw.vendor_file_current
                           WHERE dataset = 'nse_symbol_change'
                           ORDER BY partition_date DESC LIMIT 1)
        """
    ).fetchall()
    by_new: dict[str, list[SymbolChange]] = defaultdict(list)
    for old, new, day in rows:
        by_new[new].append(SymbolChange(old, new, day, calendar.index_of(day)))
    return dict(by_new)


def built_through(conn: Connection) -> date | None:
    row = conn.execute(
        "SELECT max(last_session) FROM ref.listing WHERE venue_id = %s AND asset_class <> 'index'",
        (VENUE,),
    ).fetchone()
    return row[0] if row else None


def _year_rows(conn: Connection, start: date, end: date) -> dict[date, tuple[list[Row], set[int]]]:
    out: dict[date, tuple[list[Row], set[int]]] = defaultdict(lambda: ([], set()))
    rows = conn.execute(
        "SELECT trade_date, symbol, series, isin, close, volume, file_id"
        " FROM raw.nse_cm_bar_current WHERE trade_date BETWEEN %s AND %s AND series = ANY(%s)"
        " ORDER BY trade_date, symbol, series",
        (start, end, sorted(LISTING_SERIES)),
    ).fetchall()
    for day, symbol, series, isin, close, volume, file_id in rows:
        bucket = out[day]
        bucket[0].append(
            Row(symbol, series, isin, float(close) if close is not None else None, volume)
        )
        bucket[1].add(file_id)
    return out


def _year_indices(
    conn: Connection, start: date, end: date
) -> dict[date, tuple[list[str], set[int]]]:
    out: dict[date, tuple[list[str], set[int]]] = defaultdict(lambda: ([], set()))
    rows = conn.execute(
        "SELECT trade_date, index_name, file_id FROM raw.nse_index_close_current"
        " WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date, index_name",
        (start, end),
    ).fetchall()
    for day, name, file_id in rows:
        out[day][0].append(name)
        out[day][1].add(file_id)
    return out


def _save_sessions(conn: Connection, book: Book) -> None:
    ids = list(book.listings)
    conn.execute(
        """
        UPDATE ref.listing l
           SET last_session = v.last_session, status = v.status
          FROM unnest(%s::integer[], %s::date[], %s::text[]) AS v(listing_id, last_session, status)
         WHERE l.listing_id = v.listing_id
           AND (l.last_session, l.status) IS DISTINCT FROM (v.last_session, v.status)
        """,
        (ids, [book.listings[i].last_session for i in ids], [book.listings[i].status for i in ids]),
    )


def _master_symbols(conn: Connection) -> set[str]:
    """Symbols in the latest main-board and SME equity masters."""
    rows = conn.execute(
        """
        SELECT e.symbol FROM raw.nse_equity_list e
         WHERE e.file_id IN (SELECT DISTINCT ON (dataset) file_id FROM raw.vendor_file_current
                              WHERE dataset IN ('nse_equity_list', 'nse_sme_equity_list')
                              ORDER BY dataset, partition_date DESC)
        """
    ).fetchall()
    return {r[0] for r in rows}


@dataclass
class BuildResult:
    first: date | None = None
    last: date | None = None
    sessions: int = 0
    new_listings: int = 0
    symbol_changes: int = 0
    isin_changes: int = 0
    review_items: int = 0
    outcomes: list[Outcome] = field(default_factory=list)

    def add(self, r: SessionResult) -> None:
        self.new_listings += r.new_listings
        self.symbol_changes += r.symbol_changes
        self.isin_changes += r.isin_changes
        self.review_items += r.review_items


class MissingSessionError(Exception):
    pass


def build(conn: Connection, through: date, commit: Callable[[], None] | None = None) -> BuildResult:
    """Apply every session after the last one built, up to through. Commits after each year
    (commit, when given, replaces conn.commit).

    Stops at the first session with no bhavcopy rows, since every later session depends on it.
    """
    commit = commit or conn.commit
    calendar = load_calendar(conn)
    store = PgStore(conn)
    book = load_book(conn, store, calendar)
    changes = load_symbol_changes(conn, calendar)
    after = built_through(conn)
    sessions = [d for d in calendar.dates if (after is None or d > after) and d <= through]
    index_of = dict(zip(calendar.dates, calendar.indices, strict=True))
    result = BuildResult()

    for year in sorted({d.year for d in sessions}):
        days = [d for d in sessions if d.year == year]
        bars = _year_rows(conn, days[0], days[-1])
        indices = _year_indices(conn, days[0], days[-1])
        for day in days:
            if day not in bars:
                commit()
                raise MissingSessionError(f"no bhavcopy rows for session {day}; load it first")
            rows, file_ids = bars[day]
            result.add(book.apply_session(day, index_of[day], rows, sorted(file_ids), changes))
            if day in indices:
                names, index_files = indices[day]
                result.add(book.apply_indices(day, index_of[day], names, sorted(index_files)))
            result.first = result.first or day
            result.last = day
            result.sessions += 1
        _save_sessions(conn, book)
        commit()

    if result.last is not None:
        book.refresh_status(index_of[result.last], _master_symbols(conn))
        _save_sessions(conn, book)
        commit()
    return result


# Checks (spec 0002, section 8). Each looks at the whole ref.listing* state, or at the sessions
# just built, and returns one Outcome.


def _outcome(check_id: str, rows: Sequence[tuple[Any, ...]], what: str) -> Outcome:
    if not rows:
        return Outcome(check_id, True)
    sample = ["/".join(str(v) for v in r) for r in rows[:SAMPLE]]
    return Outcome(check_id, False, {"count": len(rows), "sample": sample}, f"{len(rows)} {what}")


def _overlaps(conn: Connection, attr: str) -> list[tuple[Any, ...]]:
    return conn.execute(
        f"""
        SELECT a.{attr}, a.listing_id, b.listing_id, greatest(a.valid_from, b.valid_from)
          FROM ref.listing_{attr} a
          JOIN ref.listing_{attr} b
            ON a.{attr} = b.{attr} AND a.listing_id < b.listing_id
           AND daterange(a.valid_from, a.valid_to) && daterange(b.valid_from, b.valid_to)
         ORDER BY 4
        """
    ).fetchall()


def run_checks(
    conn: Connection, start: date, end: date, params: Mapping[str, Any]
) -> list[Outcome]:
    out = [
        _outcome("listing_unique_symbol", _overlaps(conn, "symbol"), "symbols on two listings"),
        _outcome("listing_unique_isin", _overlaps(conn, "isin"), "ISINs on two listings"),
    ]
    unmapped = conn.execute(
        """
        SELECT b.trade_date, b.symbol, b.series FROM raw.nse_cm_bar_current b
         WHERE b.trade_date BETWEEN %s AND %s AND b.series = ANY(%s)
           AND NOT EXISTS (
               SELECT 1 FROM ref.listing_symbol s JOIN ref.listing l USING (listing_id)
                WHERE s.symbol = b.symbol AND l.asset_class <> 'index' AND l.status <> 'merged'
                  AND s.valid_from <= b.trade_date
                  AND (s.valid_to IS NULL OR s.valid_to > b.trade_date))
         ORDER BY 1, 2
        """,
        (start, end, sorted(LISTING_SERIES)),
    ).fetchall()
    out.append(_outcome("listing_rows_mapped", unmapped, "rows without a listing"))

    no_isin = conn.execute(
        """
        SELECT l.listing_id, s.symbol FROM ref.listing l
          LEFT JOIN ref.listing_symbol s ON s.listing_id = l.listing_id AND s.valid_to IS NULL
         WHERE l.status = 'active' AND l.asset_class = 'equity' AND l.last_session >= %s
           AND NOT EXISTS (SELECT 1 FROM ref.listing_isin i WHERE i.listing_id = l.listing_id)
         ORDER BY 1
        """,
        (params.get("listing_isin_present", {}).get("from", FIRST_ISIN_DATE),),
    ).fetchall()
    out.append(_outcome("listing_isin_present", no_isin, "active equity listings with no ISIN"))

    unconfirmed = conn.execute(
        """
        SELECT effective_date, payload->>'old_symbol', payload->>'new_symbol'
          FROM ref.listing_change
         WHERE kind = 'symbol_change' AND evidence->>'rule' = '5.2.1'
           AND (evidence->>'confirmed')::boolean IS FALSE
           AND effective_date BETWEEN %s AND %s
         ORDER BY 1
        """,
        (start, end),
    ).fetchall()
    out.append(
        _outcome("symbol_change_confirmed", unconfirmed, "symbol changes not in symbolchange.csv")
    )

    max_sessions = params.get("review_waiting", {}).get("max_sessions", 3)
    waiting = conn.execute(
        """
        WITH cutoff AS (
            SELECT date FROM ref.calendar_session
             WHERE venue_id = %s AND is_session AND date <= current_date
             ORDER BY date DESC OFFSET %s LIMIT 1)
        SELECT 'listing_change', change_id, created_at::date FROM ref.listing_change, cutoff
         WHERE status = 'pending' AND created_at::date < cutoff.date
        UNION ALL
        SELECT 'price_adjustment', adjustment_id, created_at::date
          FROM ref.price_adjustment, cutoff
         WHERE status = 'pending' AND created_at::date < cutoff.date
         ORDER BY 3
        """,
        (VENUE, max_sessions),
    ).fetchall()
    out.append(_outcome("review_waiting", waiting, "pending items waiting too long"))
    return out


def check(conn: Connection, run_id: int, start: date, end: date) -> list[Outcome]:
    """Run and record the ref checks for sessions start to end. Returns the critical failures."""
    checks = runner.load_checks(conn, "ref")
    params = {k: c.params for k, c in checks.items()}
    outcomes = run_checks(conn, start, end, params)
    return runner.record(conn, checks, run_id=run_id, day=end, outcomes=outcomes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--through", type=date.fromisoformat, default=date.today())
    args = parser.parse_args(argv)

    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        run_id = runs.start_run(conn, "manual")
        conn.commit()
        try:
            result = build(conn, args.through)
        except MissingSessionError as e:
            runs.finish_run(conn, run_id, succeeded=False)
            conn.commit()
            print(e, file=sys.stderr)
            return 1
        failed: list[Outcome] = []
        if result.first is not None and result.last is not None:
            failed = check(conn, run_id, result.first, result.last)
        runs.finish_run(conn, run_id, succeeded=not failed)
        conn.commit()
    print(
        f"{result.sessions} sessions ({result.first} to {result.last}):"
        f" {result.new_listings} new listings, {result.symbol_changes} symbol changes,"
        f" {result.isin_changes} ISIN changes, {result.review_items} review items"
    )
    for o in failed:
        print(f"critical: {o.check_id}: {o.detail}", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
