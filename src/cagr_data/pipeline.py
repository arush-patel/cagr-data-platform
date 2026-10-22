"""The pipeline's steps, wired together. Flows and the CLI call these; they hold no logic of their own.

calendar-sync      holidays from NSE → ref.calendar_exception → ref.calendar_session
calendar-verify    bhavcopy on non-session days → special sessions (D7)
reference-sync     EQUITY_L, SME_EQUITY_L, symbolchange → ref.listing_change → listings and tickers
corporate-actions  NSE corporate actions → clean.corporate_action (proposed / needs_review)
eod                bhavcopy → raw → confirm and apply actions → clean bars → checks → lake
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import httpx
import psycopg

from cagr_data.clean import bars
from cagr_data.core import db
from cagr_data.ingest import eod as eod_ingest
from cagr_data.lake import export
from cagr_data.quality import checks
from cagr_data.reference import calendar, corporate_actions, listings
from cagr_data.settings import get_settings
from cagr_data.vendors import nse_api, nse_archives
from cagr_data.vendors.http import DirectoryFetcher, Fetcher, HttpFetcher

VENUE = "XNSE"


@dataclass
class Fetchers:
    archives: Fetcher
    api: Fetcher

    @classmethod
    def live(cls) -> Fetchers:
        root = get_settings().raw_file_root
        return cls(
            archives=HttpFetcher(root / "nse_archives", rate_limit_per_min=30),
            api=HttpFetcher(root / "nse_api", rate_limit_per_min=10, warmup_url=nse_api.HOME,
                            headers={"Referer": nse_api.HOME}),
        )  # fmt: skip

    @classmethod
    def offline(cls, root: Path) -> Fetchers:
        """Replay saved files: <root>/nse_archives/… and <root>/nse_api/…, laid out like the cache."""
        return cls(archives=DirectoryFetcher(root / "nse_archives"), api=DirectoryFetcher(root / "nse_api"))


def _venue(conn: psycopg.Connection[Any]) -> tuple[int, int]:
    row = conn.execute("SELECT venue_id, calendar_id FROM ref.venue WHERE code = %s", (VENUE,)).fetchone()
    assert row is not None
    return int(row["venue_id"]), int(row["calendar_id"])


# ---------------------------------------------------------------------------------------------


def calendar_sync(conn: psycopg.Connection[Any], f: Fetchers, start: date, end: date) -> int:
    feed = db.feed(conn, "xnse_holidays")
    with db.run_log(conn, "calendar-sync", start=start, end=end) as run:
        url, key = nse_api.holidays_url()
        fetched, payload = nse_api.fetch_json(f.api, url, key)
        hol = nse_api.parse_holidays(payload)
        with conn.transaction():
            eod_ingest.start_ingest_run(conn, run.run_id, feed["feed_id"], "full_refresh", start, end)
            eod_ingest.record_file(conn, feed["feed_id"], None, fetched, run.run_id)
            calendar.record_holidays(conn, VENUE, hol, "nse_holiday_api")
        n = calendar.rebuild(conn, VENUE, start, end)
        run.add_rows(n)
        run.metadata.update(holidays=len(hol), sessions=n)
    return n


def calendar_verify(
    conn: psycopg.Connection[Any], f: Fetchers, start: date, end: date
) -> dict[str, list[str]]:
    """Probe the archive on every day the calendar calls closed. A file there proves a special session."""
    _, cal_id = _venue(conn)
    feed = db.feed(conn, "xnse_eod_bhavcopy")
    found: list[str] = []
    with db.run_log(conn, "calendar-verify", start=start, end=end) as run:
        with conn.transaction():
            eod_ingest.start_ingest_run(conn, run.run_id, feed["feed_id"], "backfill", start, end)
        sessions = set(calendar.sessions_between(conn, cal_id, start, end))
        d = start
        while d <= end:
            if d not in sessions:
                bf, fetched = nse_archives.fetch_bhavcopy(f.archives, d)
                if not fetched.missing:
                    with conn.transaction():
                        eod_ingest.record_file(conn, feed["feed_id"], d, fetched, run.run_id)
                        calendar.record_special_session(conn, VENUE, d, f"bhavcopy published ({bf.fmt})")
                    found.append(str(d))
            d += timedelta(days=1)
        if found:
            first = conn.execute(
                "SELECT min(session_date) AS lo, max(session_date) AS hi FROM ref.calendar_session "
                "WHERE calendar_id = %s",
                (cal_id,),
            ).fetchone()
            assert first is not None
            calendar.rebuild(conn, VENUE, min(first["lo"], start), max(first["hi"], end))
        run.metadata.update(special_sessions=found)
    return {"special_sessions": found}


def reference_sync(
    conn: psycopg.Connection[Any], f: Fetchers, as_of: date, symbol_lookback_days: int = 7
) -> dict[str, Any]:
    venue_id, _ = _venue(conn)
    master_feed = db.feed(conn, "xnse_listing_master")
    sym_feed = db.feed(conn, "xnse_symbol_change")
    out: dict[str, Any] = {}
    with db.run_log(conn, "reference-sync", as_of=as_of) as run:
        ix = listings.ListingIndex.load(conn, venue_id)
        with conn.transaction():
            eod_ingest.start_ingest_run(
                conn, run.run_id, master_feed["feed_id"], "full_refresh", as_of, as_of
            )
            for url, key in (
                (nse_archives.EQUITY_L_URL, f"masters/{as_of:%Y%m%d}/EQUITY_L.csv"),
                (nse_archives.SME_EQUITY_L_URL, f"masters/{as_of:%Y%m%d}/SME_EQUITY_L.csv"),
            ):
                fetched = f.archives.get(url, key)
                eod_ingest.record_file(conn, master_feed["feed_id"], None, fetched, run.run_id)
                if fetched.content is None:
                    out[key] = "missing"
                    continue
                master = nse_archives.parse_listing_master(fetched.content)
                r = listings.sync_master(
                    conn, ix, master, as_of, venue_code=VENUE, feed_id=master_feed["feed_id"]
                )
                out[key] = {"rows": len(master), "new": r.new_listings, "tickers": r.ticker_changes,
                            "review": r.queued_for_review}  # fmt: skip
                run.add_rows(r.new_listings + r.ticker_changes)
            fetched = f.archives.get(
                nse_archives.SYMBOL_CHANGE_URL, f"masters/{as_of:%Y%m%d}/symbolchange.csv"
            )
            eod_ingest.record_file(conn, sym_feed["feed_id"], None, fetched, run.run_id)
            if fetched.content is not None:
                changes = nse_archives.parse_symbol_changes(fetched.content)
                r = listings.apply_symbol_changes(
                    conn,
                    ix,
                    changes,
                    since=as_of - timedelta(days=symbol_lookback_days),
                    feed_id=sym_feed["feed_id"],
                )
                out["symbol_changes"] = r.ticker_changes
        run.metadata.update(out)
    return out


def corporate_actions_sync(
    conn: psycopg.Connection[Any], f: Fetchers, start: date, end: date
) -> dict[str, Any]:
    """D3 cadence: today−7 … today+30 nightly; any window for a backfill."""
    venue_id, _ = _venue(conn)
    feed = db.feed(conn, "xnse_corporate_actions")
    with db.run_log(conn, "corporate-actions", start=start, end=end) as run:
        url, key = nse_api.corporate_actions_url(start, end)
        fetched, payload = nse_api.fetch_json(f.api, url, key)
        actions = nse_api.parse_corporate_actions(payload)
        with conn.transaction():
            eod_ingest.start_ingest_run(conn, run.run_id, feed["feed_id"], "incremental", start, end)
            eod_ingest.record_file(conn, feed["feed_id"], None, fetched, run.run_id)
            res = corporate_actions.load(conn, venue_id, actions)
        run.add_rows(res.inserted)
        out = {"announced": len(actions), "inserted": res.inserted, "needs_review": res.needs_review,
               "unknown": res.unknown_isin[:50]}  # fmt: skip
        run.metadata.update(out)
    return out


@dataclass
class EodResult:
    sessions: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    actions: dict[str, int] = field(default_factory=dict)
    clean_rows: int = 0
    dirty_rebuilt: int = 0
    checks: dict[str, bool] = field(default_factory=dict)
    lake_files: int = 0


def ingest(
    conn: psycopg.Connection[Any], f: Fetchers, start: date, end: date, *, nightly: bool = False
) -> EodResult:
    """Primary EOD files → raw.vendor_bar, creating listings and tickers as they appear. Oldest first."""
    _, cal_id = _venue(conn)
    sessions = calendar.sessions_between(conn, cal_id, start, end)
    if not sessions:
        raise ValueError(f"no {VENUE} sessions between {start} and {end}; run calendar-sync first")
    results = eod_ingest.ingest_bhavcopy(
        conn, f.archives, sessions, kind="incremental" if nightly else "backfill"
    )
    return EodResult(
        sessions=[str(s) for s in sessions],
        missing=[str(r.session_date) for r in results if r.status == "missing"],
    )


def build(
    conn: psycopg.Connection[Any],
    start: date,
    end: date,
    *,
    nightly: bool = False,
    run_checks: bool = True,
    lake_root: Path | None = None,
    result: EodResult | None = None,
) -> EodResult:
    """raw → confirmed and applied actions → clean bars → checks → lake, for the sessions in [start, end]."""
    venue_id, cal_id = _venue(conn)
    sessions = calendar.sessions_between(conn, cal_id, start, end)
    out = result or EodResult(sessions=[str(s) for s in sessions])
    loaded = [
        r["session_date"]
        for r in conn.execute(
            "SELECT DISTINCT b.session_date FROM raw.vendor_bar b JOIN ref.feed fd ON fd.vendor_id = b.vendor_id "
            "AND fd.venue_id = %s AND fd.dataset = 'eod_bar' AND fd.priority = 1 "
            "WHERE b.session_date BETWEEN %s AND %s ORDER BY 1",
            (venue_id, start, end),
        )
    ]

    with db.run_log(conn, "clean", start=start, end=end) as run:
        with conn.transaction():
            out.actions = corporate_actions.confirm(conn, venue_id)
            breaks = corporate_actions.continuity_breaks(conn, venue_id, loaded) if loaded else []
            out.actions["detected"] = corporate_actions.record_detected(conn, breaks)
            out.actions["applied"] = corporate_actions.apply_confirmed(conn, venue_id, end)
        with conn.transaction():
            out.clean_rows = bars.build_1d(conn, venue_id, start, end)
            bars.resample(conn, venue_id, start, end)
            out.dirty_rebuilt = bars.rebuild_dirty(conn, venue_id, end)
        run.add_rows(out.clean_rows)
        run.saw_session(max(loaded) if loaded else None)
        run.metadata.update(actions=out.actions, dirty_rebuilt=out.dirty_rebuilt)

    if run_checks and loaded:
        with db.run_log(conn, "checks", session=max(loaded)) as run:
            res = checks.run_checks(
                conn, venue_id, max(loaded), run.run_id, nightly=nightly, raise_on_critical=False
            )
            out.checks = {r.check_code: r.passed for r in res}
            run.metadata.update(failed=[r.check_code for r in res if not r.passed])
            critical = {
                r["check_code"]
                for r in conn.execute("SELECT check_code FROM ops.dq_check WHERE severity = 'critical'")
            }
            failed = [r for r in res if not r.passed and r.check_code in critical]
            if failed:
                raise checks.DataQualityError(failed)

    root = lake_root or get_settings().lake_root
    with db.run_log(conn, "lake-export", start=start, end=end) as run:
        files = export.export_bars(conn, root, venue_id, sorted({s.year for s in sessions}))
        files.append(export.export_reference(conn, root, venue_id))
        out.lake_files = len(files)
        run.add_rows(len(files))
    return out


def eod(
    conn: psycopg.Connection[Any],
    f: Fetchers,
    start: date,
    end: date,
    *,
    nightly: bool = False,
    run_checks: bool = True,
    lake_root: Path | None = None,
) -> EodResult:
    """ingest then build: everything from the primary EOD file to the lake."""
    res = ingest(conn, f, start, end, nightly=nightly)
    return build(conn, start, end, nightly=nightly, run_checks=run_checks, lake_root=lake_root, result=res)


def nightly(conn: psycopg.Connection[Any], f: Fetchers, session: date) -> EodResult:
    """The weekday night for one session: reference and corporate actions first, then prices."""
    reference_sync(conn, f, session)
    # The API is the secondary path: an action it misses is caught by the previous-close check.
    with contextlib.suppress(FileNotFoundError, httpx.HTTPError):
        corporate_actions_sync(conn, f, session - timedelta(days=7), session + timedelta(days=30))
    return eod(conn, f, session, session, nightly=True)


def backfill(
    conn: psycopg.Connection[Any],
    f: Fetchers,
    start: date,
    end: date,
    *,
    action_window_days: int = 90,
    lake_root: Path | None = None,
) -> EodResult:
    """Load history into an empty database, in the order that keeps every change date-forward:

    1. calendar (holidays, then special sessions proven by the archive)
    2. raw bars oldest first, creating listings and tickers as they appear
    3. today's listing master (real listing dates, names, listings that never traded)
    4. corporate actions over the whole window, in chunks
    5. clean bars, checks on the last session, lake export
    """
    calendar_sync(conn, f, start, date(end.year + 1, 12, 31))
    calendar_verify(conn, f, start, end)
    res = ingest(conn, f, start, end)
    reference_sync(conn, f, end)
    lo = start
    while lo <= end:
        hi = min(lo + timedelta(days=action_window_days - 1), end)
        with contextlib.suppress(FileNotFoundError):
            corporate_actions_sync(conn, f, lo, hi)
        lo = hi + timedelta(days=1)
    return build(conn, start, end, lake_root=lake_root, result=res)
