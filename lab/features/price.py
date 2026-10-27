"""The first shared features: returns, momentum, volatility, range and volume, on daily bars.

Each one is a cagr.fs feature, so it is previewed, checked for look-ahead and later computed
by the platform the same way. Change a feature's code only with a new version.
"""

import polars as pl

from cagr import fs, ind

EVERYTHING = ["equity", "etf", "index"]


@fs.feature(name="ret_20", version=1, inputs=["close"], min_bars=21, asset_classes=EVERYTHING)
def ret_20() -> pl.Expr:
    """Return over the last 20 sessions."""
    return ind.returns(20)


@fs.feature(name="rsi_14", version=1, inputs=["close"], min_bars=15, asset_classes=EVERYTHING)
def rsi_14() -> pl.Expr:
    """Wilder RSI over 14 sessions, 0 to 100."""
    return ind.rsi(14)


@fs.feature(
    name="atr_pct_14",
    version=1,
    inputs=["high", "low", "close"],
    min_bars=14,
    asset_classes=EVERYTHING,
)
def atr_pct_14() -> pl.Expr:
    """Average true range over 14 sessions, as a percentage of the close."""
    return ind.atr_pct(14)


@fs.feature(
    name="dist_52w_high",
    version=1,
    inputs=["high", "close"],
    min_bars=252,
    asset_classes=EVERYTHING,
)
def dist_52w_high() -> pl.Expr:
    """How far the close is below the highest high of the last 252 sessions (0 at the high)."""
    return pl.col("close") / ind.rolling_high(252) - 1


@fs.feature(
    name="range_contraction",
    version=1,
    inputs=["high", "low"],
    min_bars=25,
    asset_classes=EVERYTHING,
)
def range_contraction() -> pl.Expr:
    """The last 5 sessions' high-low range divided by the range of the 20 sessions before."""
    rng5 = ind.rolling_high(5) - ind.rolling_low(5)
    rng20 = ind.rolling_high(20) - ind.rolling_low(20)
    return rng5 / rng20.shift(5)


@fs.feature(name="volume_ratio_20", version=1, inputs=["volume"], min_bars=21)
def volume_ratio_20() -> pl.Expr:
    """Today's volume over the average of the 20 sessions before it."""
    return pl.col("volume") / ind.volume_mean(20).shift(1)


ALL = [ret_20, rsi_14, atr_pct_14, dist_52w_high, range_contraction, volume_ratio_20]
