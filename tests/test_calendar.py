from datetime import date, time

from cagr_data.reference.calendar import compute_sessions, with_period_ends


def test_sessions_follow_holidays_and_special_sessions() -> None:
    exc = {
        date(2026, 10, 2): {"kind": "holiday"},
        date(2026, 10, 10): {"kind": "special_session", "description": "budget"},
    }
    s = compute_sessions(date(2026, 9, 28), date(2026, 10, 11), tz="Asia/Kolkata", regular_open=time(9, 15),
                         regular_close=time(15, 30), weekend_days={6, 7}, exceptions=exc)  # fmt: skip
    days = [x.session_date for x in s]
    assert date(2026, 10, 2) not in days
    assert date(2026, 10, 10) in days and date(2026, 10, 11) not in days
    assert s[0].opens_at.utcoffset().total_seconds() == 5.5 * 3600  # type: ignore[union-attr]
    df = with_period_ends(s)
    assert df["session_idx"].tolist() == list(range(1, len(s) + 1))
    # the special Saturday closes its ISO week
    assert df.loc[df["session_date"] == date(2026, 10, 9), "week_end"].item() == date(2026, 10, 10)
    assert df.loc[df["session_date"] == date(2026, 9, 30), "month_end"].item() == date(2026, 9, 30)
