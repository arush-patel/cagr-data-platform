"""Textbook indicators on one listing's bars, as Polars expressions.

Each function returns an expression over the columns of one listing's bars, sorted by date
(open, high, low, close, volume). Run it per listing with `.over("listing_id")`, or through
`cagr.fs.compute`, which does that for you. Values are null until the window is full.

Wilder smoothing (RSI, ATR, ADX) follows Wilder's own definition: the first value is the simple
mean of the first n inputs, and each later value is prev + (x - prev) / n.
"""

import polars as pl


def _col(col: str | pl.Expr) -> pl.Expr:
    return pl.col(col) if isinstance(col, str) else col


def wilder(x: pl.Expr, n: int) -> pl.Expr:
    """Wilder's moving average of x over n values, seeded with the mean of the first n."""
    mean = x.rolling_mean(n)
    seen = mean.is_not_null().cum_sum()
    seeded = pl.when(seen == 0).then(None).when(seen == 1).then(mean).otherwise(x)
    return pl.when(seen == 0).then(None).otherwise(seeded.ewm_mean(alpha=1 / n, adjust=False))


def sma(n: int, col: str | pl.Expr = "close") -> pl.Expr:
    """Simple moving average over n bars."""
    return _col(col).rolling_mean(n)


def ema(n: int, col: str | pl.Expr = "close") -> pl.Expr:
    """Exponential moving average with alpha 2 / (n + 1), seeded with the SMA of the first n."""
    x = _col(col)
    mean = x.rolling_mean(n)
    seen = mean.is_not_null().cum_sum()
    seeded = pl.when(seen == 0).then(None).when(seen == 1).then(mean).otherwise(x)
    return pl.when(seen == 0).then(None).otherwise(seeded.ewm_mean(span=n, adjust=False))


def returns(n: int = 1, col: str | pl.Expr = "close") -> pl.Expr:
    """Simple return over the last n bars: x / x[n bars ago] - 1."""
    x = _col(col)
    return x / x.shift(n) - 1


def rolling_high(n: int, col: str | pl.Expr = "high") -> pl.Expr:
    """Highest value over the last n bars, today included."""
    return _col(col).rolling_max(n)


def rolling_low(n: int, col: str | pl.Expr = "low") -> pl.Expr:
    """Lowest value over the last n bars, today included."""
    return _col(col).rolling_min(n)


def volume_mean(n: int, col: str | pl.Expr = "volume") -> pl.Expr:
    """Average volume over n bars."""
    return _col(col).rolling_mean(n)


def true_range() -> pl.Expr:
    """The larger of today's range and the gaps from yesterday's close; the range on bar one."""
    high, low, prev_close = pl.col("high"), pl.col("low"), pl.col("close").shift(1)
    return pl.max_horizontal(high - low, (high - prev_close).abs(), (low - prev_close).abs())


def atr(n: int = 14) -> pl.Expr:
    """Average true range, Wilder-smoothed."""
    return wilder(true_range(), n)


def atr_pct(n: int = 14) -> pl.Expr:
    """ATR as a percentage of the close."""
    return atr(n) / pl.col("close") * 100


def rsi(n: int = 14, col: str | pl.Expr = "close") -> pl.Expr:
    """Wilder's relative strength index, 0 to 100. A window with no losses is 100."""
    change = _col(col).diff()
    gain = wilder(change.clip(lower_bound=0), n)
    loss = wilder((-change).clip(lower_bound=0), n)
    return pl.when(loss == 0).then(100.0).otherwise(100 - 100 / (1 + gain / loss))


def _from_bar_two(x: pl.Expr) -> pl.Expr:
    # directional movement needs a previous bar, so DI and its true range start on bar two
    return pl.when(pl.int_range(pl.len()) == 0).then(None).otherwise(x)


def _directional_movement() -> tuple[pl.Expr, pl.Expr]:
    up = pl.col("high").diff()
    down = -pl.col("low").diff()
    plus = pl.when((up > down) & (up > 0)).then(up).otherwise(0.0)
    minus = pl.when((down > up) & (down > 0)).then(down).otherwise(0.0)
    return _from_bar_two(plus), _from_bar_two(minus)


def plus_di(n: int = 14) -> pl.Expr:
    """+DI: smoothed upward movement as a percentage of the smoothed true range."""
    plus, _ = _directional_movement()
    return 100 * wilder(plus, n) / wilder(_from_bar_two(true_range()), n)


def minus_di(n: int = 14) -> pl.Expr:
    """-DI: smoothed downward movement as a percentage of the smoothed true range."""
    _, minus = _directional_movement()
    return 100 * wilder(minus, n) / wilder(_from_bar_two(true_range()), n)


def adx(n: int = 14) -> pl.Expr:
    """Average directional index: Wilder-smoothed DX, where DX = |+DI - -DI| / (+DI + -DI)."""
    p, m = plus_di(n), minus_di(n)
    dx = pl.when(p + m == 0).then(0.0).otherwise(100 * (p - m).abs() / (p + m))
    return wilder(dx, n)
