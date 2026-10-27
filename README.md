# cagr-data-platform

One repository for the Cagr trading data platform: data pipelines, reference data, features,
the research lab, the Observatory and the `cagr` toolkit. It is being built step by step;
see `docs/specs/` for what each step does.

## What is here so far

- `platform/`: the data platform package `cagr-data` (Python).
- `toolkit/`: the shared library `cagr-toolkit`, imported as `cagr` (spec 0004): indicators,
  performance metrics, session calendars, time-based splits, feature declarations and read-only
  database access. It never imports `cagr_data`.
- `db/migrations/`: numbered SQL migrations for every schema, applied in order with yoyo.
- `docs/specs/`: one spec per step. Step one is `0001-nse-daily-market-data.md`.

## Working on it

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
uv sync                    # create .venv with every workspace package
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # type-check
uv run pytest              # tests; database tests run when CAGR_TEST_DATABASE_URL is set

# apply pending migrations as the owner role (cagr_platform), using CAGR_DATABASE_URL
uv run python -m cagr_data.core.migrate --list   # what would run
uv run python -m cagr_data.core.migrate          # run it
```

`CAGR_TEST_DATABASE_URL` must be a login that can create roles and databases: the tests make
the three platform roles if missing and a fresh `cagr_migrate_test` database for each run.

Settings come from `CAGR_*` environment variables or a `.env` file (never committed):

- `CAGR_DATABASE_URL`: the owner's login (`cagr_platform`), used only to apply migrations.
- `CAGR_PIPELINE_DATABASE_URL`: the pipeline's login (`cagr_writer`), used by Dagster.
- `CAGR_RAW_ROOT`: the folder where original vendor files are kept.
- `CAGR_READ_DATABASE_URL`: a read-only login (`cagr_read`), used by the toolkit's `cagr.db`.

## Running the NSE pipeline (spec 0001)

```sh
uv run dagster dev -m cagr_data.orchestration.definitions   # UI on http://localhost:3000
```

Locally, Dagster keeps its own run history in `DAGSTER_HOME` (SQLite), never in the platform
database. Assets, all in group `nse`:

| Asset | Partitions | Schedule (IST) |
|---|---|---|
| `nse_cm_bhavcopy`, `nse_index_close` | every calendar day from 2010-01-01 | 19:00 to 22:30, every 30 minutes |
| `nse_session_complete` (the `file_arrived` check) | every calendar day | same runs; decides at 22:30 |
| `nse_holidays` → `xnse_calendar` | none | Mondays 07:00 |
| `nse_equity_list`, `nse_sme_equity_list`, `nse_symbol_change` | none | daily 07:30 |

A backfill runs in this order, a year at a time: `nse_cm_bhavcopy` and `nse_index_close` over
the range, then `nse_holidays` and `xnse_calendar` (which records the 2010 holidays the archive
proves), then `nse_session_complete` over the same range. One command does all of it:

```sh
DAGSTER_HOME=~/.dagster uv run python -m cagr_data.orchestration.backfill 2024-06-01 2024-08-31
```
