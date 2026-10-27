# Running the platform

Everything runs from the repository root with [uv](https://docs.astral.sh/uv/) and Python 3.12.
The pipeline talks to NSE, so it must run from a machine NSE answers (today, Arush's laptop).

## Settings

Settings come from `CAGR_*` environment variables or a `.env` file in the current folder. `.env` is
git-ignored and never committed.

| Variable | Read by | Login | Used for |
|---|---|---|---|
| `CAGR_DATABASE_URL` | `cagr_data.core.settings`, `core.migrate`, `core.db.connect()` | `cagr_platform` | applying migrations only |
| `CAGR_PIPELINE_DATABASE_URL` | Dagster resource `db` | `cagr_writer` | every pipeline run and the backfill |
| `CAGR_RAW_ROOT` | Dagster resource `raw` | | folder for original vendor files (`core.settings` defaults to `data/raw`, which is git-ignored) |
| `CAGR_READ_DATABASE_URL` | `cagr.db` | `cagr_read` | toolkit reads |
| `CAGR_CODE_VERSION` | assets | | optional; stored in `ops.run.code_version` |
| `DAGSTER_HOME` | Dagster | | where Dagster keeps run history (SQLite); optional for the backfill |
| `CAGR_TEST_DATABASE_URL` | tests | a login that can create roles and databases | database tests |

Connection strings are `postgresql://user:password@host:port/database?sslmode=require`. A
character such as `@` in a password must be percent-encoded (`%40`).

```dotenv
CAGR_DATABASE_URL=postgresql://cagr_platform:...@localhost:5438/cagr_test?sslmode=require
CAGR_PIPELINE_DATABASE_URL=postgresql://cagr_writer:...@localhost:5438/cagr_test?sslmode=require
CAGR_READ_DATABASE_URL=postgresql://cagr_read:...@localhost:5438/cagr_test?sslmode=require
CAGR_RAW_ROOT=/path/to/cagr-data/raw
```

## Install

```sh
uv sync          # creates .venv with both workspace packages and the dev tools
```

## Apply migrations

Run as the owner role. yoyo creates its own `yoyo` schema for bookkeeping and applies each pending
migration in its own transaction, under a lock.

```sh
uv run python -m cagr_data.core.migrate --list   # show what would run
uv run python -m cagr_data.core.migrate          # apply it
```

After a migration that changes seed data (such as 0006), no pipeline restart is needed: checks and
feeds are read from the database on every file.

## Run the pipeline with the Dagster UI

```sh
uv run dagster dev -m cagr_data.orchestration.definitions    # http://localhost:3000
```

Turn on the three schedules in the UI to run the nightly session fetch (19:00 to 22:30 IST), the
weekly calendar refresh (Mondays 07:00) and the daily listing snapshots (07:30). A single
partition can be materialised from the asset page. See [NSE daily pipeline](../pipeline/nse-daily.md).

## Run a backfill

```sh
DAGSTER_HOME=~/.dagster uv run python -m cagr_data.orchestration.backfill 2024-06-01 2024-08-31
```

It prints one line per step (`2024-06-03 files: ok`), waits for the database if the tunnel drops,
and ends with the number of failures (exit code 1 if any). It is safe to rerun: received days are
skipped. Run longer ranges a year at a time.

## Useful queries

Files received per feed:

```sql
SELECT feed_id, count(*) AS files, sum(row_count) AS rows,
       min(partition_date) AS first, max(partition_date) AS last
  FROM raw.vendor_file
 GROUP BY feed_id
 ORDER BY feed_id;
```

Requests by status:

```sql
SELECT feed_id, http_status, count(*) FROM raw.fetch_attempt GROUP BY 1, 2 ORDER BY 1, 2;
```

Latest runs:

```sql
SELECT run_id, kind, status, started_at, finished_at - started_at AS took
  FROM ops.run ORDER BY run_id DESC LIMIT 20;
```

Sessions in a month:

```sql
SELECT count(*) FROM ref.calendar_session
 WHERE venue_id = 'XNSE' AND is_session AND date >= '2024-06-01' AND date < '2024-07-01';
```
