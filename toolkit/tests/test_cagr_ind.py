import polars as pl
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from cagr import ind


def bars(close: list[float], spread: float = 1.0) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "open": close,
            "high": [c + spread for c in close],
            "low": [c - spread for c in close],
            "close": close,
            "volume": [100.0] * len(close),
        }
    )


def values(df: pl.DataFrame, expr: pl.Expr) -> list[float | None]:
    return df.select(expr.alias("v"))["v"].to_list()


def test_sma_is_null_until_the_window_is_full() -> None:
    assert values(bars([1, 2, 3, 4, 5]), ind.sma(3)) == [None, None, 2.0, 3.0, 4.0]


def test_ema_is_seeded_with_the_sma() -> None:
    # alpha = 2 / (3 + 1) = 0.5; seed mean(1, 2, 3) = 2; then 2 + (4 - 2) * 0.5 = 3, ...
    assert values(bars([1, 2, 3, 4, 5]), ind.ema(3)) == [None, None, 2.0, 3.0, 4.0]
    assert values(bars([1, 2, 3, 6]), ind.ema(3)) == [None, None, 2.0, 4.0]


def test_wilder_smoothing_by_hand() -> None:
    df = pl.DataFrame({"x": [1.0, 2.0, 3.0, 4.0]})
    out = values(df, ind.wilder(pl.col("x"), 3))
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(2.0 + (4.0 - 2.0) / 3)


def test_returns_and_rolling_extremes() -> None:
    df = bars([10, 11, 9, 12])
    assert values(df, ind.returns(1)) == pytest.approx([None, 0.1, 9 / 11 - 1, 12 / 9 - 1])
    assert values(df, ind.rolling_high(2)) == [None, 12.0, 12.0, 13.0]
    assert values(df, ind.rolling_low(2)) == [None, 9.0, 8.0, 8.0]


def test_true_range_counts_gaps_from_the_previous_close() -> None:
    df = pl.DataFrame(
        {"high": [11.0, 12.0, 20.0], "low": [9.0, 11.0, 18.0], "close": [10.0, 11.5, 19.0]}
    )
    # bar 1: its range; bar 2: high - prev close = 2; bar 3: high - prev close = 8.5
    assert values(df, ind.true_range()) == [2.0, 2.0, 8.5]


def test_rsi_by_hand() -> None:
    # changes 1, 1, -1, 1, 1; first average gain 2/3, loss 1/3 over three changes
    out = values(bars([1, 2, 3, 2, 3, 4]), ind.rsi(3))
    assert out[:3] == [None, None, None]
    assert out[3] == pytest.approx(100 - 100 / 3)
    gain, loss = 2 / 3 + (1 - 2 / 3) / 3, 1 / 3 + (0 - 1 / 3) / 3
    assert out[4] == pytest.approx(100 - 100 / (1 + gain / loss))


def test_rsi_of_a_rising_series_is_100() -> None:
    assert values(bars([1, 2, 3, 4, 5, 6]), ind.rsi(3))[3:] == [100.0, 100.0, 100.0]


def test_adx_in_a_steady_uptrend() -> None:
    df = bars([float(i) for i in range(1, 60)])
    frame = df.select(p=ind.plus_di(14), m=ind.minus_di(14), a=ind.adx(14))
    assert frame["a"][:27].null_count() == 27  # DI from bar 15, then 14 DX values to seed ADX
    assert frame["a"][27] is not None
    assert (frame["p"].drop_nulls() > frame["m"].drop_nulls()).all()
    assert frame["a"][-1] == pytest.approx(100.0)


@settings(max_examples=50, deadline=None)
@given(st.lists(st.floats(min_value=1, max_value=1000), min_size=30, max_size=80))
def test_bounded_indicators_stay_in_range(close: list[float]) -> None:
    df = bars(close, spread=0.5).with_columns(
        high=pl.max_horizontal("high", "close"), low=pl.min_horizontal("low", "close")
    )
    frame = df.select(r=ind.rsi(14), a=ind.atr(14), x=ind.adx(14))
    assert frame["r"].drop_nulls().is_between(0, 100).all()
    assert (frame["a"].drop_nulls() >= 0).all()
    assert frame["x"].drop_nulls().is_between(0, 100 + 1e-9).all()
