"""`cagr` command line: the same steps the Prefect flows run, for laptops, backfills and tests."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from cagr_data import pipeline
from cagr_data.core import db, migrate
from cagr_data.settings import get_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help=__doc__)

DateOpt = Annotated[str, typer.Option(help="YYYY-MM-DD")]
OfflineOpt = Annotated[
    Path | None, typer.Option("--offline", help="Replay saved files from this folder instead of downloading")
]


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _fetchers(offline: Path | None) -> pipeline.Fetchers:
    return pipeline.Fetchers.offline(offline) if offline else pipeline.Fetchers.live()


def _print(obj: Any) -> None:
    typer.echo(json.dumps(obj, indent=2, default=str))


@app.command("migrate")
def migrate_cmd(
    reset: Annotated[bool, typer.Option(help="Drop everything first (dev and test databases only)")] = False,
) -> None:
    """Apply pending schema migrations."""
    url = get_settings().database_url
    if reset:
        migrate.reset(url)
    applied = migrate.migrate(url)
    typer.echo("applied: " + (", ".join(applied) if applied else "nothing, up to date"))


@app.command("calendar-sync")
def calendar_sync(start: DateOpt = "2011-01-01", end: DateOpt = f"{date.today().year + 1}-12-31",
                  offline: OfflineOpt = None) -> None:  # fmt: skip
    """Holidays from NSE, then rebuild the XNSE sessions between start and end."""
    with db.connect() as conn:
        n = pipeline.calendar_sync(conn, _fetchers(offline), _d(start), _d(end))
    typer.echo(f"{n} sessions")


@app.command("calendar-verify")
def calendar_verify(start: DateOpt, end: DateOpt, offline: OfflineOpt = None) -> None:
    """Probe the archive on non-session days; a published bhavcopy proves a special session."""
    with db.connect() as conn:
        _print(pipeline.calendar_verify(conn, _fetchers(offline), _d(start), _d(end)))


@app.command("reference-sync")
def reference_sync(as_of: DateOpt = str(date.today()), offline: OfflineOpt = None) -> None:
    """Listing masters and symbol changes."""
    with db.connect() as conn:
        _print(pipeline.reference_sync(conn, _fetchers(offline), _d(as_of)))


@app.command("corporate-actions")
def corporate_actions(start: DateOpt, end: DateOpt, offline: OfflineOpt = None) -> None:
    """Load NSE corporate actions announced for ex-dates in [start, end]."""
    with db.connect() as conn:
        _print(pipeline.corporate_actions_sync(conn, _fetchers(offline), _d(start), _d(end)))


@app.command("eod")
def eod(
    start: DateOpt,
    end: DateOpt,
    offline: OfflineOpt = None,
    checks: Annotated[bool, typer.Option(help="Run data-quality checks on the last session")] = True,
) -> None:
    """Bhavcopy → raw → clean → checks → lake for every session in [start, end], oldest first."""
    with db.connect() as conn:
        _print(pipeline.eod(conn, _fetchers(offline), _d(start), _d(end), run_checks=checks).__dict__)


@app.command("ingest")
def ingest(start: DateOpt, end: DateOpt, offline: OfflineOpt = None) -> None:
    """Bhavcopy → raw only (with listings and tickers), oldest first. Safe to rerun."""
    with db.connect() as conn:
        _print(pipeline.ingest(conn, _fetchers(offline), _d(start), _d(end)).__dict__)


@app.command("build")
def build(
    start: DateOpt,
    end: DateOpt,
    checks: Annotated[bool, typer.Option(help="Run data-quality checks on the last session")] = True,
) -> None:
    """raw → corporate actions → clean bars → checks → lake, without downloading anything."""
    with db.connect() as conn:
        _print(pipeline.build(conn, _d(start), _d(end), run_checks=checks).__dict__)


@app.command("backfill")
def backfill(start: DateOpt, end: DateOpt, offline: OfflineOpt = None) -> None:
    """Load history into an empty database: calendar, raw, listings, corporate actions, then clean bars."""
    with db.connect() as conn:
        _print(pipeline.backfill(conn, _fetchers(offline), _d(start), _d(end)).__dict__)


@app.command("demo")
def demo(
    folder: Annotated[Path, typer.Option(help="Where to write the sample NSE files")] = Path("data/sample"),
) -> None:
    """Write synthetic NSE files (Sep–Oct 2026) and backfill them offline. No network needed."""
    from cagr_data import sample

    sample.write_all(folder)
    with db.connect() as conn:
        _print(pipeline.backfill(conn, pipeline.Fetchers.offline(folder), sample.START, sample.END).__dict__)


@app.command("nightly")
def nightly(session: DateOpt = str(date.today()), offline: OfflineOpt = None) -> None:
    """One weekday night: reference sync, corporate actions, then EOD for the session."""
    with db.connect() as conn:
        _print(pipeline.nightly(conn, _fetchers(offline), _d(session)).__dict__)


@app.command("status")
def status() -> None:
    """Latest runs, feed freshness and the review queues."""
    with db.connect() as conn:
        runs = conn.execute(
            "SELECT runner, status, started_at, ended_at - started_at AS took, rows_written, max_session, error_msg "
            "FROM public.run_log ORDER BY started_at DESC LIMIT 10"
        ).fetchall()
        fresh = conn.execute(
            "SELECT code, last_session, expected_session, is_stale FROM ops.feed_freshness"
        ).fetchall()
        queues = conn.execute(
            "SELECT (SELECT count(*) FROM ref.listing_change WHERE status IN ('proposed','approved')) AS listing_changes, "
            "(SELECT count(*) FROM clean.corporate_action WHERE status = 'proposed') AS corporate_actions, "
            "(SELECT count(*) FROM clean.corporate_action WHERE parse_status = 'needs_review' "
            " AND status = 'proposed') AS needs_review"
        ).fetchone()
        _print({"runs": runs, "feeds": fresh, "queues": queues})


if __name__ == "__main__":
    app()
