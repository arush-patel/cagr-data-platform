from datetime import date, timedelta
from pathlib import Path

import pytest

from cagr_data.reference.calendar import (
    CalendarDay,
    CalendarException,
    archive_exceptions,
    build_calendar,
    holiday_exceptions,
)
from cagr_data.vendors.nse.holidays import parse_holidays

WEEKEND = (6, 7)


def _holiday(day: date) -> CalendarException:
    return CalendarException(date=day, kind="holiday", source="holiday_api")


def _special(day: date) -> CalendarException:
    return CalendarException(date=day, kind="special_session", source="archive_proof")


def _by_date(days: list[CalendarDay]) -> dict[date, CalendarDay]:
    return {d.date: d for d in days}


def test_plain_weeks() -> None:
    # Mon 2 Nov to Sun 15 Nov 2026, no exceptions
    days = build_calendar(date(2026, 11, 2), date(2026, 11, 15), WEEKEND, [])
    assert len(days) == 14
    sessions = [d for d in days if d.is_session]
    assert [d.session_index for d in sessions] == list(range(1, 11))
    assert [d.date for d in sessions if d.is_week_end] == [date(2026, 11, 6), date(2026, 11, 13)]
    assert {d.reason for d in days if not d.is_session} == {"weekend"}
    assert not any(d.is_week_end or d.is_month_end for d in days if not d.is_session)


def test_muhurat_and_holidays() -> None:
    exceptions = [
        _holiday(date(2026, 11, 8)),  # Diwali, a Sunday
        _special(date(2026, 11, 8)),  # Muhurat trading that evening
        _holiday(date(2026, 11, 10)),
        _holiday(date(2026, 11, 13)),  # a Friday, so Thursday ends the week
    ]
    days = _by_date(build_calendar(date(2026, 11, 2), date(2026, 11, 15), WEEKEND, exceptions))
    assert days[date(2026, 11, 8)].reason == "special_session"
    assert days[date(2026, 11, 8)].is_session
    assert days[date(2026, 11, 7)].reason == "weekend"
    assert days[date(2026, 11, 10)] == CalendarDay(date(2026, 11, 10), False, None, "holiday")
    assert days[date(2026, 11, 12)].is_week_end
    # Sunday 8 Nov is the last session of its ISO week, not Friday 6 Nov
    assert days[date(2026, 11, 8)].is_week_end
    assert not days[date(2026, 11, 6)].is_week_end
    indexes = [d.session_index for d in days.values() if d.is_session]
    assert indexes == list(range(1, len(indexes) + 1))


def test_holiday_on_a_weekend_says_holiday() -> None:
    days = _by_date(build_calendar(date(2026, 8, 10), date(2026, 8, 16), WEEKEND, [
        _holiday(date(2026, 8, 15)),
    ]))  # fmt: skip
    assert days[date(2026, 8, 15)].reason == "holiday"
    assert days[date(2026, 8, 16)].reason == "weekend"


def test_month_end_skips_a_holiday() -> None:
    days = _by_date(build_calendar(date(2026, 3, 23), date(2026, 4, 5), WEEKEND, [
        _holiday(date(2026, 3, 31)),
    ]))  # fmt: skip
    assert [d for d, v in days.items() if v.is_month_end] == [date(2026, 3, 30)]


def test_open_week_and_month_are_not_flagged() -> None:
    # stops on Wednesday 28 Oct 2026: neither the week nor the month is over
    days = build_calendar(date(2026, 10, 19), date(2026, 10, 28), WEEKEND, [])
    assert [d.date for d in days if d.is_week_end] == [date(2026, 10, 23)]
    assert not any(d.is_month_end for d in days)
    # stops on Saturday 31 Oct: the month is over, the week runs to Sunday
    days = _by_date(build_calendar(date(2026, 10, 19), date(2026, 10, 31), WEEKEND, []))
    assert days[date(2026, 10, 30)].is_month_end
    assert not days[date(2026, 10, 30)].is_week_end
    days = _by_date(build_calendar(date(2026, 10, 19), date(2026, 11, 1), WEEKEND, []))
    assert days[date(2026, 10, 30)].is_week_end


def test_end_before_start_raises() -> None:
    with pytest.raises(ValueError):
        build_calendar(date(2026, 1, 2), date(2026, 1, 1), WEEKEND, [])


def test_a_year_from_the_holiday_api(nse_fixtures: Path) -> None:
    holidays = parse_holidays((nse_fixtures / "holiday-master_trading_2026.json").read_bytes())
    exceptions = holiday_exceptions(holidays, file_id=7)
    assert len(exceptions) == 20
    assert {e.source_file_id for e in exceptions} == {7}
    assert exceptions[0].description == "Municipal Corporation Election - Maharashtra"
    exceptions.append(_special(date(2026, 2, 1)))  # Budget day, a Sunday session
    days = build_calendar(date(2026, 1, 1), date(2026, 12, 31), WEEKEND, exceptions)
    # 261 weekdays, 16 weekday holidays, plus the Budget Sunday
    assert sum(d.is_session for d in days) == 261 - 16 + 1
    assert sum(d.is_month_end for d in days) == 12
    assert len({d.date.isocalendar()[:2] for d in days if d.is_week_end}) == sum(
        d.is_week_end for d in days
    )


def test_archive_proof() -> None:
    mon = date(2010, 1, 4)
    found = [mon, mon + timedelta(1), mon + timedelta(3), mon + timedelta(5)]  # Mon Tue Thu Sat
    missing = [mon + timedelta(2), mon + timedelta(4), mon + timedelta(7)]  # Wed Fri, next Mon
    assert archive_exceptions(found, missing, WEEKEND) == [
        CalendarException(mon + timedelta(2), "holiday", "archive_proof"),
        CalendarException(mon + timedelta(4), "holiday", "archive_proof"),
        CalendarException(mon + timedelta(5), "special_session", "archive_proof"),
    ]


def test_archive_proof_ignores_misses_outside_the_files() -> None:
    mon = date(2010, 1, 4)
    assert archive_exceptions([mon], [mon - timedelta(3), mon + timedelta(1)], WEEKEND) == []
    assert archive_exceptions([], [mon], WEEKEND) == []
