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
| [`cagr.formula`](#cagrformula) | Analysts' formulas, such as `close / rolling_max(high, 20) - 1` |
| [`cagr.dataset`](#cagrdataset) | Rows to learn from, with features and labels joined point in time |
| [`cagr.data`](#cagrdata) | Listings, bars and market cap from the database, as of a date |
| [`cagr.report`](#cagrreport) | Excel workbooks that say what made them |
| [`cagr.db`](#cagrdb) | Read-only database connection |

Rules (enforced by tests): only `cagr.db`, `cagr.data` and `cagr.report` read or write anything,
and the toolkit never imports the platform.

```python
from cagr import data, db, ind

with db.connect_read() as conn:
    ids = data.lookup(conn, ["RELIANCE"], date(2024, 6, 28))
    bars = data.bars(conn, date(2024, 1, 1), date(2024, 6, 28), listings=list(ids.values()))
bars.with_columns(ind.rsi(14).over("listing_id").alias("rsi_14"))
```

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

## `cagr.formula`

A small language for formulas typed by analysts. It compiles to the same Polars expressions as
features, and it can only look back.

```python
formula.compile_formula("rsi(14) > 70 and close > sma(close, 50)")  # a Polars expression
f = formula.to_feature("close / rolling_max(high, 20) - 1", name="off_20d_high", version=1)
```

| Allowed | |
|---|---|
| Columns | `open`, `high`, `low`, `close`, `volume`, and numbers |
| Operators | `+ - * /`, `< <= > >= == !=`, `and or not`, brackets |
| Functions | `lag`, `sma`, `ema`, `rolling_max`, `rolling_min`, `rolling_std`, `change`, `abs`, `log`, `min`, `max` |
| Indicators | `rsi(n)`, `atr(n)`, `atr_pct(n)`, `adx(n)` |

Windows must be whole numbers of at least 1. Anything else raises `FormulaError` before it runs.
`to_feature` works out the inputs and warm-up for you.

## `cagr.dataset`

Builds the rows a model learns from. Features are joined on the exact `(listing_id, date)`, so a
row never sees a later value.

| Function | Gives |
|---|---|
| `event_rows(events, calendar, at="T-1")` | One row per event (a golden, say), dated `at` sessions from its `start_date` |
| `build(rows, features={...}, labels={...})` | A `Dataset`: the joined frame, `X()`, `y()` and a `recipe_hash` |
| `asof(rows, values, column, max_days=...)` | The latest value on or before each row, for slow inputs like shares |

`build` refuses a label used as a feature (`LeakError`) and a feature with two values for one row.

## `cagr.data`

Point-in-time reads through the `cagr_read` login. Every function takes an open connection.

| Function | Gives |
|---|---|
| `listings(conn, as_of)` | Every listing that had traded by that date, with the symbol and ISIN it had then. Delisted ones stay in |
| `lookup(conn, symbols, as_of)` | `{symbol: listing_id}` as of the date. Raises `KeyError` for an unknown symbol |
| `bars(conn, start, end, listings=..., timeframe="1d", adjusted=True, warmup=0)` | Bars sorted by listing and date. `timeframe` is `1d`, `1w` or `1mo` |
| `market_cap(conn, start, end)` | Daily shares and market cap in rupees |
| `classification(conn, as_of)` | Industry, sector index and index tier |
| `holds(conn)` | Listings whose adjusted bars are held for a pending corporate action |
| `adjustment_state(conn)` | One number for the adjusted history. Record it with a run |
| `preview(feature, conn, symbols, start, end)` | A feature's values for a few symbols, with its warm-up read for you |

- Adjusted bars stop the session before a pending adjustment, so features never see a false jump.
  Unadjusted bars are always complete.
- There is no bar on a day a stock didn't trade.
- Weekly and monthly bars are dated by their last session, and `complete` is false for the
  current week or month.
- `warmup=n` adds up to n bars before `start` for each listing. A feature needs `min_bars - 1`.

## `cagr.report`

```python
with report.workbook("reports/contraction.xlsx", "Contraction by industry", run=info) as wb:
    wb.sheet("By industry", table)
```

The first sheet, Run, records the title, the time, the toolkit version and whatever you pass in
`run`. Your sheets follow in order. The file is written only if the block finishes without an
error.

## `cagr.db`

| Function | Does |
|---|---|
| `connect_read()` | Opens a read-only connection using `CAGR_READ_DATABASE_URL` |
| `read_frame(conn, sql, params)` | Runs a query and returns a Polars frame |
| `load_calendar(conn)` | Returns the trading calendar as a `Calendar` |

Use `cagr.data` for listings and bars. `read_frame` is for anything else `cagr_read` can see.

## Shared features

The first shared features live in [`lab/features/price.py`](../../lab/features/price.py):
`ret_20`, `rsi_14`, `atr_pct_14`, `dist_52w_high`, `range_contraction` and `volume_ratio_20`. A
test in `lab/tests/` checks every one for look-ahead.
