"""Data-quality checks (D11). Each check is a row in ops.dq_check; each result a row in ops.dq_result.

A critical failure stops the run after every result is recorded, so the morning view shows all
problems at once, not just the first.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg

from cagr_data.clean.bars import QA_RETURN_OUTLIER
from cagr_data.reference import corporate_actions


class DataQualityError(RuntimeError):
    def __init__(self, failures: list[CheckResult]) -> None:
        self.failures = failures
        super().__init__("critical checks failed: " + ", ".join(f.check_code for f in failures))


@dataclass
class CheckResult:
    check_code: str
    passed: bool
    observed: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    session_date: date | None = None
    listing_id: int | None = None


@dataclass(frozen=True)
class Ctx:
    conn: psycopg.Connection[Any]
    venue_id: int
    calendar_id: int
    session: date  # the session the run produced
    run_id: uuid.UUID
    thresholds: dict[str, float | None]
    nightly: bool


def _one(conn: psycopg.Connection[Any], sql: str, params: dict[str, Any]) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else next(iter(row.values()))


def _p(c: Ctx) -> dict[str, Any]:
    return {"venue": c.venue_id, "cal": c.calendar_id, "session": c.session}


def check_holiday_mismatch(c: Ctx) -> CheckResult:
    missing = _one(
        c.conn,
        "SELECT count(*) FROM raw.vendor_file_missing m JOIN ref.feed f USING (feed_id) "
        "JOIN ref.calendar_session cs ON cs.calendar_id = %(cal)s AND cs.session_date = m.session_date "
        "WHERE f.venue_id = %(venue)s AND f.dataset = 'eod_bar' AND f.priority = 1 AND m.session_date = %(session)s",
        _p(c),
    )
    on_holiday = _one(
        c.conn,
        "SELECT count(DISTINCT vf.session_date) FROM raw.vendor_file vf JOIN ref.feed f USING (feed_id) "
        "WHERE f.venue_id = %(venue)s AND f.dataset = 'eod_bar' AND f.priority = 1 AND vf.session_date = %(session)s "
        "AND NOT EXISTS (SELECT 1 FROM ref.calendar_session cs WHERE cs.calendar_id = %(cal)s "
        "AND cs.session_date = vf.session_date)",
        _p(c),
    )
    return CheckResult("holiday_mismatch", missing == 0 and on_holiday == 0, float(missing + on_holiday),
                       {"missing_on_session": missing, "file_on_non_session": on_holiday})  # fmt: skip


def check_raw_row_count(c: Ctx) -> CheckResult:
    row = c.conn.execute(
        """
        WITH pv AS (SELECT vendor_id FROM ref.feed WHERE venue_id = %(venue)s AND dataset = 'eod_bar'
                    AND asset_class = 'equity' AND priority = 1),
        counts AS (
            SELECT b.session_date, count(DISTINCT b.listing_id) AS n FROM raw.vendor_bar b JOIN pv USING (vendor_id)
            WHERE b.session_date IN (SELECT session_date FROM ref.calendar_session WHERE calendar_id = %(cal)s
                                     AND session_date <= %(session)s ORDER BY session_date DESC LIMIT 21)
            GROUP BY b.session_date
        )
        SELECT (SELECT n FROM counts WHERE session_date = %(session)s) AS today,
               (SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY n) FROM counts
                 WHERE session_date < %(session)s) AS median,
               (SELECT count(*) FROM counts WHERE session_date < %(session)s) AS history
        """,
        _p(c),
    ).fetchone()
    assert row is not None
    today, median, history = row["today"] or 0, row["median"], row["history"]
    if not median or history < 5:
        return CheckResult(
            "raw_row_count", today > 0, float(today), {"today": today, "note": "too little history"}
        )
    dev = abs(today / median - 1)
    thr = c.thresholds["raw_row_count"] or 0.10
    # A handful of listings arriving or leaving is normal; the check is for a truncated or wrong file.
    ok = dev <= thr or abs(today - median) <= 5
    return CheckResult("raw_row_count", ok, dev, {"today": today, "median": median})


def check_bar_on_calendar(c: Ctx) -> CheckResult:
    n = _one(
        c.conn,
        "SELECT count(*) FROM clean.bar_1d b JOIN ref.listing l USING (listing_id) WHERE l.venue_id = %(venue)s "
        "AND b.bar_date <= %(session)s AND NOT EXISTS (SELECT 1 FROM ref.calendar_session cs "
        "WHERE cs.calendar_id = %(cal)s AND cs.session_date = b.bar_date)",
        _p(c),
    )
    return CheckResult("bar_on_calendar", n == 0, float(n))


def check_calendar_last_session(c: Ctx) -> CheckResult:
    latest = _one(
        c.conn,
        "SELECT max(b.bar_date) FROM clean.bar_1d b JOIN ref.listing l USING (listing_id) "
        "WHERE l.venue_id = %(venue)s AND b.is_traded AND b.bar_date <= %(session)s",
        _p(c),
    )
    return CheckResult("calendar_last_session", latest == c.session, None, {"latest_bar": str(latest)})


def check_coverage(c: Ctx) -> CheckResult:
    # Until universes are built from market cap, the cohort is every listing that traded in each of the
    # five sessions before this one: a stock that traded all week and not today is the gap this check
    # catches. "Any of the five" counted BE, BZ and SME stocks that trade on some days only, and sat
    # at 97-98% on every real session in Jun-Aug 2024; "each of the five" sits at 99.5-99.8%.
    row = c.conn.execute(
        """
        WITH prev AS (SELECT session_date FROM ref.calendar_session WHERE calendar_id = %(cal)s
                      AND session_date < %(session)s ORDER BY session_date DESC LIMIT 5),
        cohort AS (SELECT b.listing_id FROM clean.bar_1d b JOIN ref.listing l USING (listing_id)
                   WHERE l.venue_id = %(venue)s AND b.is_traded AND b.bar_date IN (SELECT session_date FROM prev)
                     AND (l.delisted_on IS NULL OR l.delisted_on > %(session)s)
                   GROUP BY b.listing_id HAVING count(*) = (SELECT count(*) FROM prev))
        SELECT count(*) AS cohort,
               count(*) FILTER (WHERE EXISTS (SELECT 1 FROM clean.bar_1d b WHERE b.listing_id = cohort.listing_id
                                              AND b.bar_date = %(session)s AND b.is_traded)) AS covered
        FROM cohort
        """,
        _p(c),
    ).fetchone()
    assert row is not None
    if row["cohort"] == 0:
        return CheckResult("coverage_latest_session", True, None, {"note": "no cohort yet (first sessions)"})
    share = row["covered"] / row["cohort"]
    thr = c.thresholds["coverage_latest_session"] or 0.98
    return CheckResult("coverage_latest_session", share >= thr, share, dict(row))


def check_prev_close_continuity(c: Ctx) -> CheckResult:
    breaks = corporate_actions.continuity_breaks(c.conn, c.venue_id, [c.session])
    return CheckResult(
        "prev_close_continuity",
        not breaks,
        float(len(breaks)),
        {"breaks": [{k: str(v) for k, v in b.items()} for b in breaks[:50]]},
    )


def check_ca_needs_review(c: Ctx) -> CheckResult:
    n = _one(
        c.conn,
        "SELECT count(*) FROM clean.corporate_action ca JOIN ref.listing l USING (listing_id) "
        "WHERE l.venue_id = %(venue)s AND ca.parse_status = 'needs_review' AND ca.status = 'proposed' "
        "AND ca.ex_date <= (SELECT coalesce(max(session_date), %(session)s) FROM (SELECT session_date "
        "FROM ref.calendar_session WHERE calendar_id = %(cal)s AND session_date > %(session)s "
        "ORDER BY session_date LIMIT 3) s)",
        _p(c),
    )
    return CheckResult("ca_needs_review", n == 0, float(n))


def check_pending_corporate_actions(c: Ctx) -> CheckResult:
    n = _one(
        c.conn,
        "SELECT count(*) FROM clean.corporate_action ca JOIN ref.listing l USING (listing_id) "
        "WHERE l.venue_id = %(venue)s AND ca.status = 'proposed' AND ca.parse_status <> 'needs_review' "
        "AND ca.ex_date <= %(session)s",
        _p(c),
    )
    return CheckResult("pending_corporate_actions", n == 0, float(n))


def check_pending_listing_changes(c: Ctx) -> CheckResult:
    n = _one(c.conn, "SELECT count(*) FROM ref.listing_change WHERE status IN ('proposed','approved')", _p(c))
    return CheckResult("pending_listing_changes", n == 0, float(n))


def check_unknown_isin(c: Ctx) -> CheckResult:
    n = _one(
        c.conn,
        "SELECT count(*) FROM ref.listing_change WHERE kind = 'new_listing' AND status = 'proposed' "
        "AND payload ->> 'venue' = (SELECT code FROM ref.venue WHERE venue_id = %(venue)s)",
        _p(c),
    )
    return CheckResult("unknown_isin_in_bhavcopy", n == 0, float(n))


def check_return_outlier(c: Ctx) -> CheckResult:
    rows = c.conn.execute(
        "SELECT b.listing_id FROM clean.bar_1d b JOIN ref.listing l USING (listing_id) "
        "WHERE l.venue_id = %(venue)s AND b.bar_date = %(session)s AND b.qa_flags & %(bit)s <> 0",
        _p(c) | {"bit": QA_RETURN_OUTLIER},
    ).fetchall()
    return CheckResult(
        "return_outlier", not rows, float(len(rows)), {"listings": [r["listing_id"] for r in rows[:50]]}
    )


def check_history_drift(c: Ctx) -> CheckResult:
    n = _one(
        c.conn,
        "SELECT count(DISTINCT listing_id) FROM clean.dirty_range WHERE venue_id = %(venue)s "
        "AND reason = 'vendor_revision' AND detected_at >= (SELECT started_at FROM public.run_log "
        "WHERE run_id = %(run)s)",
        _p(c) | {"run": c.run_id},
    )
    return CheckResult("history_drift", n == 0, float(n))


def check_feed_freshness(c: Ctx) -> CheckResult:
    stale = c.conn.execute(
        "SELECT code FROM ops.feed_freshness ff JOIN ref.feed f USING (feed_id) "
        "WHERE f.venue_id = %(venue)s AND ff.is_stale AND f.dataset = 'eod_bar' AND f.priority = 1",
        _p(c),
    ).fetchall()
    return CheckResult("feed_freshness", not stale, float(len(stale)), {"stale": [r["code"] for r in stale]})


CHECKS: list[tuple[Callable[[Ctx], CheckResult], bool]] = [
    # (check, nightly only)
    (check_holiday_mismatch, False),
    (check_raw_row_count, False),
    (check_bar_on_calendar, False),
    (check_calendar_last_session, False),
    (check_coverage, False),
    (check_prev_close_continuity, False),
    (check_ca_needs_review, False),
    (check_pending_corporate_actions, False),
    (check_pending_listing_changes, False),
    (check_unknown_isin, False),
    (check_return_outlier, False),
    (check_history_drift, False),
    (check_feed_freshness, True),
]


def run_checks(
    conn: psycopg.Connection[Any],
    venue_id: int,
    session: date,
    run_id: uuid.UUID,
    *,
    nightly: bool,
    raise_on_critical: bool = True,
) -> list[CheckResult]:
    catalogue = {r["check_code"]: r for r in conn.execute("SELECT * FROM ops.dq_check")}
    cal = conn.execute("SELECT calendar_id FROM ref.venue WHERE venue_id = %s", (venue_id,)).fetchone()
    assert cal is not None
    ctx = Ctx(conn, venue_id, cal["calendar_id"], session, run_id,
              {k: v["threshold"] for k, v in catalogue.items()}, nightly)  # fmt: skip
    results = [fn(ctx) for fn, nightly_only in CHECKS if nightly or not nightly_only]
    with conn.transaction():
        for r in results:
            conn.execute(
                "INSERT INTO ops.dq_result (run_id, check_code, venue_id, session_date, listing_id, observed, passed, "
                "detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (run_id, r.check_code, venue_id, r.session_date or session, r.listing_id, r.observed, r.passed,
                 json.dumps(r.detail, default=str)),
            )  # fmt: skip
    failures = [r for r in results if not r.passed and catalogue[r.check_code]["severity"] == "critical"]
    if failures and raise_on_critical:
        raise DataQualityError(failures)
    return results
