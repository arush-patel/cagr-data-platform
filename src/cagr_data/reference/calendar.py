"""Trading calendars (D7): sessions are built from facts, never from a weekday rule alone.

Facts live in ref.calendar_exception: the exchange's holiday list and special sessions that the
archive proves. `build_sessions` turns weekday rule + exceptions into ref.calendar_session with a
dense session index, so windows count sessions, not rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import psycopg


@dataclass(frozen=True)
class Session:
    session_date: date
    opens_at: datetime
    closes_at: datetime
    is_special: bool
    note: str | None


def compute_sessions(
    start: date,
    end: date,
    *,
    tz: str,
    regular_open: time,
    regular_close: time,
    weekend_days: set[int],
    exceptions: dict[date, dict[str, Any]],
) -> list[Session]:
    """Weekdays minus holidays plus special sessions, between start and end inclusive."""
    zone = ZoneInfo(tz)
    out: list[Session] = []
    d = start
    while d <= end:
        exc = exceptions.get(d)
        is_weekday = d.isoweekday() not in weekend_days
        if exc and exc["kind"] == "holiday":
            pass
        elif exc and exc["kind"] == "special_session":
            o = exc.get("opens_at_local") or regular_open
            c = exc.get("closes_at_local") or regular_close
            out.append(
                Session(
                    d,
                    datetime.combine(d, o, zone),
                    datetime.combine(d, c, zone),
                    True,
                    exc.get("description"),
                )
            )
        elif is_weekday:
            out.append(
                Session(
                    d,
                    datetime.combine(d, regular_open, zone),
                    datetime.combine(d, regular_close, zone),
                    False,
                    None,
                )
            )
        d += timedelta(days=1)
    return out


def with_period_ends(sessions: list[Session]) -> pd.DataFrame:
    """Adds session_idx and the last session of each session's ISO week and calendar month."""
    df = pd.DataFrame([s.__dict__ for s in sessions])
    if df.empty:
        return df
    df["session_idx"] = range(1, len(df) + 1)
    sd = pd.to_datetime(df["session_date"])
    iso = sd.dt.isocalendar()
    df["week_end"] = df.groupby([iso["year"], iso["week"]])["session_date"].transform("max")
    df["month_end"] = df.groupby([sd.dt.year, sd.dt.month])["session_date"].transform("max")
    return df


def rebuild(conn: psycopg.Connection[Any], calendar_code: str, start: date, end: date) -> int:
    """Rebuild every session of a calendar from start to end. Returns the session count.

    The session index is dense from the first session, so the whole calendar is rewritten in one
    transaction; nothing references calendar_session by key.
    """
    cal = conn.execute(
        "SELECT calendar_id, timezone, regular_open, regular_close, weekend_days FROM ref.calendar WHERE code = %s",
        (calendar_code,),
    ).fetchone()
    if cal is None:
        raise LookupError(f"unknown calendar {calendar_code}")
    exc = {
        r["exception_date"]: r
        for r in conn.execute(
            "SELECT exception_date, kind, description, opens_at_local, closes_at_local "
            "FROM ref.calendar_exception WHERE calendar_id = %s",
            (cal["calendar_id"],),
        ).fetchall()
    }
    sessions = compute_sessions(
        start,
        end,
        tz=cal["timezone"],
        regular_open=cal["regular_open"],
        regular_close=cal["regular_close"],
        weekend_days=set(cal["weekend_days"]),
        exceptions=exc,
    )
    df = with_period_ends(sessions)
    with conn.transaction():
        conn.execute("DELETE FROM ref.calendar_session WHERE calendar_id = %s", (cal["calendar_id"],))
        with conn.cursor().copy(
            "COPY ref.calendar_session (calendar_id, session_date, session_idx, opens_at, closes_at, week_end, "
            "month_end, is_special, note) FROM STDIN"
        ) as cp:
            for r in df.itertuples(index=False):
                cp.write_row(
                    (cal["calendar_id"], r.session_date, r.session_idx, r.opens_at, r.closes_at, r.week_end,
                     r.month_end, r.is_special, r.note)
                )  # fmt: skip
    return len(df)


def record_holidays(
    conn: psycopg.Connection[Any], calendar_code: str, holidays: pd.DataFrame, source: str
) -> int:
    """Upsert holidays from the exchange list. A date already proven a special session is kept."""
    n = 0
    for r in holidays.itertuples(index=False):
        cur = conn.execute(
            "INSERT INTO ref.calendar_exception (calendar_id, exception_date, kind, description, source) "
            "SELECT calendar_id, %s, 'holiday', %s, %s FROM ref.calendar WHERE code = %s "
            "ON CONFLICT (calendar_id, exception_date) DO UPDATE SET description = EXCLUDED.description, "
            "source = EXCLUDED.source, recorded_at = now() "
            "WHERE ref.calendar_exception.kind = 'holiday'",
            (r.holiday_date, r.description, source, calendar_code),
        )
        n += cur.rowcount
    return n


def record_special_session(
    conn: psycopg.Connection[Any],
    calendar_code: str,
    d: date,
    description: str,
    source: str = "archive_evidence",
) -> None:
    """A file exists for a day the rule calls closed (a Saturday budget day, Muhurat trading)."""
    conn.execute(
        "INSERT INTO ref.calendar_exception (calendar_id, exception_date, kind, description, source) "
        "SELECT calendar_id, %s, 'special_session', %s, %s FROM ref.calendar WHERE code = %s "
        "ON CONFLICT (calendar_id, exception_date) DO UPDATE SET kind = 'special_session', "
        "description = EXCLUDED.description, source = EXCLUDED.source, recorded_at = now()",
        (d, description, source, calendar_code),
    )


def is_session(conn: psycopg.Connection[Any], calendar_id: int, d: date) -> bool:
    row = conn.execute(
        "SELECT 1 FROM ref.calendar_session WHERE calendar_id = %s AND session_date = %s", (calendar_id, d)
    ).fetchone()
    return row is not None


def sessions_between(conn: psycopg.Connection[Any], calendar_id: int, start: date, end: date) -> list[date]:
    rows = conn.execute(
        "SELECT session_date FROM ref.calendar_session WHERE calendar_id = %s AND session_date BETWEEN %s AND %s "
        "ORDER BY session_date",
        (calendar_id, start, end),
    ).fetchall()
    return [r["session_date"] for r in rows]
