from datetime import date, timedelta

import polars as pl
import pytest

from cagr import fs, ind
from cagr.formula import FormulaError, compile_formula, to_feature


def bars(n: int = 40) -> pl.DataFrame:
    close = [100.0 + i + (3 if i % 4 == 0 else 0) for i in range(n)]
    return pl.DataFrame(
        {
            "listing_id": [1] * n,
            "date": [date(2024, 1, 1) + timedelta(days=i) for i in range(n)],
            "open": close,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "volume": [1000.0] * n,
        }
    )


def evaluate(text: str) -> list[object]:
    return bars().select(compile_formula(text).alias("v"))["v"].to_list()


def test_formula_matches_the_same_expression_written_in_python() -> None:
    got = bars().select(compile_formula("close / rolling_max(high, 20) - 1").alias("v"))
    want = bars().select((pl.col("close") / pl.col("high").rolling_max(20) - 1).alias("v"))
    assert got.equals(want)


def test_indicators_and_logic() -> None:
    got = bars().select(compile_formula("rsi(14) > 50 and close > sma(close, 5)").alias("v"))
    want = bars().select(((ind.rsi(14) > 50) & (pl.col("close") > ind.sma(5))).alias("v"))
    assert got.equals(want)
    assert evaluate("not (close > 1000)")[-1] is True
    assert evaluate("10 < close < 102.5")[:3] == [False, True, True]


def test_lag_looks_back_only() -> None:
    assert evaluate("lag(close, 1)")[:2] == [None, 103.0]
    for bad in ["lag(close, -1)", "lag(close, 0)", "lag(close, 1.5)", "lag(close, volume)"]:
        with pytest.raises(FormulaError, match="window"):
            compile_formula(bad)


@pytest.mark.parametrize(
    "text",
    [
        "__import__('os')",
        "close.shift(-1)",
        "close[0]",
        "foo + 1",
        "lambda: 1",
        "sma(close, n=5)",
        "close ** 2",
        "'text'",
        "close +",
    ],
)
def test_anything_outside_the_language_is_refused(text: str) -> None:
    with pytest.raises(FormulaError):
        compile_formula(text)


def test_to_feature_works_out_inputs_and_warm_up() -> None:
    f = to_feature("close / rolling_max(high, 20) - 1", name="off_high_20", version=1)
    assert f.inputs == ("close", "high") and f.min_bars == 20
    assert to_feature("change(sma(close, 5), 3)", name="x", version=1).min_bars == 8
    assert to_feature("adx(14)", name="a", version=1).min_bars == 28
    values = fs.compute(f, bars())["off_high_20@1"]
    assert values[:19].null_count() == 19 and values[19] is not None


def test_formula_features_pass_the_no_future_check() -> None:
    for text in ["close / rolling_max(high, 20) - 1", "rsi(14) - 50", "change(close, 5)"]:
        fs.check_no_future(to_feature(text, name="f", version=1), bars(), by="listing_id")


def test_the_hash_follows_the_formula_text() -> None:
    a = to_feature("sma(close, 5)", name="s", version=1)
    b = to_feature("sma(close, 6)", name="s", version=1)
    assert a.code_hash != b.code_hash


def test_a_formula_must_read_a_column() -> None:
    with pytest.raises(FormulaError):
        to_feature("1 + 2", name="c", version=1)
