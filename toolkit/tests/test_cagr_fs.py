from datetime import date, timedelta

import polars as pl
import pytest

from cagr import fs, ind


def two_listings(n: int = 60) -> pl.DataFrame:
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(n)]
    a = [100 + i + (i % 7) for i in range(n)]
    b = [500 - 2 * i + (i % 5) for i in range(n)]
    close = [float(x) for x in a + b]
    return pl.DataFrame(
        {
            "listing_id": [1] * n + [2] * n,
            "date": days * 2,
            "high": [c + 1 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
        }
    )


@fs.feature(name="sma_3", version=1, inputs=["close"], min_bars=3)
def sma_3() -> pl.Expr:
    return ind.sma(3)


@fs.feature(name="rsi_5", version=1, inputs=["close"], min_bars=6)
def rsi_5() -> pl.Expr:
    return ind.rsi(5)


def test_decorator_records_the_declaration() -> None:
    assert sma_3.key == "sma_3@1"
    assert sma_3.inputs == ("close",) and not sma_3.uses_future
    assert sma_3.code_hash and sma_3.code_hash != rsi_5.code_hash


def test_compute_runs_per_listing_and_blanks_the_warm_up() -> None:
    bars = two_listings()
    out = fs.compute(sma_3, bars)
    assert out.columns == ["listing_id", "date", "sma_3@1"]
    for listing, closes in bars.group_by("listing_id"):
        values = out.filter(pl.col("listing_id") == listing[0])["sma_3@1"].to_list()
        assert values[:2] == [None, None]
        assert values[2] == pytest.approx(sum(closes["close"][:3]) / 3)  # never mixes listings


def test_compute_warm_up_covers_an_indicator_that_is_ready_early() -> None:
    @fs.feature(name="last_close", version=1, inputs=["close"], min_bars=10)
    def last_close() -> pl.Expr:
        return pl.col("close")

    assert fs.compute(last_close, two_listings())["last_close@1"].null_count() == 2 * 9


def test_compute_needs_its_inputs() -> None:
    with pytest.raises(ValueError, match="volume"):

        @fs.feature(name="vol", version=1, inputs=["volume"], min_bars=1)
        def vol() -> pl.Expr:
            return pl.col("volume")

        fs.compute(vol, two_listings())


def test_an_honest_feature_passes_the_no_future_check() -> None:
    assert len(fs.check_no_future(rsi_5, two_listings())) == 8
    fs.check_no_future(sma_3, two_listings().filter(pl.col("listing_id") == 1), by=None)


def test_a_feature_that_peeks_ahead_is_caught() -> None:
    @fs.feature(name="tomorrow", version=1, inputs=["close"], min_bars=1)
    def tomorrow() -> pl.Expr:
        return pl.col("close").shift(-1)

    with pytest.raises(fs.LookaheadError, match="tomorrow@1"):
        fs.check_no_future(tomorrow, two_listings())


def test_a_feature_using_whole_history_statistics_is_caught() -> None:
    @fs.feature(name="demeaned", version=1, inputs=["close"], min_bars=1)
    def demeaned() -> pl.Expr:
        return pl.col("close") - pl.col("close").mean()

    with pytest.raises(fs.LookaheadError):
        fs.check_no_future(demeaned, two_listings())


def test_labels_are_marked_and_exempt() -> None:
    @fs.label(name="fwd_return_5", version=1, inputs=["close"])
    def fwd_return_5() -> pl.Expr:
        return pl.col("close").shift(-5) / pl.col("close") - 1

    assert fwd_return_5.uses_future
    with pytest.raises(ValueError, match="label"):
        fs.check_no_future(fwd_return_5, two_listings())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "Bad Name", "version": 1, "inputs": ["close"], "min_bars": 1},
        {"name": "ok", "version": 0, "inputs": ["close"], "min_bars": 1},
        {"name": "ok", "version": 1, "inputs": [], "min_bars": 1},
        {"name": "ok", "version": 1, "inputs": ["close"], "min_bars": 0},
    ],
)
def test_bad_declarations_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        fs.feature(**kwargs)  # type: ignore[arg-type]
