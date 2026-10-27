"""Assets for NSE's daily files (spec 0001, section 5).

Session files are partitioned by calendar day in Asia/Kolkata, every day rather than weekdays,
because NSE trades on days a weekday rule misses. A 404 on a non-session day is the expected
answer. Each asset runs the raw checks through cagr_data.raw.ingest, which records them in
ops.dq_result; the asset reports them to Dagster as asset checks and fails after reporting if
any critical check failed.

Receipt and rows are written in one transaction, so one asset per dataset covers both the file
and its rows (a parser fix is re-applied from stored files by a later re-derive job, never by
editing raw rows).
"""

import os
from collections.abc import Iterator, Sequence
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

import dagster as dg

from cagr_data.core.db import Connection
from cagr_data.core.runs import RunKind, finish_run, start_run
from cagr_data.orchestration.resources import NseClient, PlatformDatabase, RawStorage
from cagr_data.quality.checks import Outcome
from cagr_data.raw.feeds import load_feed
from cagr_data.raw.fetch import FetchOutcome, feeds_for, fetch_and_ingest
from cagr_data.raw.ingest import VENUE, check_arrived
from cagr_data.reference.calendar import record_archive_proof, refresh_calendar

Materialized = dg.MaterializeResult[None]
IST = ZoneInfo("Asia/Kolkata")
START = "2010-01-01"
FIRST_HOLIDAY_LIST = date(2011, 1, 1)
DEADLINE = time(22, 30)

daily = dg.DailyPartitionsDefinition(start_date=START, timezone="Asia/Kolkata", end_offset=1)

CRITICAL = frozenset(
    {"file_shape", "trade_date_matches", "no_duplicates", "row_count", "benchmark_present",
     "file_arrived"}
)  # fmt: skip
BAR_CHECKS = (
    "file_shape", "trade_date_matches", "no_duplicates", "row_count", "holiday_session",
    "prices_positive", "ohlc_order", "isin_present", "vendor_revision", "special_session",
)  # fmt: skip
INDEX_CHECKS = (
    "file_shape", "trade_date_matches", "no_duplicates", "benchmark_present", "holiday_session",
    "vendor_revision", "special_session",
)  # fmt: skip
SNAPSHOT_CHECKS = ("file_shape", "vendor_revision")
PR_CHECKS = ("file_shape", "no_duplicates", "vendor_revision")


def today_ist() -> date:
    return datetime.now(IST).date()


def _specs(asset: str, check_ids: Sequence[str]) -> list[dg.AssetCheckSpec]:
    return [
        dg.AssetCheckSpec(c, asset=asset, blocking=c in CRITICAL, description=c) for c in check_ids
    ]


def _check_results(
    check_ids: Sequence[str], outcomes: Sequence[Outcome], why_not: str
) -> Iterator[dg.AssetCheckResult]:
    """One result per declared check. A check that ran for several feeds (the 2024 overlap)
    passes only if it passed for each; one that did not run says why."""
    by_id: dict[str, list[Outcome]] = {}
    for o in outcomes:
        by_id.setdefault(o.check_id, []).append(o)
    for check_id in check_ids:
        ran = by_id.get(check_id, [])
        if not ran:
            yield dg.AssetCheckResult(
                check_name=check_id, passed=True, metadata={"evaluated": False, "why": why_not}
            )
            continue
        severity = (
            dg.AssetCheckSeverity.ERROR if check_id in CRITICAL else dg.AssetCheckSeverity.WARN
        )
        yield dg.AssetCheckResult(
            check_name=check_id,
            passed=all(o.passed for o in ran),
            severity=severity,
            metadata={"evaluated": True, "observed": [o.observed for o in ran]},
        )


class _Run:
    """An ops.run row for the Dagster run, closed when the asset finishes."""

    def __init__(self, conn: Connection, context: dg.AssetExecutionContext, kind: RunKind):
        self.conn = conn
        self.run_id = start_run(
            conn, kind, code_version=os.environ.get("CAGR_CODE_VERSION"),
            dagster_run_id=context.run.run_id,
        )  # fmt: skip
        conn.commit()

    def finish(self, succeeded: bool) -> None:
        self.conn.rollback()  # anything not yet committed belongs to a failed step
        finish_run(self.conn, self.run_id, succeeded=succeeded)
        self.conn.commit()


RUN_KIND_TAG = "cagr/run_kind"


