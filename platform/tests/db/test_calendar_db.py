from datetime import date
from pathlib import Path

import psycopg
import pytest

from cagr_data.reference.calendar import (
    CALENDAR_START,
    CalendarException,
    holiday_exceptions,
    record_exceptions,
    refresh_calendar,
)
from cagr_data.vendors.nse.holidays import parse_holidays

pytestmark = pytest.mark.db

Conn = psycopg.Connection[tuple[object, ...]]


def _exceptions(nse_fixtures: Path) -> list[CalendarException]:
    holidays = parse_holidays((nse_fixtures / "holiday-master_trading_2026.json").read_bytes())
    return [
        *holiday_exceptions(holidays),
        CalendarException(date(2026, 2, 1), "special_session", "archive_proof"),
        CalendarException(date(2026, 11, 8), "special_session", "archive_proof"),
    ]


def test_record_exceptions_once(db: Conn, nse_fixtures: Path) -> None:
    assert record_exceptions(db, "XNSE", _exceptions(nse_fixtures)) == 22
    assert record_exceptions(db, "XNSE", _exceptions(nse_fixtures)) == 0
    kinds = db.execute(
        "SELECT kind FROM ref.calendar_exception WHERE date = '2026-11-08' ORDER BY kind"
    ).fetchall()
    assert kinds == [("holiday",), ("special_session",)]


def test_refresh_builds_from_the_start_and_reruns(db: Conn, nse_fixtures: Path) -> None:
    record_exceptions(db, "XNSE", _exceptions(nse_fixtures))
    sessions = refresh_calendar(db, "XNSE", date(2026, 12, 31))
    assert refresh_calendar(db, "XNSE", date(2026, 12, 31)) == sessions
    first, last, days, max_index = db.execute(
        "SELECT min(date), max(date), count(*), max(session_index)"
        " FROM ref.calendar_session WHERE venue_id = 'XNSE'"
    ).fetchone()  # type: ignore[misc]
    assert (first, last) == (CALENDAR_START, date(2026, 12, 31))
    assert days == (date(2026, 12, 31) - CALENDAR_START).days + 1
    assert max_index == sessions
    in_2026 = db.execute(
        "SELECT count(*) FILTER (WHERE is_session), count(*) FILTER (WHERE is_month_end)"
        " FROM ref.calendar_session WHERE date >= '2026-01-01'"
    ).fetchone()
    assert in_2026 == (261 - 16 + 2, 12)  # Budget Sunday and Muhurat Sunday are sessions
    diwali = db.execute(
        "SELECT is_session, reason FROM ref.calendar_session WHERE date = '2026-11-08'"
    ).fetchone()
    assert diwali == (True, "special_session")


def test_unknown_venue(db: Conn) -> None:
    with pytest.raises(ValueError, match="XBOM"):
        refresh_calendar(db, "XBOM", date(2026, 12, 31))
