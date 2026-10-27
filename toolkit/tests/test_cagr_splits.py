from datetime import date, timedelta

import polars as pl
import pytest

from cagr import splits
from cagr.calendar import Calendar


def weekdays(start: date, end: date) -> list[date]:
    days = (start + timedelta(days=i) for i in range((end - start).days + 1))
    return [d for d in days if d.weekday() < 5]


DATES = weekdays(date(2015, 1, 1), date(2021, 12, 31))
DS = pl.DataFrame({"date": DATES * 2, "listing_id": [1] * len(DATES) + [2] * len(DATES)})
CAL = Calendar(DATES)


def test_walk_forward_leaves_the_gap_and_never_overlaps() -> None:
    folds = list(splits.walk_forward(DS, train_years=4, test_months=6, gap_sessions=20))
    assert len(folds) == 6  # 2019-01 to 2021-12 in six-month windows
    for f in folds:
        assert f.train["date"].max() < f.test["date"].min()
        assert CAL.count(f.train_end, f.test["date"].min()) == 21  # 20 sessions skipped
        assert f.train_start >= splits.add_months(f.test_start, -48)
    assert folds[0].test_start == date(2019, 1, 1)
    assert folds[1].train_start == date(2015, 7, 1)


def test_expanding_keeps_the_first_date() -> None:
    folds = list(splits.expanding(DS, min_train_years=4, test_months=12, gap_sessions=5))
    assert [f.train_start for f in folds] == [date(2015, 1, 1)] * len(folds)
    assert folds[-1].train.height > folds[0].train.height


def test_holdout_by_date() -> None:
    s = splits.holdout_by_date(DS, date(2020, 1, 1), gap_sessions=10, calendar=CAL)
    assert s.test["date"].min() == date(2020, 1, 1)
    assert CAL.count(s.train_end, date(2020, 1, 1)) == 11
    assert s.train.height + s.test.height == DS.height - 2 * 10


def test_gap_uses_the_calendar_when_rows_are_sparse() -> None:
    # event rows (one golden a month) are far sparser than sessions
    events = pl.DataFrame({"date": [date(2019, m, 15) for m in range(1, 13)]})
    s = splits.holdout_by_date(events, date(2019, 7, 1), gap_sessions=20, calendar=CAL)
    assert s.train["date"].max() == date(2019, 5, 15)  # 15 June is inside the 20-session gap


def test_random_split_is_refused_unless_asked() -> None:
    with pytest.raises(splits.RandomSplitError):
        splits.random_split(DS, 0.2)
    train, test = splits.random_split(DS, 0.2, allow_random=True)
    assert train.height + test.height == DS.height and test.height == round(DS.height * 0.2)


def test_add_months_clamps_to_month_end() -> None:
    assert splits.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert splits.add_months(date(2024, 3, 15), -14) == date(2023, 1, 15)
