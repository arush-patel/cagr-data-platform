"""Share periods from reports and the walk through corporate actions (spec 0007, 5.3)."""

from datetime import date
from decimal import Decimal as D
from itertools import pairwise

import pytest

from cagr_data.reference.shares import Event, Period, Run, build_periods, multiplier

FIRST = date(2010, 1, 4)
MCAP = date(2024, 2, 1)


def test_multipliers() -> None:
    assert multiplier("split", D(1), D(10), 0.1) == 10  # Rs 10 to Re 1: ten times the shares
    assert multiplier("consolidation", D(10), D(1), 10) == 0.1
    assert multiplier("bonus", D(1), D(1), 0.5) == 2
    assert multiplier("rights", D(1), D(4), 0.92) == 1.25
    assert multiplier(None, None, None, 0.5) == 2  # a clean-ratio jump with no action
    assert multiplier("dividend", None, None, 0.97) is None


def test_walks_back_from_the_first_mcap_report() -> None:
    runs = [Run(MCAP, date(2026, 10, 23), 2000, "nse_mcap")]
    events = [Event(date(2015, 6, 1), 2.0), Event(date(2020, 3, 2), 10.0)]  # bonus, then split
    assert build_periods(FIRST, runs, events) == [
        Period(FIRST, date(2015, 6, 1), 100, "walk_back"),
        Period(date(2015, 6, 1), date(2020, 3, 2), 200, "walk_back"),
        Period(date(2020, 3, 2), MCAP, 2000, "walk_back"),
        Period(MCAP, None, 2000, "nse_mcap"),
    ]


def test_reported_changes_open_new_periods() -> None:
    runs = [
        Run(MCAP, date(2024, 5, 31), 1000, "nse_mcap"),
        Run(date(2024, 6, 3), date(2026, 10, 23), 2000, "nse_mcap"),
    ]
    periods = build_periods(MCAP, runs, [Event(date(2024, 6, 3), 2.0)])
    assert periods == [
        Period(MCAP, date(2024, 6, 3), 1000, "nse_mcap"),
        Period(date(2024, 6, 3), None, 2000, "nse_mcap"),
    ]


def test_index_reports_then_a_gap_spread_to_2024() -> None:
    runs = [
        Run(FIRST, date(2013, 12, 31), 400, "index_file"),
        Run(MCAP, date(2026, 10, 23), 1000, "nse_mcap"),
    ]
    # A 1:1 bonus in 2016 explains 800 of the 1,000; the other 200 are spread from 2014 to 2024.
    periods = build_periods(FIRST, runs, [Event(date(2016, 7, 1), 2.0)])
    assert periods[0] == Period(FIRST, date(2014, 1, 1), 400, "index_file",
                                {"next_report_ratio": 1.25})  # fmt: skip
    spread = periods[1:-1]
    assert [p.valid_from for p in spread][:3] == [date(2014, 1, 1), date(2014, 4, 1),
                                                    date(2014, 7, 1)]  # fmt: skip
    assert {p.source for p in spread} == {"spread"}
    assert spread[0].evidence == {"spread_ratio": 1.25}
    assert date(2016, 7, 1) in [p.valid_from for p in spread]  # the bonus is a step too
    shares = [p.shares for p in spread]
    assert shares == sorted(shares) and 400 <= shares[0] < 405  # grows from 400 ...
    assert 990 < shares[-1] < 1000  # ... to just under the next report
    assert all(a.valid_to == b.valid_from for a, b in pairwise(periods))
    assert periods[-1] == Period(MCAP, None, 1000, "nse_mcap")


def test_a_short_gap_keeps_the_walk_back() -> None:
    runs = [
        Run(FIRST, date(2010, 3, 1), 400, "index_file"),
        Run(date(2010, 4, 1), date(2013, 12, 31), 500, "index_file"),
    ]
    periods = build_periods(FIRST, runs, [])
    assert periods[0] == Period(FIRST, date(2010, 4, 1), 400, "index_file",
                                {"next_report_ratio": 1.25})  # fmt: skip
    assert "spread" not in {p.source for p in periods}


def test_delisted_after_its_last_index_report_walks_forward() -> None:
    runs = [Run(FIRST, date(2013, 12, 31), 400, "index_file")]
    periods = build_periods(FIRST, runs, [Event(date(2017, 1, 2), 5.0)])
    assert periods == [
        Period(FIRST, date(2017, 1, 2), 400, "index_file"),
        Period(date(2017, 1, 2), None, 2000, "walk_back"),
    ]


def test_pending_adjustments_are_marked_and_no_reports_give_nothing() -> None:
    runs = [Run(MCAP, date(2026, 10, 23), 1000, "nse_mcap")]
    periods = build_periods(FIRST, runs, [], pending=[date(2018, 5, 2)])
    assert periods[0].evidence == {"pending_adjustments": ["2018-05-02"]}
    assert build_periods(FIRST, [], []) == []


@pytest.mark.parametrize("first_session", [MCAP, date(2025, 1, 1)])
def test_a_listing_that_starts_with_its_report(first_session: date) -> None:
    runs = [Run(first_session, date(2026, 10, 23), 1000, "nse_mcap")]
    assert build_periods(first_session, runs, []) == [Period(first_session, None, 1000,
                                                             "nse_mcap")]  # fmt: skip
