"""EOD bars from the NSE bhavcopy into raw.vendor_bar.

raw is append-only. A session is written once as revision 1; if the vendor later sends different
values for it, those become revision 2 and a dirty range, so history drift is visible and the
clean layer recomputes exactly what changed.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
import psycopg

from cagr_data.clean import bars
from cagr_data.core import db
from cagr_data.reference import listings
from cagr_data.settings import get_settings
from cagr_data.vendors import nse_archives
from cagr_data.vendors.http import Fetched, Fetcher

PRICE_COLS = ("open", "high", "low", "close", "volume")


@dataclass
class SessionResult:
    session_date: date
    status: str  # loaded | missing | unchanged
    rows_in_file: int = 0
    equity_rows: int = 0
    inserted: int = 0
    revised: int = 0
    unmapped: int = 0
    reference: listings.SyncResult = field(default_factory=listings.SyncResult)


def start_ingest_run(
    conn: psycopg.Connection[Any], run_id: uuid.UUID, feed_id: int, kind: str, start: date, end: date
) -> None:
    conn.execute(
        "INSERT INTO raw.ingest_run (ingest_run_id, feed_id, kind, requested_from, requested_to, "
        "symbols_requested, symbols_returned, started_at) VALUES (%s, %s, %s, %s, %s, 0, 0, now())",
        (run_id, feed_id, kind, start, end),
    )


def finish_ingest_run(conn: psycopg.Connection[Any], run_id: uuid.UUID, returned: int) -> None:
    conn.execute(
        "UPDATE raw.ingest_run SET symbols_returned = %s, finished_at = now() WHERE ingest_run_id = %s",
        (returned, run_id),
    )


def record_file(
    conn: psycopg.Connection[Any], feed_id: int, session: date | None, f: Fetched, run_id: uuid.UUID
) -> None:
    if f.missing:
        if session is not None:
            conn.execute(
                "INSERT INTO raw.vendor_file_missing (feed_id, session_date, url, http_status) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (feed_id, session_date, url) DO UPDATE SET http_status = EXCLUDED.http_status, "
                "checked_at = now()",
                (feed_id, session, f.url, f.status),
            )
        return
    conn.execute(
        "INSERT INTO raw.vendor_file (feed_id, session_date, url, local_path, sha256, bytes, http_status, ingest_run_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (feed_id, url, sha256) DO NOTHING",
        (feed_id, session, f.url, str(f.local_path), f.sha256, len(f.content or b""), f.status, run_id),
    )
    if session is not None:
        conn.execute(
            "DELETE FROM raw.vendor_file_missing WHERE feed_id = %s AND session_date = %s", (feed_id, session)
        )


def _same(a: Any, b: Any) -> bool:
    if a is None or (isinstance(a, float) and math.isnan(a)):
        return b is None or (isinstance(b, float) and math.isnan(b))
    if b is None or (isinstance(b, float) and math.isnan(b)):
        return False
    return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))


def _nn(v: Any) -> Any:
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else v


def write_bars(
    conn: psycopg.Connection[Any],
    bars: pd.DataFrame,
    session: date,
    *,
    vendor_id: int,
    venue_id: int,
    run_id: uuid.UUID,
) -> tuple[int, int]:
    """Insert new bars as revision 1 and changed bars as the next revision. Returns (inserted, revised)."""
    existing = {
        r["listing_id"]: r
        for r in conn.execute(
            "SELECT DISTINCT ON (listing_id) listing_id, revision, open, high, low, close, volume "
            "FROM raw.vendor_bar WHERE session_date = %s AND vendor_id = %s "
            "ORDER BY listing_id, revision DESC",
            (session, vendor_id),
        )
    }
    inserted = revised = 0
    rows: list[tuple[Any, ...]] = []
    revised_listings: list[int] = []
    for b in bars.itertuples(index=False):
        prev = existing.get(b.listing_id)
        if prev is None:
            rev = 1
        elif all(_same(getattr(b, c), prev[c]) for c in PRICE_COLS):
            continue
        else:
            rev = prev["revision"] + 1
            revised_listings.append(int(b.listing_id))
        rows.append(
            (session, int(b.listing_id), vendor_id, rev, _nn(b.open), _nn(b.high), _nn(b.low), _nn(b.close),
             _nn(b.volume), _nn(b.turnover), _nn(b.prev_close),
             None if pd.isna(b.trades) else int(b.trades), b.series, run_id)
        )  # fmt: skip
        if rev == 1:
            inserted += 1
        else:
            revised += 1
    if rows:
        with conn.cursor().copy(
            "COPY raw.vendor_bar (session_date, listing_id, vendor_id, revision, open, high, low, close, volume, "
            "quote_volume, prev_close, trade_count, series, ingest_run_id) FROM STDIN"
        ) as cp:
            for row in rows:
                cp.write_row(row)
    for lid in revised_listings:
        conn.execute(
            "INSERT INTO clean.dirty_range (listing_id, venue_id, from_date, anchor_date, reason, source_ref) "
            "VALUES (%s, %s, %s, %s, 'vendor_revision', %s)",
            (lid, venue_id, session, session, f"raw.vendor_bar:{session}:{vendor_id}"),
        )
    return inserted, revised


def map_listings(
    conn: psycopg.Connection[Any], ix: listings.ListingIndex, eq: pd.DataFrame, session: date
) -> pd.DataFrame:
    """Attach listing_id: by ISIN when the file has one, else by the ticker valid on that session."""
    ids: list[int | None] = []
    for r in eq.itertuples(index=False):
        if r.isin:
            ids.append(ix.by_isin.get(r.isin))
        else:
            ids.append(ix.ticker_as_of(conn, r.symbol, session))
    out = eq.assign(listing_id=ids)
    return out


def ingest_bhavcopy_session(
    conn: psycopg.Connection[Any],
    fetcher: Fetcher,
    session: date,
    *,
    feed: dict[str, Any],
    ix: listings.ListingIndex,
    run_id: uuid.UUID,
) -> SessionResult:
    bf, fetched = nse_archives.fetch_bhavcopy(fetcher, session)
    record_file(conn, feed["feed_id"], session, fetched, run_id)
    if fetched.missing:
        return SessionResult(session, "missing")
    assert fetched.content is not None
    bhav = nse_archives.parse_bhavcopy(fetched.content, bf.fmt, expected_date=session)
    eq = nse_archives.equity_rows(bhav, get_settings().equity_series)
    res = SessionResult(session, "loaded", rows_in_file=len(bhav), equity_rows=len(eq))
    with_isin = eq[eq["isin"] != ""]
    res.reference = listings.observe(
        conn,
        ix,
        with_isin[["isin", "symbol", "name"]],
        session,
        venue_code=feed["venue_code"],
        feed_id=feed["feed_id"],
    )
    mapped = map_listings(conn, ix, eq, session)
    res.unmapped = int(mapped["listing_id"].isna().sum())
    mapped = mapped.dropna(subset=["listing_id"])
    mapped["listing_id"] = mapped["listing_id"].astype(int)
    # Two rows for one listing (the same company under two tickers on a no-ISIN day) are ambiguous: keep none.
    dup = mapped["listing_id"].duplicated(keep=False)
    res.unmapped += int(dup.sum())
    mapped = mapped[~dup]
    res.inserted, res.revised = write_bars(
        conn, mapped, session, vendor_id=feed["vendor_id"], venue_id=feed["venue_id"], run_id=run_id
    )
    if res.inserted == 0 and res.revised == 0:
        res.status = "unchanged"
    return res


def ingest_bhavcopy(
    conn: psycopg.Connection[Any],
    fetcher: Fetcher,
    sessions: list[date],
    *,
    feed_code: str = "xnse_eod_bhavcopy",
    kind: str = "incremental",
) -> list[SessionResult]:
    """Ingest each session oldest first, one transaction per session, under one run."""
    feed = db.feed(conn, feed_code)
    sessions = sorted(sessions)
    if not sessions:
        return []
    results: list[SessionResult] = []
    with db.run_log(conn, f"ingest:{feed_code}", sessions=[str(sessions[0]), str(sessions[-1])]) as run:
        with conn.transaction():
            start_ingest_run(conn, run.run_id, feed["feed_id"], kind, sessions[0], sessions[-1])
        ix = listings.ListingIndex.load(conn, feed["venue_id"])
        for s in sessions:
            with conn.transaction():
                r = ingest_bhavcopy_session(conn, fetcher, s, feed=feed, ix=ix, run_id=run.run_id)
                if r.status != "missing":
                    conn.execute(
                        "INSERT INTO ops.feed_watermark (feed_id, last_session, last_success_at, last_run_id) "
                        "VALUES (%s, %s, now(), %s) ON CONFLICT (feed_id) DO UPDATE SET "
                        "last_session = GREATEST(ops.feed_watermark.last_session, EXCLUDED.last_session), "
                        "last_success_at = now(), last_run_id = EXCLUDED.last_run_id",
                        (feed["feed_id"], s, run.run_id),
                    )
            results.append(r)
            run.add_rows(r.inserted + r.revised)
            if r.status != "missing":
                run.saw_session(s)
        with conn.transaction():
            finish_ingest_run(conn, run.run_id, sum(r.equity_rows - r.unmapped for r in results))
            # A partitioned parent is never auto-analyzed; the clean build plans its joins from this.
            if sum(r.inserted + r.revised for r in results) >= bars.ANALYZE_AFTER_ROWS:
                conn.execute("ANALYZE raw.vendor_bar")
        run.metadata.update(
            loaded=sum(r.status == "loaded" for r in results),
            missing=[str(r.session_date) for r in results if r.status == "missing"],
            new_listings=sum(r.reference.new_listings for r in results),
            ticker_changes=sum(r.reference.ticker_changes for r in results),
            isin_changes=sum(r.reference.isin_changes for r in results),
            queued_for_review=sum(r.reference.queued_for_review for r in results),
            revised=sum(r.revised for r in results),
            unmapped=sum(r.unmapped for r in results),
        )
    return results