def _kind(context: dg.AssetExecutionContext) -> RunKind:
    tags = context.run.tags
    if tags.get(RUN_KIND_TAG) == "backfill" or tags.get("dagster/backfill"):
        return "backfill"
    return "nightly"


BHAVCOPY = "nse_cm_bhavcopy"


def no_session(conn: Connection, day: date) -> bool:
    """NSE answered 404 for the bhavcopy of day, at least two days later, and none is stored:
    no session, so no session file of any dataset. A backfill uses it to skip the index and PR
    zip requests on the roughly 110 weekends and holidays a year, and to skip asking again for
    a bhavcopy an earlier backfill was already told is missing. The nightly run does not,
    since on the night a 404 may only mean the file is late."""
    row = conn.execute(
        """
        SELECT NOT EXISTS (SELECT 1 FROM raw.vendor_file f JOIN raw.feed d USING (feed_id)
                            WHERE d.dataset = %(ds)s AND f.partition_date = %(day)s)
           AND EXISTS (SELECT 1 FROM raw.fetch_attempt a JOIN raw.feed d USING (feed_id)
                        WHERE d.dataset = %(ds)s AND a.partition_date = %(day)s
                          AND a.http_status = 404 AND a.attempted_at > %(day)s::date + 2)
           AND NOT EXISTS (SELECT 1 FROM raw.fetch_attempt a JOIN raw.feed d USING (feed_id)
                            WHERE d.dataset = %(ds)s AND a.partition_date = %(day)s
                              AND a.http_status IS DISTINCT FROM 404)
        """,
        {"ds": BHAVCOPY, "day": day},
    ).fetchone()
    return bool(row and row[0])


def _fetch(
    context: dg.AssetExecutionContext,
    db: PlatformDatabase,
    raw: RawStorage,
    nse: NseClient,
    dataset: str,
    day: date,
    kind: RunKind,
    *,
    url_day: date | None = None,
    revision_check: bool = False,
    feed_ids: Sequence[str] | None = None,
) -> tuple[list[FetchOutcome], list[Outcome], list[Outcome]]:
    """Fetch and ingest every feed of dataset for day; commit after each feed."""
    storage = raw.storage()
    with db.connect() as conn:
        feeds = (
            [load_feed(conn, f) for f in feed_ids] if feed_ids else feeds_for(conn, dataset, day)
        )
        if not feeds or (kind == "backfill" and no_session(conn, day)):
            return [], [], []
        run = _Run(conn, context, kind)
        fetched: list[FetchOutcome] = []
        outcomes: list[Outcome] = []
        critical: list[Outcome] = []
        try:
            with nse.client() as client:
                for feed in feeds:
                    o = fetch_and_ingest(conn, client, storage, run_id=run.run_id, feed=feed,
                                         day=day, url_day=url_day,
                                         revision_check=revision_check)  # fmt: skip
                    conn.commit()
                    context.log.info(f"{feed.feed_id} {day}: {o.status} ({o.http_status})")
                    fetched.append(o)
                    if o.result is not None:
                        outcomes += o.result.outcomes
                        critical += o.result.critical
        except BaseException:
            run.finish(succeeded=False)
            raise
        run.finish(succeeded=not critical)
    return fetched, outcomes, critical


def _metadata(fetched: Sequence[FetchOutcome]) -> dict[str, Any]:
    return {
        "files": [
            {
                "feed": f.feed_id,
                "status": f.status,
                "http_status": f.http_status,
                "rows": f.result.rows_loaded if f.result else 0,
                "revision": f.result.receipt.revision if f.result and f.result.receipt else None,
            }
            for f in fetched
        ]
    }


def _session_asset(
    context: dg.AssetExecutionContext,
    db: PlatformDatabase,
    raw: RawStorage,
    nse: NseClient,
    dataset: str,
    check_ids: Sequence[str],
) -> Iterator[dg.AssetCheckResult | Materialized]:
    day = date.fromisoformat(context.partition_key)
    fetched, outcomes, critical = _fetch(context, db, raw, nse, dataset, day, _kind(context))
    why = (
        ", ".join(f"{f.feed_id}: {f.status}" for f in fetched)
        or "no feed publishes this day, or no session"
    )
    yield from _check_results(check_ids, outcomes, why)
    yield dg.MaterializeResult(metadata=_metadata(fetched))
    if critical:
        raise dg.Failure(f"critical checks failed: {', '.join(o.check_id for o in critical)}")


