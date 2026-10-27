# Running the platform

**In short:** put four settings in a `.env` file, run `uv sync`, apply the migrations, then start
Dagster or run a backfill. The pipeline must run from a machine NSE answers (today, Arush's laptop).

## 1. Settings

Create `.env` in the repository root. It is never committed.

```dotenv
CAGR_DATABASE_URL=postgresql://cagr_platform:...@localhost:5438/cagr_test?sslmode=require
CAGR_PIPELINE_DATABASE_URL=postgresql://cagr_writer:...@localhost:5438/cagr_test?sslmode=require
CAGR_READ_DATABASE_URL=postgresql://cagr_read:...@localhost:5438/cagr_test?sslmode=require
CAGR_RAW_ROOT=/path/to/cagr-data/raw
```

| Setting | Used for |
|---|---|
| `CAGR_DATABASE_URL` | Migrations only (owner login) |
| `CAGR_PIPELINE_DATABASE_URL` | The pipeline (writer login) |
| `CAGR_READ_DATABASE_URL` | The toolkit (read-only login) |
| `CAGR_RAW_ROOT` | Folder where original NSE files are saved |

Write a `@` in a password as `%40`.

## 2. Install

```sh
uv sync
```

## 3. Apply migrations

```sh
uv run python -m cagr_data.core.migrate --list   # what would run
uv run python -m cagr_data.core.migrate          # run it
```

## 4. Run the pipeline

```sh
uv run dagster dev -m cagr_data.orchestration.definitions    # opens http://localhost:3000
```

Turn the schedules on in the UI. See [NSE daily pipeline](../pipeline/nse-daily.md) for what they do.

## 5. Or load past dates

```sh
DAGSTER_HOME=~/.dagster uv run python -m cagr_data.orchestration.backfill 2024-01-01 2024-12-31
```

Run a year at a time. It is safe to rerun.

## Quick checks

```sql
-- files received per feed
SELECT feed_id, count(*), min(partition_date), max(partition_date)
  FROM raw.vendor_file GROUP BY feed_id;

-- latest runs
SELECT run_id, kind, status, started_at FROM ops.run ORDER BY run_id DESC LIMIT 10;
```
