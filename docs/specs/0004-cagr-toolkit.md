# Spec 0004: the `cagr` toolkit (definition)

| | |
|---|---|
| Status | Draft for discussion. Defines the toolkit; the build is split into slices (section 9), each its own PR. |
| Date | 2026-10-27 |
| Owner | Arush |
| Component | `toolkit` (the Python package `cagr`), new, version 0.1.0 when its first slice lands |
| Depends on | Reads what specs 0002 (reference data) and 0003 (clean bars) produce, and the lake export. The pure parts (section 9, slice T1) depend on nothing. |
| Next | Slice specs or tasks for T1 to T5, once the open questions in section 10 are answered. |

## 1. Goal

One small Python package that every person who does research on the platform imports, so that a
feature, a label, a dataset, a split and a report mean the same thing in a notebook, in the
nightly pipeline and in the Observatory.

When the toolkit is done, this is true:

- A new feature idea goes from a function in a notebook to a logged decision tree in under an
  hour, using every listing and every year, with no Excel file in between.
- A feature is written once. The lab previews it, the platform computes it nightly, the
  Observatory shows it, and all three get the same numbers because they run the same function.
- No dataset can see the future: features are joined as they were known on each date, the
  universe is the one that existed on that date, and splits are by time with a gap.
- Any result (a tree, a report, an Observatory run) can be rebuilt months later, because it
  records the exact data snapshot, feature, label and toolkit versions it used.

## 2. What it is, and what it is not

The toolkit is the **definition language and the read side** of the platform. The platform is
the **jobs that compute and store**. The lab is **where research happens**.

| Is | Is not |
|---|---|
| How to declare a feature or label (name, version, inputs, window, asset classes) | The nightly engine that computes features for every listing (that is `platform`) |
| How to read bars, listings, calendars, universes, features and labels, point in time | A vendor client. It never talks to NSE or any other source |
| Datasets, time-based splits, the no-future test | A model library. Trees and boosting come from scikit-learn, LightGBM and so on |
| Run tracking and the report frame (Run sheet first, saved with the run) | The Observatory UI or its strategy engine |
| The `cagr` command line: `lake sync`, `preview`, `register`, `promote` | A place for research code. Notebooks, experiments and feature ideas live in `lab/` |

The rule that keeps it small: **if only one notebook needs it, it belongs in `lab/`; if two
consumers need the same answer, it belongs in the toolkit.**

## 3. Who uses it

