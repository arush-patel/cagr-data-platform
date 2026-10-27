# Toolkit (`cagr`)

**In short:** `cagr` is the shared Python library. Every part of the platform uses it, so a number
such as CAGR is calculated one way everywhere. Inputs and outputs are Polars frames.

| Module | For |
|---|---|
| [`cagr.ind`](#cagrind) | Indicators: SMA, EMA, RSI, ATR, ADX, ... |
| [`cagr.metrics`](#cagrmetrics) | Performance: CAGR, drawdown, Sharpe, XIRR, ... |
| [`cagr.calendar`](#cagrcalendar) | Counting in trading days |
| [`cagr.splits`](#cagrsplits) | Train and test splits by date |
| [`cagr.fs`](#cagrfs) | Declaring features and checking they don't look ahead |
| [`cagr.db`](#cagrdb) | Read-only database access |

Rules (enforced by tests): only `cagr.db` touches the database, and the toolkit never imports the
platform.

## `cagr.ind`

Each function returns a Polars expression for one stock's bars, sorted by date. Values are empty
until there are enough bars.

| Function | Gives |
|---|---|
| `sma(n)`, `ema(n)` | Simple and exponential moving average of close |
| `returns(n=1)` | Return over n bars |
| `rolling_high(n)`, `rolling_low(n)` | Highest high and lowest low over n bars |
| `volume_mean(n)` | Average volume over n bars |
| `true_range()`, `atr(n=14)`, `atr_pct(n=14)` | True range, average true range, and ATR as a percentage of close |
| `rsi(n=14)` | Wilder RSI, 0 to 100 |
| `plus_di(n=14)`, `minus_di(n=14)`, `adx(n=14)` | Directional indicators and ADX |

For many stocks, add `.over("listing_id")`:

```python
bars.with_columns(ind.rsi(14).over("listing_id").alias("rsi_14"))
```

## `cagr.metrics`

Plain functions that return a number.

| Function | Gives |
|---|---|
| `total_return(curve)` | Last value ÷ first value − 1 |
| `cagr(curve)` | Compound annual growth rate |
| `drawdown(curve)` | Fall from the peak, for each date |
| `max_drawdown(curve)` | Deepest fall (−0.25 means 25%) |
| `max_drawdown_duration(curve)` | Longest stretch below a previous peak, in rows |
| `curve_returns(curve)` | Daily returns of the curve |
| `volatility(returns)` | Annualised volatility |
| `sharpe(returns)`, `sortino(returns)` | Annualised Sharpe and Sortino ratios |
| `xirr(flows)` | Annual return on dated cash flows, as in Excel |
| `hit_rate(pnl)`, `win_loss(pnl)` | Share of winning trades; average win, average loss and payoff |
| `holding_days(trades)` | Calendar days each trade was held |

- A **curve** is a frame with `date` and `equity` columns.
- **Cash flows** have `date` and `amount` columns. Money put in is negative, and money taken out
  is positive.
- Ratios assume 252 trading days a year.

```python
curve = pl.DataFrame({"date": [date(2020, 1, 1), date(2022, 1, 1)], "equity": [100.0, 121.0]})
metrics.cagr(curve)  # 0.0999...
```

## `cagr.calendar`

`Calendar` answers questions in trading days instead of calendar days.

| Method | Gives |
|---|---|
| `is_session(d)` | Was d a trading day? |
| `shift(d, n)` | The trading day n sessions after d (before, if n is negative) |
| `on_or_before(d)`, `on_or_after(d)` | The nearest trading day |
| `between(a, b)` | Trading days from a to b |
| `count(a, b)` | Number of trading days after a, up to and including b |

```python
cal = load_calendar(connect_read())
cal.shift(date(2024, 6, 14), 1)  # 2024-06-18 (the 17th was a holiday)
```

## `cagr.splits`

These split rows by date, never randomly, and leave a gap between train and test so a label that
looks ahead can't leak.

| Function | Gives |
|---|---|
| `holdout_by_date(ds, test_start, gap_sessions=...)` | One split: train before the date, test from it |
| `walk_forward(ds, train_years=..., test_months=..., gap_sessions=...)` | A rolling window of splits |
| `expanding(ds, min_train_years=..., test_months=..., gap_sessions=...)` | Like `walk_forward`, but training always starts at the first date |
| `random_split(...)` | Refused unless you pass `allow_random=True` |

Set `gap_sessions` to at least your label's horizon.

## `cagr.fs`

This module declares features, computes them, and checks that they don't use future data.

```python
@fs.feature(name="rsi_14", version=1, inputs=["close"], min_bars=15)
def rsi_14():
    return ind.rsi(14)


fs.compute(rsi_14, bars)  # columns: listing_id, date, rsi_14@1
fs.check_no_future(rsi_14, bars)  # raises LookaheadError if it peeks ahead
```

- `check_no_future` recomputes the feature on history cut at 8 dates. If any past value changes,
  the feature is using the future.
- `@fs.label(...)` declares a target. Labels look ahead on purpose and are marked as doing so.
- If you change a feature's code, raise its version number.

## `cagr.db`

| Function | Does |
|---|---|
| `connect_read()` | Opens a read-only connection using `CAGR_READ_DATABASE_URL` |
| `read_frame(conn, sql, params)` | Runs a query and returns a Polars frame |
| `load_calendar(conn)` | Returns the trading calendar as a `Calendar` |

```python
with connect_read() as conn:
    bars = read_frame(conn, "SELECT * FROM raw.nse_cm_bar_current WHERE symbol = %s", ("INFY",))
```

Prices in `raw` are not yet adjusted for splits or bonuses. That is spec 0003.
