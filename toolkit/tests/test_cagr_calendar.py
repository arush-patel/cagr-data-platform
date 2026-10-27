from datetime import date

import pytest

from cagr.calendar import Calendar

# Mon 1 to Fri 12 Jan 2024, with Wed 10 Jan a holiday
SESSIONS = [date(2024, 1, d) for d in (1, 2, 3, 4, 5, 8, 9, 11, 12)]


def test_shift_counts_sessions_not_days() -> None:
    cal = Calendar(SESSIONS)
    assert cal.shift(date(2024, 1, 5), 1) == date(2024, 1, 8)
    assert cal.shift(date(2024, 1, 9), 1) == date(2024, 1, 11)
    assert cal.shift(date(2024, 1, 11), -2) == date(2024, 1, 8)
    with pytest.raises(ValueError):
        cal.shift(date(2024, 1, 10), 1)  # not a session
    with pytest.raises(ValueError):
        cal.shift(date(2024, 1, 12), 1)  # past the end


def test_nearest_sessions() -> None:
    cal = Calendar(SESSIONS)
    assert cal.on_or_before(date(2024, 1, 7)) == date(2024, 1, 5)
    assert cal.on_or_before(date(2024, 1, 8)) == date(2024, 1, 8)
    assert cal.on_or_after(date(2024, 1, 10)) == date(2024, 1, 11)
    with pytest.raises(ValueError):
        cal.on_or_before(date(2023, 12, 31))


def test_between_and_count() -> None:
    cal = Calendar(SESSIONS)
    assert cal.between(date(2024, 1, 6), date(2024, 1, 10)) == [date(2024, 1, 8), date(2024, 1, 9)]
    assert cal.count(date(2024, 1, 5), date(2024, 1, 8)) == 1
    assert cal.count(date(2024, 1, 1), date(2024, 1, 12)) == 8
    assert cal.is_session(date(2024, 1, 11)) and not cal.is_session(date(2024, 1, 10))


def test_duplicates_and_order_do_not_matter() -> None:
    cal = Calendar(reversed(SESSIONS + SESSIONS[:2]))
    assert len(cal) == len(SESSIONS) and cal.first == date(2024, 1, 1)
