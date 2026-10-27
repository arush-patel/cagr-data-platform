"""One file in: parse, store, receipt, rows and raw checks, in the caller's transaction.

The Dagster assets (task 6) call this and nothing more clever: fetch the bytes, call ingest_file
(and check_arrived after the deadline), commit, then raise_on_critical(result.critical).
"""

from dataclasses import dataclass, field
from datetime import date

import polars as pl

from cagr_data.core.db import Connection
from cagr_data.core.storage import Storage
from cagr_data.quality import checks as qc
from cagr_data.quality.runner import Check, load_checks, record
from cagr_data.raw.feeds import FORMATS, Feed
from cagr_data.raw.loaders import PARTITIONED, analyze, load_rows
from cagr_data.raw.receipts import Receipt, receive
from cagr_data.reference.calendar import (
    CalendarException,
    holiday_exceptions,
    record_exceptions,
)
from cagr_data.vendors.errors import VendorFileError

VENUE = "XNSE"
BAR_KEYS = ("symbol", "series")
INDEX_KEYS = ("index_name",)


@dataclass(frozen=True)
class IngestResult:
    receipt: Receipt | None
    rows_loaded: int
    outcomes: list[qc.Outcome] = field(default_factory=list)
    critical: list[qc.Outcome] = field(default_factory=list)


def _previous_row_counts(conn: Connection, dataset: str, day: date, sessions: int) -> list[int]:
    rows = conn.execute(
        "SELECT row_count FROM raw.vendor_file_current WHERE dataset = %s AND partition_date < %s"
        " ORDER BY partition_date DESC LIMIT %s",
        (dataset, day, sessions),
    ).fetchall()
    return [int(r[0]) for r in rows]


def _arrival(conn: Connection, day: date) -> list[qc.Outcome]:
    """holiday_session and special_session; an arrival on a weekend or a listed holiday is
    recorded as a special session (spec 0001, section 7.2)."""
    weekend = conn.execute("SELECT weekend_days FROM ref.venue WHERE venue_id = %s", (VENUE,))
    weekend_days = weekend.fetchone()[0]  # type: ignore[index]
    listed = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM ref.calendar_exception"
        " WHERE venue_id = %s AND date = %s AND kind = 'holiday')",
        (VENUE, day),
    ).fetchone()[0]  # type: ignore[index]
    outcomes = qc.arrival(day, weekend_days, listed)
    if not all(o.passed for o in outcomes):
        record_exceptions(conn, VENUE, [CalendarException(day, "special_session", "archive_proof")])
    return outcomes


def _content_checks(
    conn: Connection, checks: dict[str, Check], feed: Feed, day: date, frame: pl.DataFrame
) -> list[qc.Outcome]:
    if feed.dataset == "nse_cm_bhavcopy":
        rc = checks.get("row_count")
        previous = (
            _previous_row_counts(conn, feed.dataset, day, rc.params["lookback_sessions"])
            if rc
            else []
        )
        return [
            qc.no_duplicates(frame, BAR_KEYS),
            qc.prices_positive(frame),
            qc.ohlc_order(frame, checks["ohlc_order"].params if "ohlc_order" in checks else None),
            *([qc.isin_present(frame, day, checks["isin_present"].params)]
              if "isin_present" in checks else []),
            *([qc.row_count(frame.height, previous, rc.params)] if rc else []),
            *_arrival(conn, day),
        ]  # fmt: skip
    if feed.dataset == "nse_index_close":
        return [
            qc.no_duplicates(frame, INDEX_KEYS),
            *([qc.benchmark_present(frame, checks["benchmark_present"].params)]
              if "benchmark_present" in checks else []),
            *_arrival(conn, day),
        ]  # fmt: skip
    return []


def ingest_file(
    conn: Connection,
    storage: Storage,
    *,
    run_id: int,
    feed: Feed,
    day: date,
    url: str,
    content: bytes,
) -> IngestResult:
    """Ingest one downloaded file for a partition. Running it again with the same bytes writes
    no new receipt or rows (spec 0001, section 7.3); new bytes become the next revision."""
    checks = load_checks(conn)
    spec = FORMATS[feed.format]
    try:
        frame = spec.parse(content, day)
    except VendorFileError as e:
        outcomes = qc.parse_outcomes(e)
        critical = record(conn, checks, run_id=run_id, day=day, outcomes=outcomes)
        return IngestResult(None, 0, outcomes, critical)

    receipt = receive(
        conn,
        storage,
        run_id=run_id,
        feed=feed,
        day=day,
        url=url,
        content=content,
        row_count=frame.height,
    )
    if not receipt.is_new:
        return IngestResult(receipt, 0)

    loaded = load_rows(conn, spec.table, receipt.file_id, frame)
    if spec.table in PARTITIONED:
        analyze(conn, spec.table)
    if feed.format == "holiday_master_v1":
        record_exceptions(conn, VENUE, holiday_exceptions(frame, receipt.file_id))

    outcomes = [
        *qc.parse_outcomes(None),
        *_content_checks(conn, checks, feed, day, frame),
        qc.vendor_revision(receipt.revision, receipt.supersedes_file_id),
    ]
    critical = record(conn, checks, run_id=run_id, day=day, outcomes=outcomes)
    return IngestResult(receipt, loaded, outcomes, critical)


def check_arrived(conn: Connection, *, run_id: int, day: date) -> IngestResult:
    """After the deadline on an expected session: is there a file for each dataset the check
    names that publishes on that day? (Index closes start on 21 Feb 2012.)"""
    checks = load_checks(conn)
    check = checks.get("file_arrived")
    if check is None:
        return IngestResult(None, 0)
    published = conn.execute(
        "SELECT DISTINCT dataset FROM raw.feed WHERE active AND dataset = ANY(%s)"
        " AND (available_from IS NULL OR available_from <= %s)"
        " AND (available_to IS NULL OR available_to >= %s)",
        (check.params["datasets"], day, day),
    ).fetchall()
    present = {}
    for (dataset,) in published:
        row = conn.execute(
            "SELECT EXISTS (SELECT 1 FROM raw.vendor_file_current"
            " WHERE dataset = %s AND partition_date = %s)",
            (dataset, day),
        ).fetchone()
        present[dataset] = bool(row and row[0])
    outcomes = [qc.file_arrived(present)]
    critical = record(conn, checks, run_id=run_id, day=day, outcomes=outcomes)
    return IngestResult(None, 0, outcomes, critical)