@dg.asset(
    partitions_def=daily,
    check_specs=_specs("nse_cm_bhavcopy", BAR_CHECKS),
    group_name="nse",
    description="One session's capital-market bhavcopy (legacy and/or UDiFF) in raw.nse_cm_bar",
)
def nse_cm_bhavcopy(
    context: dg.AssetExecutionContext, db: PlatformDatabase, raw: RawStorage, nse: NseClient
) -> Iterator[dg.AssetCheckResult | Materialized]:
    yield from _session_asset(context, db, raw, nse, BHAVCOPY, BAR_CHECKS)


@dg.asset(
    partitions_def=daily,
    check_specs=_specs("nse_index_close", INDEX_CHECKS),
    group_name="nse",
    description="One session's index closes (ind_close_all) in raw.nse_index_close",
)
def nse_index_close(
    context: dg.AssetExecutionContext, db: PlatformDatabase, raw: RawStorage, nse: NseClient
) -> Iterator[dg.AssetCheckResult | Materialized]:
    yield from _session_asset(context, db, raw, nse, "nse_index_close", INDEX_CHECKS)


@dg.asset(
    partitions_def=daily,
    check_specs=_specs("nse_mcap", PR_CHECKS),
    group_name="nse",
    description=(
        "One session's shares outstanding and market cap (the PR zip's mcap file, from"
        " 1 Feb 2024) in raw.nse_mcap"
    ),
)
def nse_mcap(
    context: dg.AssetExecutionContext, db: PlatformDatabase, raw: RawStorage, nse: NseClient
) -> Iterator[dg.AssetCheckResult | Materialized]:
    yield from _session_asset(context, db, raw, nse, "nse_mcap", PR_CHECKS)


@dg.asset(
    partitions_def=daily,
    check_specs=_specs("nse_index_issue_cap", PR_CHECKS),
    group_name="nse",
    description=(
        "One session's issued capital of index constituents (the PR zip's Ix and ffix files,"
        " 2010 to 2013) in raw.nse_index_issue_cap"
    ),
)
def nse_index_issue_cap(
    context: dg.AssetExecutionContext, db: PlatformDatabase, raw: RawStorage, nse: NseClient
) -> Iterator[dg.AssetCheckResult | Materialized]:
    yield from _session_asset(context, db, raw, nse, "nse_index_issue_cap", PR_CHECKS)


@dg.asset(
    check_specs=_specs("nse_holidays", SNAPSHOT_CHECKS),
    group_name="nse",
    description=(
        "NSE's holiday lists from 2011 (the first year the API has) to next year. Past years are"
        " fetched once; this year and next are fetched again each week, and a changed list is a"
        " revision"
    ),
)
def nse_holidays(
    context: dg.AssetExecutionContext, db: PlatformDatabase, raw: RawStorage, nse: NseClient
) -> Iterator[dg.AssetCheckResult | Materialized]:
    year = today_ist().year
    fetched: list[FetchOutcome] = []
    outcomes: list[Outcome] = []
    critical: list[Outcome] = []
    for y in range(FIRST_HOLIDAY_LIST.year, year + 2):
        jan1 = date(y, 1, 1)
        f, o, c = _fetch(context, db, raw, nse, "nse_holidays", jan1, "manual",
                         revision_check=y >= year, feed_ids=["nse_holidays"])  # fmt: skip
        fetched += f
        outcomes += o
        critical += c
    yield from _check_results(SNAPSHOT_CHECKS, outcomes, "no new file")
    yield dg.MaterializeResult(metadata=_metadata(fetched))
    if critical:
        raise dg.Failure("the holiday list could not be read")


@dg.asset(
    deps=[nse_holidays],
    group_name="nse",
    description="ref.calendar_session for XNSE from 2010 to the end of next year",
)
def xnse_calendar(context: dg.AssetExecutionContext, db: PlatformDatabase) -> Materialized:
    end = date(today_ist().year + 1, 12, 31)
    with db.connect() as conn:
        proved = record_archive_proof(conn, VENUE, "nse_cm_bhavcopy", FIRST_HOLIDAY_LIST)
        sessions = refresh_calendar(conn, VENUE, end)
        conn.commit()
    context.log.info(f"{proved} new archive-proof exceptions; {sessions} sessions to {end}")
    return dg.MaterializeResult(
        metadata={"sessions": sessions, "end": str(end), "archive_proof_added": proved}
    )


