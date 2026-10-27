"""The venue calendar: one row per calendar day, derived from the venue's weekend and its
exceptions (spec 0001, sections 6.2 and 7.2).

The rules, in order:
- A special session is a session, on any day. It wins over a holiday on the same date (Muhurat
  trading on Diwali).
- A listed holiday is not a session, even on a weekend (the reason says holiday).
- Otherwise a weekend day is not a session and a weekday is a regular session.

session_index counts sessions densely from CALENDAR_START, so the calendar is always rebuilt
whole: a new exception early on moves every index after it.

Exceptions come from two places. The holiday API lists holidays from 2011 (source holiday_api).
Before that, and for sessions the list does not mention, the archive is the proof: a weekday
with a 404 between days that have files is a holiday, and a weekend day with a file is a special
session (source archive_proof).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

import polars as pl
import psycopg

CALENDAR_START = date(2010, 1, 1)

Kind = Literal["holiday", "special_session"]
Source = Literal["holiday_api", "archive_proof"]
Reason = Literal["regular", "weekend", "holiday", "special_session"]


@dataclass(frozen=True)
class CalendarException:
    date: date
    kind: Kind
    source: Source
    description: str | None = None
    source_file_id: int | None = None


@dataclass(frozen=True)
class CalendarDay:
    date: date
    is_session: bool
    session_index: int | None
    reason: Reason
    is_week_end: bool = False
    is_month_end: bool = False


def _days(start: date, end: date) -> Iterable[date]:
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def _week_last_day(day: date) -> date:
    return day + timedelta(days=7 - day.isoweekday())


def _month_last_day(day: date) -> date:
    first_of_next = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first_of_next - timedelta(days=1)


def build_calendar(
    start: date,
    end: date,
    weekend_days: Sequence[int],
    exceptions: Iterable[CalendarException],
) -> list[CalendarDay]:
    """Every day from start to end. weekend_days are ISO weekdays (6 = Saturday, 7 = Sunday).

    The last session of a week or month is flagged only when the week or month ends within the
    range, so a calendar that stops on a Wednesday does not call that Wednesday a week end.
    """
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    holidays: set[date] = set()
    specials: set[date] = set()
    for e in exceptions:
        (holidays if e.kind == "holiday" else specials).add(e.date)
    weekend = set(weekend_days)

    reasons: list[tuple[date, Reason]] = []
    for day in _days(start, end):
        if day in specials:
            reasons.append((day, "special_session"))
        elif day in holidays:
            reasons.append((day, "holiday"))
        elif day.isoweekday() in weekend:
            reasons.append((day, "weekend"))
        else:
            reasons.append((day, "regular"))

    sessions = [d for d, r in reasons if r in ("regular", "special_session")]
    week_ends: set[date] = set()
    month_ends: set[date] = set()
    for this, following in zip(sessions, [*sessions[1:], None], strict=True):
        if following is None:
            if _week_last_day(this) <= end:
                week_ends.add(this)
            if _month_last_day(this) <= end:
                month_ends.add(this)
            continue
        if following.isocalendar()[:2] != this.isocalendar()[:2]:
            week_ends.add(this)
        if (following.year, following.month) != (this.year, this.month):
            month_ends.add(this)

    index = {d: i for i, d in enumerate(sessions, start=1)}
    return [
        CalendarDay(
            date=d,
            is_session=d in index,
            session_index=index.get(d),
            reason=r,
            is_week_end=d in week_ends,
            is_month_end=d in month_ends,
        )
        for d, r in reasons
    ]


def holiday_exceptions(
    holidays: pl.DataFrame, file_id: int | None = None, segment: str = "CM"
) -> list[CalendarException]:
    """Holidays of one segment from a parsed holiday-master response (see vendors.nse.holidays).

    NSE also lists trading days there: "Special Live Trading" (Sat 2 Mar 2024) is a session, so
    it is left out. Muhurat trading stays a holiday; its file is recorded as a special session
    with a holiday_session warning (section 7.2)."""
    listed = holidays.filter(pl.col("segment") == segment)
    trading = pl.col("description").str.contains(r"(?i)special live trading").fill_null(False)
    rows = listed.filter(~trading).sort("date").iter_rows(named=True)
    return [
        CalendarException(
            date=r["date"],
            kind="holiday",
            source="holiday_api",
            description=r["description"],
            source_file_id=file_id,
        )
        for r in rows
    ]


def archive_exceptions(
    found: Iterable[date],
    missing: Iterable[date],
    weekend_days: Sequence[int],
    since: date | None = None,
) -> list[CalendarException]:
    """Exceptions proved by the archive: which days had a file (200) and which had none (404).

    A weekday without a file is a holiday only before the last day with a file, so a file that
    is merely late (tonight's) is not mistaken for a holiday, and only from `since` (the day the
    archive is known to start) or else after the first day with a file. A weekend day with a
    file is a special session.
    """
    found = sorted(set(found))
    weekend = set(weekend_days)
    result = [
        CalendarException(date=d, kind="special_session", source="archive_proof")
        for d in found
        if d.isoweekday() in weekend
    ]
    if found:
        first, last = found[0], found[-1]
        result += [
            CalendarException(date=d, kind="holiday", source="archive_proof")
            for d in sorted(set(missing) - set(found))
            if (since <= d if since else first < d) and d < last and d.isoweekday() not in weekend
        ]
    return sorted(result, key=lambda e: (e.date, e.kind))


def record_exceptions(
    conn: psycopg.Connection[Any], venue_id: str, exceptions: Iterable[CalendarException]
) -> int:
    """Insert exceptions not yet recorded; a fact already recorded is kept as it was.

    Returns how many were new.
    """
    added = 0
    with conn.cursor() as cur:
        for e in exceptions:
            cur.execute(
                """
                INSERT INTO ref.calendar_exception
                       (venue_id, date, kind, description, source, source_file_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (venue_id, date, kind) DO NOTHING
                """,
                (venue_id, e.date, e.kind, e.description, e.source, e.source_file_id),
            )
            added += cur.rowcount
    return added


def refresh_calendar(conn: psycopg.Connection[Any], venue_id: str, end: date) -> int:
    """Rebuild ref.calendar_session for the venue from CALENDAR_START to end, in the caller's
    transaction. Returns the number of sessions.
    """
    venue = conn.execute(
        "SELECT weekend_days FROM ref.venue WHERE venue_id = %s", (venue_id,)
    ).fetchone()
    if venue is None:
        raise ValueError(f"unknown venue {venue_id}")
    rows = conn.execute(
        "SELECT date, kind, source FROM ref.calendar_exception WHERE venue_id = %s",
        (venue_id,),
    ).fetchall()
    exceptions = [CalendarException(date=d, kind=k, source=s) for d, k, s in rows]
    days = build_calendar(CALENDAR_START, end, venue[0], exceptions)

    conn.execute("DELETE FROM ref.calendar_session WHERE venue_id = %s", (venue_id,))
    with (
        conn.cursor() as cur,
        cur.copy(
            "COPY ref.calendar_session (venue_id, date, is_session, session_index, reason,"
            " is_week_end, is_month_end) FROM STDIN"
        ) as copy,
    ):
        for d in days:
            copy.write_row(
                (venue_id, d.date, d.is_session, d.session_index, d.reason, d.is_week_end,
                 d.is_month_end)
            )  # fmt: skip
    return sum(d.is_session for d in days)


def record_archive_proof(
    conn: psycopg.Connection[Any], venue_id: str, dataset: str, before: date
) -> int:
    """Record the exceptions the archive proves for days before `before` (the first year with a
    holiday list): which days had a file of the dataset and which had only 404s. Returns how
    many were new."""
    venue = conn.execute(
        "SELECT weekend_days FROM ref.venue WHERE venue_id = %s", (venue_id,)
    ).fetchone()
    if venue is None:
        raise ValueError(f"unknown venue {venue_id}")
    found = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT partition_date FROM raw.vendor_file_current"
            " WHERE dataset = %s AND partition_date < %s",
            (dataset, before),
        ).fetchall()
    ]
    missing = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT a.partition_date FROM raw.fetch_attempt a"
            " JOIN raw.feed f USING (feed_id)"
            " WHERE f.dataset = %s AND a.http_status = 404 AND a.partition_date < %s",
            (dataset, before),
        ).fetchall()
    ]
    # The bhavcopy archive starts with the calendar: 1 Jan 2010 has no file, so it was no session.
    proved = archive_exceptions(found, missing, venue[0], since=CALENDAR_START)
    return record_exceptions(conn, venue_id, proved)
