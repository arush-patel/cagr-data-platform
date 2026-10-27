import math
import statistics
from datetime import date, timedelta

import polars as pl
import pytest
from hypothesis import given
from hypothesis import strategies as st

from cagr import metrics


def curve(values: list[float], start: date = date(2020, 1, 1)) -> pl.DataFrame:
    return pl.DataFrame(
        {"date": [start + timedelta(days=i) for i in range(len(values))], "equity": values}
    )


def test_cagr_over_four_years() -> None:
    # 2020-01-01 to 2024-01-01 is 1461 days, exactly 4 years of 365.25
    c = pl.DataFrame({"date": [date(2020, 1, 1), date(2024, 1, 1)], "equity": [100.0, 146.41]})
    assert metrics.cagr(c) == pytest.approx(0.10)
    assert metrics.total_return(c) == pytest.approx(0.4641)


def test_cagr_of_a_flat_curve_is_zero_and_a_wipeout_is_minus_one() -> None:
    assert metrics.cagr(curve([100.0, 100.0, 100.0])) == 0.0
    assert metrics.cagr(curve([100.0, 0.0])) == -1.0


def test_cagr_needs_a_real_curve() -> None:
    with pytest.raises(ValueError):
        metrics.cagr(curve([100.0]))
    with pytest.raises(ValueError):
        metrics.cagr(curve([100.0, None]))  # type: ignore[list-item]


def test_drawdown_and_its_duration() -> None:
    c = curve([100.0, 120.0, 90.0, 130.0, 65.0])
    assert metrics.drawdown(c)["drawdown"].to_list() == pytest.approx([0, 0, -0.25, 0, -0.5])
    assert metrics.max_drawdown(c) == pytest.approx(-0.5)
    assert metrics.max_drawdown_duration(curve([100.0, 90.0, 80.0, 95.0, 101.0, 99.0])) == 3
    assert metrics.max_drawdown(curve([1.0, 2.0, 3.0])) == 0.0


def test_volatility_and_sharpe_match_the_formulas() -> None:
    r = pl.Series([0.01, -0.01, 0.02, 0.0])
    sd = statistics.stdev(r.to_list())
    assert metrics.volatility(r) == pytest.approx(sd * math.sqrt(252))
    assert metrics.sharpe(r) == pytest.approx(statistics.mean(r.to_list()) / sd * math.sqrt(252))
    assert math.isnan(metrics.sharpe(pl.Series([0.01, 0.01, 0.01])))


def test_sortino_only_counts_shortfalls() -> None:
    r = pl.Series([0.02, -0.01, 0.03, -0.02])
    downside = math.sqrt((0.01**2 + 0.02**2) / 4)
    assert metrics.sortino(r) == pytest.approx(0.005 / downside * math.sqrt(252))
    assert math.isnan(metrics.sortino(pl.Series([0.01, 0.02])))


def test_curve_returns() -> None:
    assert metrics.curve_returns(curve([100.0, 110.0, 99.0])).to_list() == pytest.approx(
        [0.1, -0.1]
    )


def test_xirr_of_one_year() -> None:
    flows = pl.DataFrame(
        {"date": [date(2021, 1, 1), date(2022, 1, 1)], "amount": [-1000.0, 1100.0]}
    )
    assert metrics.xirr(flows) == pytest.approx(0.10, abs=1e-9)


def test_xirr_with_several_flows_zeroes_the_npv() -> None:
    flows = pl.DataFrame(
        {
            "date": [date(2020, 1, 1), date(2020, 7, 1), date(2021, 3, 15), date(2022, 1, 1)],
            "amount": [-1000.0, -500.0, 200.0, 1700.0],
        }
    )
    rate = metrics.xirr(flows)
    npv = sum(a / (1 + rate) ** ((d - date(2020, 1, 1)).days / 365) for d, a in flows.iter_rows())
    assert npv == pytest.approx(0, abs=1e-6)


def test_xirr_needs_money_in_and_out() -> None:
    with pytest.raises(ValueError):
        metrics.xirr(pl.DataFrame({"date": [date(2020, 1, 1)], "amount": [100.0]}))


def test_trade_statistics() -> None:
    pnl = pl.Series([1.0, -1.0, 0.0, 2.0])
    assert metrics.hit_rate(pnl) == 0.5
    assert metrics.win_loss(pnl) == {"avg_win": 1.5, "avg_loss": -1.0, "payoff": 1.5}
    trades = pl.DataFrame({"entry_date": [date(2024, 1, 1)], "exit_date": [date(2024, 1, 31)]})
    assert metrics.holding_days(trades).to_list() == [30]


@given(st.lists(st.floats(min_value=0.01, max_value=1e6), min_size=2, max_size=60))
def test_drawdown_is_never_positive(values: list[float]) -> None:
    dd = metrics.max_drawdown(curve(values))
    assert -1 <= dd <= 0