| Who | What they do with it | What they never do |
|---|---|---|
| **Arush (researcher)** | Writes features and label drafts in `lab/`, builds datasets, trains trees, writes reports, promotes features | Read Postgres or the raw files directly from a notebook |
| **Coworker (Observatory backend)** | The Python API and backtest worker read features, labels and snapshots through the toolkit's read layer, so the chart shows what research saw | Recompute a feature inside the app |
| **The platform itself** | The feature engine imports the same `@feature` functions to compute them nightly; parity tests compare production with the experimental values | Define a feature its own way |
| **Analysts** | Nothing directly. Their formulas typed in the Observatory are parsed by the toolkit's formula engine, so a formula means the same in the chart and in the lab | Install Python or clone the repo |
| **Future researchers** | `uv sync`, `cagr lake sync`, first dataset in minutes | Need database credentials to start |
| **Execution (later, team lead's Node service)** | Not a user. It reads strategy versions and served values as data | Import Python |

## 4. Where it lives in the monorepo

```
cagr-data-platform/
├── platform/        # cagr-data (import cagr_data): feeds, reference, clean, feature engine, Dagster
├── toolkit/         # cagr-toolkit (import cagr): this spec
│   ├── src/cagr/
│   └── tests/
├── lab/             # research: features/, labels/, datasets/, notebooks/, models/, reports/
├── observatory/     # later: api/, worker/ (Python, imports cagr), web/ (TypeScript/React)
├── db/migrations/
└── docs/specs/
```

### Which way the dependencies point

```
lab ──────────┐
observatory ──┼──▶ cagr (toolkit) ──▶ polars, duckdb, psycopg
platform ─────┘
```

- The toolkit depends on **nothing else in the repo**. It knows the platform only through
  contracts: the Postgres schemas (`ref`, `clean`, `fb`, ...) and the lake's folder layout.
- `platform` depends on the toolkit (for the `@feature` protocol and the point-in-time rules),
  never the other way round. So installing the toolkit for research does not pull in Dagster,
  httpx or the NSE parsers.
- `lab` and `observatory` never import `cagr_data`. CI enforces this with an import check.

### Why a separate package now that it is one repo

The three-repo design made the toolkit the only shared code, released by tag so the lab could pin
it. In one repo the pin is not needed: `lab/` always uses the toolkit at the same commit, and CI
runs the lab's quick tests on every toolkit change. What stays:

- A separate package keeps research installs light and the dependency direction honest.
- A version number per component (decision of 26 Oct): every run records `cagr` version and git
  commit, so "which toolkit made this?" always has an answer.
- Your coworker is protected from research churn by ownership, not by repos: `lab/` commits
  never change `platform/` or `observatory/`, and a CODEOWNERS file asks for review only where
  code ships.

## 5. What is in it

Modules, in the order they get built. Names are proposals.

| Module | What it does | Replaces today |
|---|---|---|
| `cagr.calendar` | Sessions for a venue, shift by n sessions, last session on or before a date | Weekday arithmetic in notebooks |
| `cagr.data` | Point-in-time reads: bars (adjusted or raw), listings, classifications, universe membership, as of any date. Lake first, Postgres when the lake does not have it | `read_excel` on the feature bank; SQL through the tunnel |
| `cagr.lake` | Where the lake is (`CAGR_LAKE_ROOT`: a laptop folder now, Azure Blob later), local sync of the parts you use | 13 to 17 MB Excel exports |
| `cagr.fs` | `@fs.feature` and `@fs.label` decorators; `preview` (a few listings, nothing stored); `register` (experimental lane: every listing, stored as `name@v`); warm-up blanks; the truncation (no-future) test | The JS feature bank and its Python port |
| `cagr.dataset` | `dataset(features, labels, universe, start, end)` on (listing, date) rows, and `dataset(events=..., at=...)` on event rows such as goldens; cached by recipe hash | `golden_*_dataset.mjs`, the coloured header cells used to pick feature columns |
| `cagr.splits` | `walk_forward`, `expanding`, `holdout_by_date`, each with a gap in sessions; refuses a random split unless asked; counts variants tried per question | The every-5th-row split |
| `cagr.track` | Logs a run: dataset snapshot, versions, split, parameters, metrics, files | Workbooks in `runs/analysis` and `.prev` copies |
| `cagr.report` | `report.workbook(...)`: Run sheet first, your sheets after, saved with the run; SharePoint and Teams later | Hand-built openpyxl code in every notebook |
| `cagr.formula` | Parses and evaluates analysts' formulas (`close / rolling_max(high, 20) - 1`) into the same expressions features use; can only look back | Nothing; new |
| `cagr` CLI | `cagr lake sync`, `cagr preview`, `cagr register`, `cagr promote name@v` | Nothing; new |

### What a feature looks like

Polars, because the platform is Polars throughout (D10). A feature is a function that returns a
Polars expression over one listing's bars; the toolkit runs it per listing, so it cannot
accidentally mix stocks. Cross-sectional features (ranks within an industry) declare
`scope="cross_section"` and receive one date's rows instead.

```python
# lab/features/breakout.py
import polars as pl
from cagr import fs

@fs.feature(name="range_contraction", version=1, inputs=["high", "low"],
            asset_classes=["equity", "index"], min_bars=25)
def range_contraction() -> pl.Expr:
    rng = lambda n: pl.col("high").rolling_max(n) - pl.col("low").rolling_min(n)
    return rng(5) / rng(20).shift(5)       # last 5 sessions' range / the 20 before them
```

```python
# in a notebook
from cagr import fs, dataset, splits, track

fs.preview(range_contraction, listings=["RELIANCE", "TCS"], start="2024-01-01")   # seconds, plotted
fs.register(range_contraction)                          # every listing, stored as range_contraction@1

ds = dataset(events="golden_a@11", at="T-1",              # one row per golden, values as of the day before
             features=["range_contraction@1", "atr14_pct@2"], universe="xnse_liquid")
for train, test in splits.walk_forward(ds, train_years=4, test_months=6, gap_sessions=20):
    with track.run("C1 trees", dataset=ds) as run:
        ...                                             # scikit-learn as today; ds.to_pandas() when needed
```

### Rules the toolkit enforces

1. **No future data.** `register` recomputes a feature on history cut at random dates and
   compares with the full run; any difference fails. The same test runs in CI on promotion.
2. **Warm-up is blank.** A value appears only once `min_bars` bars exist.
3. **Labels are marked.** Anything declared with `@fs.label` carries `uses_future=True`. A
   dataset may put labels in its target columns only; the platform refuses to let a feature,
   signal or strategy version read one.
4. **Versions are immutable.** Changing a feature's code or parameters means a new version.
   `register` refuses to overwrite `name@v` with different code (it hashes the function source).
5. **Point in time.** Every read takes an as-of date. Listings, classifications and universes
   are the ones that existed then, so delisted stocks are in the backtest and today's industry
   is not applied to 2015.
6. **Splits are by time.** A random split on a time series raises unless `allow_random=True`
   is passed, and the run log says so.

## 6. What it reads and writes

| | Where | Owner |
|---|---|---|
| Reads bars, listings, calendars, universes | Lake (`bars/`, `ref/`) and Postgres `clean`, `ref` | Platform writes them (specs 0002, 0003, lake export) |
| Reads production features and labels | Lake `features/`, `labels/`; Postgres for one-stock or one-date lookups | Platform |
| Writes experimental features and label drafts | Lake `features/<name>/v<n>/` (immutable), a row in `fb.feature_def` with lane `experimental` | Toolkit, on `register` |
| Writes datasets | Lake `datasets/<recipe-hash>.parquet` | Toolkit |
| Writes runs and reports | Lake `runs/<run-id>/`, plus the tracking store | Toolkit |
| Never writes | `raw`, `ref`, `clean`, production features | |

The toolkit uses the read-only role (`cagr_read`) for Postgres. Writing the experimental lane's
registry rows needs a narrow research role; that role is designed with the `fb` migrations, not
here.

## 7. Versioning and releases

- `toolkit/pyproject.toml` carries the version; `0.x` until the feature store's contracts settle.
- A release is a tag `toolkit-v0.3.0` on main. The changelog says what changed for researchers.
- A breaking change to what a function returns is a major (or, before 1.0, minor) bump with a
  note in the changelog. Feature and label versions are separate from the toolkit's version.
- Every run, dataset and report records the toolkit version and the git commit.

## 8. How we will know it works

- **Unit tests** for the calendar, splits, the truncation test and the formula parser, on small
  synthetic bars (no database).
- **A leak test that must fail**: a feature that uses `shift(-1)` is rejected by `register`.
- **A parity test**: one feature computed through `fs.preview`, through `register` and by the
  platform's engine gives identical values.
- **The real check**: port two of today's notebooks (`dt_15_variables` and `dt_c1_trees`) onto the
  toolkit and compare their trees and scores with today's, once with the old every-5th split and
  once with a walk-forward split. The difference is the leak today's split hides.
- **Speed**: five features across every listing since 2015 load in under 2 seconds on the laptop.

## 9. Build slices

The toolkit can't read what does not exist yet, so most of it waits for clean bars. The parts that
need no data can start now.

| Slice | What | Needs | Can start |
|---|---|---|---|
| **T1** | Package skeleton in the uv workspace, `cagr.calendar` (reading `ref.calendar_session`), `cagr.splits`, the `@fs.feature` decorator, warm-up and the truncation test on synthetic bars, CI import rule | Nothing beyond spec 0001 | Now |
| **T2** | `cagr.data` and `cagr.lake` over clean bars and listings; `fs.preview`; `cagr lake sync` | Specs 0002 and 0003, the lake export of bars | After 0003 |
| **T3** | `fs.register` (experimental lane), `cagr.dataset` on (listing, date) and event rows | The `fb` registry migration; the first few features | After T2 |
| **T4** | `cagr.track` and `cagr.report`; port the two notebooks | Tracking choice (question 3) | After T3 |
| **T5** | `cagr.formula`, `cagr promote`, the Observatory read layer | The feature engine in the platform; the Observatory backend starts | With the production lane |

## 10. Open questions

Each has a recommendation; the build can start on T1 with the recommendations assumed.

1. **Does the Observatory backend read through the toolkit, or through a Feature API over HTTP?**
   Recommend: through the toolkit. The backend is Python now (decided 26 Oct), so it can import
   the same read layer and get identical point-in-time behaviour with no extra service. A Feature
   API is built only when a non-Python consumer (execution) needs one.
2. **Features written as Polars expressions?** Recommend yes, to match D10 and so the formula
   engine can compile to the same expressions. Datasets hand over to pandas with one call for
   scikit-learn. The cost: today's research code is pandas, so features are written fresh.
3. **Experiment tracking: MLflow, or something lighter first?** Recommend MLflow with a local
   file store on the laptop now, its database on the server later. Lighter option: the toolkit
   writes a `run.json` beside each report and MLflow comes later.
4. **Are goldens (events) the main unit of research?** Today's trees are one row per golden with
   features at T−1 and T. Recommend `dataset(events=..., at=...)` as a first-class shape beside
   (listing, date) rows. Which anchors besides T−1 and T do you use?
5. **Does your coworker write features or research too, or only the Observatory?** This decides
   whether the lab needs review rules and how much of the toolkit's API needs to be stable early.
6. **SharePoint and Teams delivery for reports: in the first version, or later?** Recommend
   later. Reports save with the run and into your synced OneDrive folder first.
7. **Where do label definitions like the golden live while they are drafts?** Recommend
   `lab/labels/` as `@fs.label` functions, promoted into the platform once stable.
