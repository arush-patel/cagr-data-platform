"""Train and test splits by time, with a gap between them.

Rows are split on a date column: a session for (listing, date) rows, the anchor date for event
rows such as goldens. The gap drops the last `gap_sessions` sessions before each test window
from training, so a label that looks n sessions ahead cannot span both sides; set it to at
least the label's horizon.

Sessions come from a `Calendar` when one is given, otherwise from the distinct dates in the
frame. A random split on time-ordered rows raises unless `allow_random=True`.
"""

import calendar as _months
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date

import polars as pl

from cagr.calendar import Calendar


class RandomSplitError(ValueError):
    """Raised for a random split of time-ordered rows that was not explicitly allowed."""


@dataclass(frozen=True)
class Split:
    train: pl.DataFrame
    test: pl.DataFrame
    train_start: date
    train_end: date
    """Last date kept in training, after the gap."""
    test_start: date
    test_end: date


def add_months(d: date, months: int) -> date:
    """d moved by whole months, clamped to the month's last day (31 Jan + 1 month = 28 Feb)."""
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    return date(y, m, min(d.day, _months.monthrange(y, m)[1]))


def _sessions(ds: pl.DataFrame, date_col: str, cal: Calendar | None) -> Calendar:
    if cal is not None:
        return cal
    return Calendar(ds[date_col].unique().to_list(), venue="frame")


def _train_end(test_start: date, gap_sessions: int, cal: Calendar) -> date | None:
    """Last date training may hold: gap_sessions sessions are left out before test_start."""
    first_test = cal.on_or_after(test_start)
    i = cal.index(first_test) - gap_sessions - 1
    return cal.sessions[i] if i >= 0 else None


def holdout_by_date(
    ds: pl.DataFrame,
    test_start: date,
    *,
    gap_sessions: int,
    date_col: str = "date",
    calendar: Calendar | None = None,
) -> Split:
    """Everything before test_start (less the gap) trains; test_start onwards tests."""
    cal = _sessions(ds, date_col, calendar)
    end = _train_end(test_start, gap_sessions, cal)
    if end is None:
        raise ValueError("no training rows are left before the test window")
    train = ds.filter(pl.col(date_col) <= end)
    test = ds.filter(pl.col(date_col) >= test_start)
    last = test[date_col].max()
    return Split(train, test, ds[date_col].min(), end, test_start, last)  # type: ignore[arg-type]


def walk_forward(
    ds: pl.DataFrame,
    *,
    train_years: int,
    test_months: int,
    gap_sessions: int,
    step_months: int | None = None,
    expanding: bool = False,
    date_col: str = "date",
    calendar: Calendar | None = None,
) -> Iterator[Split]:
    """Rolling windows: train on train_years, skip the gap, test the next test_months.

    Windows move on by step_months (default test_months). With expanding=True training always
    starts at the first date instead of rolling forward.
    """
    if train_years < 1 or test_months < 1 or gap_sessions < 0:
        raise ValueError("train_years and test_months must be positive, gap_sessions >= 0")
    cal = _sessions(ds, date_col, calendar)
    first: date = ds[date_col].min()  # type: ignore[assignment]
    last: date = ds[date_col].max()  # type: ignore[assignment]
    step = step_months or test_months
    test_start = add_months(first, 12 * train_years)
    while test_start <= last:
        test_end = add_months(test_start, test_months)  # exclusive
        train_start = first if expanding else add_months(test_start, -12 * train_years)
        end = _train_end(test_start, gap_sessions, cal)
        test = ds.filter((pl.col(date_col) >= test_start) & (pl.col(date_col) < test_end))
        if end is not None and end >= train_start and test.height:
            train = ds.filter((pl.col(date_col) >= train_start) & (pl.col(date_col) <= end))
            yield Split(train, test, train_start, end, test_start, test[date_col].max())  # type: ignore[arg-type]
        test_start = add_months(test_start, step)


def expanding(
    ds: pl.DataFrame,
    *,
    min_train_years: int,
    test_months: int,
    gap_sessions: int,
    date_col: str = "date",
    calendar: Calendar | None = None,
) -> Iterator[Split]:
    """Like walk_forward, but training keeps every row from the first date onward."""
    return walk_forward(
        ds,
        train_years=min_train_years,
        test_months=test_months,
        gap_sessions=gap_sessions,
        expanding=True,
        date_col=date_col,
        calendar=calendar,
    )


def random_split(
    ds: pl.DataFrame, test_fraction: float, *, seed: int = 0, allow_random: bool = False
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """A random split. Refused unless allow_random=True: on time series it leaks the future."""
    if not allow_random:
        raise RandomSplitError(
            "a random split puts neighbouring dates on both sides; use walk_forward or "
            "holdout_by_date, or pass allow_random=True if the rows are not time-ordered"
        )
    shuffled = ds.sample(fraction=1.0, shuffle=True, seed=seed)
    n_test = round(ds.height * test_fraction)
    return shuffled.slice(n_test), shuffled.slice(0, n_test)
