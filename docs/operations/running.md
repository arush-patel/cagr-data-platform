# Running the platform

**In short:** put your settings in a `.env` file, run `uv sync`, apply the migrations, start
Dagster (or run a backfill), then build listings and bars. The pipeline must run from a machine NSE answers (today,
Arush's laptop).

## 1. Settings

Create `.env` in the repository root. It is never committed.

```dotenv
CAGR_DATABASE_URL=postgresql://cagr_platform:...@localhost:5438/cagr_test?sslmode=require
CAGR_PIPELINE_DATABASE_URL=postgresql://cagr_writer:...@localhost:5438/cagr_test?sslmode=require
CAGR_READ_DATABASE_URL=postgresql://cagr_read:...@localhost:5438/cagr_test?sslmode=require
CAGR_REVIEW_DATABASE_URL=postgresql://cagr_reviewer:...@localhost:5438/cagr_test?sslmode=require
CAGR_REVIEWER=arush
CAGR_RAW_ROOT=/path/to/cagr-data/raw
```

| Setting | Used for |
|---|---|
| `CAGR_DATABASE_URL` | Migrations only (owner login) |
| `CAGR_PIPELINE_DATABASE_URL` | The pipeline (writer login) |
| `CAGR_READ_DATABASE_URL` | The toolkit (read-only login) |
| `CAGR_REVIEW_DATABASE_URL` | The Observatory's review decisions (reviewer login) |
| `CAGR_REVIEWER` | Your name, recorded on each decision you make on the review page |
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

On Arush's laptop the Dagster daemon runs as a login item, so the schedules fire whenever the
laptop is awake (and the database tunnel is up). The login item is
[`ops/macos/com.cagr.dagster-daemon.plist`](../../ops/macos/com.cagr.dagster-daemon.plist). It
holds no passwords: `uv run --env-file .env` reads them when the daemon starts.

```sh
# once: fill in HOME_DIR and UV in the plist, then
cp ops/macos/com.cagr.dagster-daemon.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cagr.dagster-daemon.plist
DAGSTER_HOME=~/.dagster uv run --env-file .env dagster schedule start --start-all \
    -m cagr_data.orchestration.definitions
tail ~/.dagster/daemon.log                                            # is it running?
launchctl bootout gui/$(id -u)/com.cagr.dagster-daemon                 # stop it
```

To watch runs in a browser, or without the login item:

```sh
DAGSTER_HOME=~/.dagster uv run --env-file .env dagster dev -m cagr_data.orchestration.definitions
```

That opens http://localhost:3000, where schedules can also be turned on. See
[NSE daily pipeline](../pipeline/nse-daily.md) for what they do.

To load past dates instead:

```sh
DAGSTER_HOME=~/.dagster uv run python -m cagr_data.orchestration.backfill 2024-01-01 2024-12-31
```

Run a year at a time. It is safe to rerun.

## 5. Build listings and bars

After new files are in:

```sh
uv run python -m cagr_data.reference.listing_build
uv run python -m cagr_data.clean.bars 2026-10-01 2026-10-26
```

## 6. Decide review items

```sh
uv run observatory      # the review page on http://localhost:8400
```

See [Observatory](../observatory/README.md) for building the UI the first time.

## Now and then

| When | Run |
|---|---|
| Once, to load Arush's industries | `uv run python -m cagr_data.reference.industry db/seeds/group3_2026-10-27.csv` |
| Monthly, to pack old raw files into archives | `uv run python -m cagr_data.raw.archive` |

## Quick checks

```sql
-- files received per feed
SELECT feed_id, count(*), min(partition_date), max(partition_date)
  FROM raw.vendor_file GROUP BY feed_id;

-- latest runs
SELECT run_id, kind, status, started_at FROM ops.run ORDER BY run_id DESC LIMIT 10;

-- review items waiting
SELECT 'adjustment', count(*) FROM ref.price_adjustment WHERE status = 'pending'
UNION ALL
SELECT 'listing change', count(*) FROM ref.listing_change WHERE status = 'pending';
```
