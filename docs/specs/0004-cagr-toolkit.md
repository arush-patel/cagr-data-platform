# Spec 0004: the `cagr` toolkit

| | |
|---|---|
| Status | Agreed 27 Oct 2026 (decisions in section 9). Built in slices, one PR each (section 8). |
| Owner | Arush |
| Package | `toolkit/`, installed as `cagr-toolkit`, imported as `cagr` |
| Depends on | Nothing for the pure parts. Reads come after specs 0002 (reference data) and 0003 (clean bars). |

## In one minute

- The toolkit is **the shared Python library**. Any code that two parts of the repo need lives
  here: indicators, CAGR and other metrics, features, datasets, splits, reports, and the
  read-only database connection.
- **Everyone imports it**: the lab (research), the platform (nightly jobs) and the Observatory
  backend. They all call the same function, so they all get the same number.
- **It never writes production data** and never talks to NSE. Those jobs stay in `platform`.
- **It protects research from look-ahead.** Features can't read the future, and splits go by
  time.

```python
from cagr import metrics

metrics.cagr(curve)  # the same number in a notebook and in the Observatory
```

## 1. What goes in, what stays out

The test: **if one notebook needs it, it stays in `lab/`. If two places need it, it goes in the
toolkit.**

| In the toolkit | Not in the toolkit |
|---|---|
| Indicators: SMA, EMA, RSI, ATR, ADX, rolling highs and lows | The nightly job that computes features for every stock (`platform`) |
| Metrics: CAGR, XIRR, drawdown, volatility, Sharpe, Sortino, hit rate | The Observatory's strategy engine and simulator (app code) |
| How to declare a feature or a label | Pipelines, migrations, anything that writes production data (`platform`) |
| Point-in-time reads of bars, listings and features | Code that fetches from NSE or any other source |
| Datasets, time-based splits, the no-future check | Models. Trees come from scikit-learn or LightGBM |
| The analysts' formula language | Anything the TypeScript UI imports. The UI calls the Python API |
| Run tracking (MLflow) and Excel reports | Notebooks and experiments (`lab/`) |

## 2. Who uses it

| Who | Uses it to |
|---|---|
| Arush | Write features and labels in `lab/`, build datasets, train trees, write reports |
| Coworker | Build the Observatory backend on it, and do research in `lab/` |
| Platform | Compute the same `@feature` functions nightly |
| Analysts | Nothing directly. The formulas they type in the Observatory run through `cagr.formula` |
| New researchers | `uv sync` and start, without database credentials |

## 3. Where it sits

```
cagr-data-platform/
├── platform/      nightly jobs (import cagr_data)
├── toolkit/       this library (import cagr)
├── lab/           research: features/, labels/, notebooks/, reports/
├── observatory/   later: Python API and worker, React UI
└── docs/specs/
```

Dependencies point one way: **lab, observatory and platform import `cagr`. `cagr` imports none
of them.** A test enforces this.

The library is a separate package inside the one repo, so that:

- a research install stays light (no Dagster, no NSE parsers)
- each run can record which toolkit version made it
- the Observatory can rely on stable functions. A change to what one returns gets a version bump.

## 4. The modules

Three layers. A lower layer never imports a higher one.

| Layer | Module | What it does | Status |
|---|---|---|---|
| Compute (pure, no I/O) | `cagr.ind` | Indicators as Polars expressions | Built |
| | `cagr.metrics` | CAGR, XIRR, drawdown, Sharpe and the rest | Built |
| | `cagr.calendar` | Shift by n sessions, count sessions between dates | Built |
| | `cagr.splits` | Walk-forward, expanding and holdout splits with a gap | Built |
| | `cagr.formula` | Analysts' formulas, such as `close / rolling_max(high, 20) - 1` | Built |
| | `cagr.dataset` | Joins features and labels onto rows, point in time | Built (on frames) |
| Definitions | `cagr.fs` | `@feature` and `@label`, warm-up, the no-future check | Built |
| Access | `cagr.db` | Read-only Postgres connection (`cagr_read`) | Built |
| | `cagr.report` | Excel workbook with a Run sheet first | Built |
| | `cagr.data` | Read listings, bars and market cap from Postgres, as of a date | Built |
| | `cagr.lake` | The same reads from the lake | Waits for the lake export |
| | `cagr.track` | MLflow run logging | Later |
| | `cagr` command | `lake sync`, `preview`, `register`, `promote` | Later |

The connections that write (`cagr_platform` for migrations, `cagr_writer` for Dagster) stay in
`platform`.

## 5. What using it looks like

A feature is a function that returns a Polars expression over one stock's bars:

```python
from cagr import fs, ind


@fs.feature(name="range_contraction", version=1, inputs=["high", "low"], min_bars=25)
def range_contraction():
    rng5 = ind.rolling_high(5) - ind.rolling_low(5)
    rng20 = ind.rolling_high(20) - ind.rolling_low(20)
    return rng5 / rng20.shift(5)
```

A dataset of goldens, with values as of the day before each one starts, split by time:

```python
from cagr import dataset, splits

rows = dataset.event_rows(goldens, calendar, at="T-1")
ds = dataset.build(rows, features={"range_contraction@1": values})
for split in splits.walk_forward(ds.frame, train_years=4, test_months=6, gap_sessions=20):
    ...
```

A report:

```python
from cagr import report

with report.workbook("reports/contraction.xlsx", "Contraction by industry") as wb:
    wb.sheet("By industry", table)
```

## 6. Rules it enforces

1. **No future data.** A feature is recomputed on history cut at random dates. Any difference
   from the full run fails.
2. **Warm-up is blank.** No value until `min_bars` bars exist.
3. **Labels are marked.** A label can only be a target, never a feature.
4. **Versions don't change.** New code or parameters mean a new version.
5. **Point in time.** Every read takes an as-of date, so delisted stocks stay in and today's
   industry is not applied to 2015.
6. **Splits go by time.** A random split raises unless you ask for it.

## 7. How we know it works

- Unit tests on small made-up data for every module, with hand-worked values.
- Property tests, such as "drawdown is never positive".
- A leaky feature (one that reads tomorrow) must be rejected.
- The real check: port two of today's notebooks and compare their trees with today's.
- Speed: five features over every stock since 2015 load in under 2 seconds on the laptop.

## 8. Build slices

| Slice | What | Status |
|---|---|---|
| T1 | Package, `db`, `ind`, `metrics`, `calendar`, `splits`, `fs` | Done |
| T1b | `formula`, `dataset` (on frames), `report` | Done |
| T2 | `data` reads from Postgres, `data.preview`, the first features in `lab/features/` | Done |
| T2b | `lake` reads and `cagr lake sync` | With the lake export spec |
| T3 | `fs.register` (experimental features), datasets read from the lake | After T2 |
| T4 | `track` (MLflow), port the two notebooks | After T3 |
| T5 | `cagr promote`, the Observatory read layer | With the production feature lane |

## 9. Decisions (Arush, 27 Oct 2026)

1. The Observatory backend reads through the toolkit. There is no separate Feature API.
2. Features are Polars. Datasets convert to pandas in one call for scikit-learn.
3. MLflow for tracking: local files now, a server later.
4. Goldens are the main research unit for now. Event rows sit beside (stock, date) rows, so
   another event type can replace goldens later.
5. The coworker does research too. `lab/` gets a folder per person plus a shared one.
6. SharePoint and Teams delivery comes later.
7. Label drafts live in `lab/labels/`.
8. The toolkit is the shared library for everything, not only research reads.
