"""Performance analytics from an equity curve, cash flows or trades.

Pure functions: pass in a frame or a series, get a number back. The Observatory and the lab call
the same ones, so a run's CAGR is the same number in both.

Conventions:

- An equity curve is a frame with a date column and a value column, one row per session.
- Cash flows are a frame with a date column and an amount column: money put in is negative,
  money taken out (and the final value) is positive.
- Returns are simple returns per period (a series), as `curve_returns` produces.
- Years are actual days / 365.25 for CAGR, and actual days / 365 for XIRR, as in Excel.
"""

import math
from datetime import date

import polars as pl

TRADING_DAYS = 252
"""Periods per year used to annualise daily volatility and ratios unless told otherwise."""


def _curve(curve: pl.DataFrame, date_col: str, value_col: str) -> pl.DataFrame:
    if curve.height < 2:
        raise ValueError("an equity curve needs at least two rows")
    out = curve.select(
        pl.col(date_col).alias("date"), pl.col(value_col).cast(pl.Float64).alias("v")
    )
    out = out.sort("date")
    if out["v"].null_count():
        raise ValueError("the equity curve has missing values")
    return out


def total_return(curve: pl.DataFrame, date_col: str = "date", value_col: str = "equity") -> float:
    """Last value / first value - 1."""
    c = _curve(curve, date_col, value_col)
    return float(c["v"][-1] / c["v"][0] - 1)


def cagr(curve: pl.DataFrame, date_col: str = "date", value_col: str = "equity") -> float:
    """Compound annual growth rate between the first and last rows of the curve."""
    c = _curve(curve, date_col, value_col)
    first, last = c["date"][0], c["date"][-1]
    years = (last - first).days / 365.25
    if years <= 0:
        raise ValueError("the curve must span more than one day")
    start, end = float(c["v"][0]), float(c["v"][-1])
    if start <= 0:
        raise ValueError("the curve must start above zero")
    if end <= 0:
        return -1.0
    return float((end / start) ** (1 / years) - 1)


def curve_returns(
    curve: pl.DataFrame, date_col: str = "date", value_col: str = "equity"
) -> pl.Series:
    """Simple period returns of the curve (one fewer than its rows)."""
    v = _curve(curve, date_col, value_col)["v"]
    return (v / v.shift(1) - 1).slice(1).rename("return")


def drawdown(
    curve: pl.DataFrame, date_col: str = "date", value_col: str = "equity"
) -> pl.DataFrame:
    """Each row's fall from the highest value so far: date, peak, drawdown (0 or negative)."""
    c = _curve(curve, date_col, value_col)
    return c.select(
        "date",
        pl.col("v").cum_max().alias("peak"),
        (pl.col("v") / pl.col("v").cum_max() - 1).alias("drawdown"),
    )


def max_drawdown(curve: pl.DataFrame, date_col: str = "date", value_col: str = "equity") -> float:
    """The deepest fall from a peak, as a fraction: -0.25 is a 25% drawdown; 0 if none."""
    return float(drawdown(curve, date_col, value_col)["drawdown"].min() or 0.0)  # type: ignore[arg-type]


def max_drawdown_duration(
    curve: pl.DataFrame, date_col: str = "date", value_col: str = "equity"
) -> int:
    """The most rows in a row spent below a previous peak (sessions, for a daily curve)."""
    under = (drawdown(curve, date_col, value_col)["drawdown"] < 0).to_list()
    longest = run = 0
    for below in under:
        run = run + 1 if below else 0
        longest = max(longest, run)
    return longest


def _mean_sd(x: pl.Series) -> tuple[float, float]:
    if x.len() < 2:
        raise ValueError("needs at least two returns")
    return float(x.mean()), float(x.std())  # type: ignore[arg-type]


def volatility(returns: pl.Series, periods_per_year: int = TRADING_DAYS) -> float:
    """Annualised standard deviation of period returns (sample, ddof 1)."""
    _, sd = _mean_sd(returns.drop_nulls())
    return sd * math.sqrt(periods_per_year)


def sharpe(
    returns: pl.Series, risk_free: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> float:
    """Annualised Sharpe ratio. risk_free is an annual rate, spread evenly over the periods."""
    mean, sd = _mean_sd(returns.drop_nulls() - risk_free / periods_per_year)
    if sd == 0:
        return math.nan
    return mean / sd * math.sqrt(periods_per_year)


def sortino(
    returns: pl.Series, risk_free: float = 0.0, periods_per_year: int = TRADING_DAYS
) -> float:
    """Annualised Sortino ratio: like Sharpe, but only shortfalls below the target count."""
    excess = returns.drop_nulls() - risk_free / periods_per_year
    mean, _ = _mean_sd(excess)
    downside = math.sqrt(float((excess.clip(upper_bound=0) ** 2).mean()))  # type: ignore[arg-type]
    if downside == 0:
        return math.nan
    return mean / downside * math.sqrt(periods_per_year)


def xirr(flows: pl.DataFrame, date_col: str = "date", amount_col: str = "amount") -> float:
    """The annual rate at which the cash flows' net present value is zero (Excel's XIRR)."""
    f = flows.select(pl.col(date_col).alias("d"), pl.col(amount_col).cast(pl.Float64).alias("a"))
    amounts: list[float] = f["a"].to_list()
    if not (any(a < 0 for a in amounts) and any(a > 0 for a in amounts)):
        raise ValueError("XIRR needs at least one negative and one positive cash flow")
    dates: list[date] = f["d"].to_list()
    first = min(dates)
    years = [(d - first).days / 365 for d in dates]

    def npv(rate: float) -> float:
        return float(sum(a / (1 + rate) ** t for a, t in zip(amounts, years, strict=True)))

    # bisection: slow but certain, and a few hundred flows still take well under a millisecond
    lo, hi = -0.999999, 1.0
    while npv(lo) * npv(hi) > 0:
        hi *= 2
        if hi > 1e6:
            raise ValueError("XIRR found no rate between -100% and 1e8%")
    for _ in range(200):
        mid = (lo + hi) / 2
        if npv(lo) * npv(mid) <= 0:
            hi = mid
        else:
            lo = mid
        if hi - lo < 1e-12:
            break
    return float((lo + hi) / 2)


def hit_rate(pnl: pl.Series) -> float:
    """Share of trades with a profit. A trade at exactly zero is not a hit."""
    p = pnl.drop_nulls()
    if p.len() == 0:
        raise ValueError("no trades")
    return float((p > 0).sum() / p.len())


def win_loss(pnl: pl.Series) -> dict[str, float]:
    """Average win, average loss (negative), and payoff (average win / |average loss|)."""
    p = pnl.drop_nulls()
    wins, losses = p.filter(p > 0), p.filter(p < 0)
    avg_win = float(wins.mean()) if wins.len() else math.nan  # type: ignore[arg-type]
    avg_loss = float(losses.mean()) if losses.len() else math.nan  # type: ignore[arg-type]
    payoff = avg_win / abs(avg_loss) if wins.len() and losses.len() else math.nan
    return {"avg_win": avg_win, "avg_loss": avg_loss, "payoff": payoff}


def holding_days(
    trades: pl.DataFrame, entry_col: str = "entry_date", exit_col: str = "exit_date"
) -> pl.Series:
    """Calendar days each trade was held. For sessions held, use `cagr.calendar.Calendar`."""
    return trades.select(
        (pl.col(exit_col) - pl.col(entry_col)).dt.total_days().alias("holding_days")
    )["holding_days"]