def _snapshot(dataset: str, description: str) -> dg.AssetsDefinition:
    @dg.asset(
        name=dataset,
        check_specs=_specs(dataset, SNAPSHOT_CHECKS),
        group_name="nse",
        description=description,
    )
    def snapshot(
        context: dg.AssetExecutionContext, db: PlatformDatabase, raw: RawStorage, nse: NseClient
    ) -> Iterator[dg.AssetCheckResult | Materialized]:
        fetched, outcomes, critical = _fetch(
            context, db, raw, nse, dataset, today_ist(), "manual", feed_ids=[dataset]
        )
        yield from _check_results(SNAPSHOT_CHECKS, outcomes, "already stored today")
        yield dg.MaterializeResult(metadata=_metadata(fetched))
        if critical:
            raise dg.Failure(f"{dataset} could not be read")

    return snapshot


nse_equity_list = _snapshot("nse_equity_list", "Today's EQUITY_L.csv in raw.nse_equity_list")
nse_sme_equity_list = _snapshot(
    "nse_sme_equity_list", "Today's SME_EQUITY_L.csv in raw.nse_equity_list"
)
nse_symbol_change = _snapshot(
    "nse_symbol_change", "Today's symbolchange.csv in raw.nse_symbol_change"
)
nse_etf_list = _snapshot("nse_etf_list", "Today's eq_etfseclist.csv in raw.nse_etf_list")

CORPORATE_ACTION_DATASETS = ("nse_corporate_actions", "nse_sme_corporate_actions")
FIRST_CORPORATE_ACTIONS = date(2010, 1, 1)


@dg.asset(
    check_specs=_specs("nse_corporate_actions", SNAPSHOT_CHECKS),
    group_name="nse",
    description=(
        "NSE's corporate actions (main board and SME), one file per year of ex-dates, in"
        " raw.nse_corporate_action. Missing years are fetched; this year and next are fetched"
        " again each run, so a new announcement becomes a new revision"
    ),
)
def nse_corporate_actions(
    context: dg.AssetExecutionContext, db: PlatformDatabase, raw: RawStorage, nse: NseClient
) -> Iterator[dg.AssetCheckResult | Materialized]:
    year = today_ist().year
    fetched: list[FetchOutcome] = []
    outcomes: list[Outcome] = []
    critical: list[Outcome] = []
    for dataset in CORPORATE_ACTION_DATASETS:
        for y in range(FIRST_CORPORATE_ACTIONS.year, year + 2):
            f, o, c = _fetch(context, db, raw, nse, dataset, date(y, 1, 1), "manual",
                             revision_check=y >= year, feed_ids=[dataset])  # fmt: skip
            fetched += f
            outcomes += o
            critical += c
    yield from _check_results(SNAPSHOT_CHECKS, outcomes, "no new file")
    yield dg.MaterializeResult(metadata=_metadata(fetched))
    if critical:
        raise dg.Failure("a corporate-actions response could not be read")


def deadline_passed(day: date, now: datetime) -> bool:
    """Files for day count as missing from 22:30 IST that evening."""
    return now.astimezone(IST) >= datetime.combine(day, DEADLINE, IST)


@dg.asset(
    partitions_def=daily,
    deps=[nse_cm_bhavcopy, nse_index_close, xnse_calendar],
    check_specs=_specs("nse_session_complete", ("file_arrived",)),
    group_name="nse",
    description="An expected session has its bhavcopy and index file by 22:30 IST",
)
def nse_session_complete(
    context: dg.AssetExecutionContext, db: PlatformDatabase
) -> Iterator[dg.AssetCheckResult | Materialized]:
    day = date.fromisoformat(context.partition_key)
    outcomes: list[Outcome] = []
    critical: list[Outcome] = []
    with db.connect() as conn:
        row = conn.execute(
            "SELECT is_session FROM ref.calendar_session WHERE venue_id = %s AND date = %s",
            (VENUE, day),
        ).fetchone()
        if row is None:
            why = "the calendar does not cover this day yet"
        elif not row[0]:
            why = "not a session"
        elif not deadline_passed(day, datetime.now(IST)):
            why = "before the 22:30 deadline"
        else:
            run = _Run(conn, context, _kind(context))
            result = check_arrived(conn, run_id=run.run_id, day=day)
            outcomes, critical = result.outcomes, result.critical
            conn.commit()
            run.finish(succeeded=not critical)
            why = ""
    yield from _check_results(("file_arrived",), outcomes, why)
    yield dg.MaterializeResult(metadata={"evaluated": bool(outcomes), "why": why or None})
    if critical:
        raise dg.Failure(f"missing on {day}: {critical[0].observed['missing']}")
