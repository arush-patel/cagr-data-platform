# cagr-data-platform

The data layer for the Cagr trading studio, rebuilt from the ground up: NSE end-of-day feeds, reference data
(listings, tickers, calendars, corporate actions), clean adjusted bars, data-quality checks, and a Parquet lake
that backtests read.

This repo is a fresh start. It shares no code with `cagrmeister-momentum-data`; the schema follows the new
design (Postgres 17, layered `ref` → `raw` → `clean` → feature tiers, with `ops` watching all of it).

## Quick start

You need Docker and [uv](https://docs.astral.sh/uv/).

```bash
docker compose up -d db          # Postgres 17 on localhost:54329, databases cagr_dev and cagr_test
uv sync                          # Python 3.12+ virtualenv with the dev tools
uv run cagr migrate              # apply migrations/ to cagr_dev
uv run cagr demo                 # write synthetic NSE files for Sep–Oct 2026 and load them offline
uv run cagr status               # latest runs, feed freshness, review queues
uv run pytest                    # unit tests plus end-to-end tests against cagr_test
```

`make help` lists the same steps. Copy `.env.example` to `.env` to change the database URL or folders.

If Docker Hub rate-limits you, pull the image from a mirror:
`POSTGRES_IMAGE=mirror.gcr.io/library/postgres:17 docker compose up -d db`.

### Real data

```bash
uv run cagr backfill --start 2024-07-08 --end 2024-12-31   # calendar, raw, listings, actions, clean bars
uv run cagr nightly                                         # what the scheduler runs each weekday night
```

Downloads are cached under `data/raw/` (one file per URL), so a rerun never downloads twice. Every command
that downloads also takes `--offline <folder>` to replay saved files instead.

## What a run does

```
bhavcopy ─► raw.vendor_file (receipt) ─► raw.vendor_bar (append-only, revisions kept)
                 │
listing masters, symbol changes ─► ref.listing_change queue ─► ref.listing / listing_symbol
corporate actions (NSE API) ─► clean.corporate_action: parse ─► confirm on the ex-date ─► apply
                                                                                    │
raw + applied actions ─► clean.bar_1d ─► bar_1w, bar_1mo ─► ops.dq_result ─► Parquet lake
```

1. **Calendar.** Holidays come from the NSE holiday list; a special session (a Saturday budget day, Muhurat
   trading) is recorded only when the archive actually has a bhavcopy for it. Sessions are rebuilt from these
   facts with a dense session index, so "20 sessions back" counts sessions, not rows.
2. **Ingest.** One transaction per session. The file is recorded with its sha256 before any row is
   written. A changed number for a bar already loaded becomes a new revision and marks that listing dirty.
3. **Listings.** A new ticker on a known ISIN is applied as a ticker change. A new ISIN with a free ticker
   becomes a new listing. A new ISIN whose ticker another listing still holds waits for review, because it is
   usually a new ISIN for the same company and guessing wrong splits a price history in two.
4. **Corporate actions.** Subjects are parsed into a price factor (bonus, split, consolidation, dividends).
   Rights, demergers and anything unclear go to `needs_review`. An action is confirmed when the bhavcopy's
   previous close on the ex-date matches our last close times the factor (within 1%).
5. **Clean bars.** Built from the latest raw revision of the preferred vendor. `adj_factor` is the product of
   the price factors of applied actions after the bar; prices are multiplied by it, `volume_adj` divided.
   Weekly and monthly bars are resampled from the adjusted daily bars.
6. **Checks.** Rows in `ops.dq_check` decide what runs and how severe a failure is. Every result is written
   to `ops.dq_result`; a critical failure then stops the run before the lake is touched.
7. **Lake.** `data/lake/bars/<tf>/venue=XNSE/year=YYYY/part-0.parquet` and the listings table, written to a
   temporary file and renamed into place.

## Seeing what went wrong

| Question | Where to look |
|---|---|
| Did last night's run work, and how long did each step take? | `cagr status`, `public.run_log` |
| Which check failed, with what numbers? | `ops.dq_result` (one row per check per run, `detail` has the evidence) |
| Is a feed behind? | `ops.feed_freshness` |
| Which file did a bar come from? | `raw.vendor_bar.ingest_run_id` → `raw.vendor_file` for that run and session (URL, sha256, local path) |
| Did NSE change a number after we loaded it? | `raw.vendor_bar` revisions, `clean.dirty_range` with reason `vendor_revision` |
| Which ticker changes are waiting for a person? | `ref.listing_change WHERE status = 'proposed'` |
| Which corporate actions could not be parsed or confirmed? | `clean.corporate_action WHERE status = 'proposed'` |
| A price jump with no announced action? | `clean.corporate_action WHERE source = 'detected'` plus the `prev_close_continuity` result |

## Layout

```
migrations/            numbered SQL files, applied in order, each in one transaction
src/cagr_data/
  core/                database connections, run log, migration runner
  vendors/             NSE archive files and the NSE API: fetch and parse, never touch the database
  reference/           calendar, listings and tickers, corporate actions
  ingest/              bhavcopy → raw
  clean/               raw → adjusted daily bars → weekly and monthly
  quality/             data-quality checks
  lake/                Parquet export
  pipeline.py          the steps in order; the CLI and the Prefect flows both call these
  cli.py               `cagr` command
  flows/               thin Prefect 3 flows, one deployment per row of ref.feed
  sample.py            synthetic NSE files for the demo and the tests
tests/                 parser and calendar unit tests; end-to-end tests on cagr_test
docs/decisions.md      where this repo follows the design, where it differs, and what is not built yet
```

## Tests

`uv run pytest` runs everything. Tests marked `db` need the Docker database and are skipped when it is not
reachable. The end-to-end tests load the synthetic files (a 1:5 split, a ticker rename, a new listing, two
holidays and a special Saturday session) and check:

- the full run: calendar, listings, the split applied, adjusted bars, weekly and monthly resampling, checks,
  and the lake files;
- a rerun is idempotent, and a number NSE revises becomes a new raw revision that flags the bar and marks
  a dirty range;
- an unannounced price jump stops the run;
- a ticker held by another listing waits for review;
- an applied migration that was edited is refused.

CI runs ruff, mypy and pytest against a Postgres 17 service on every push and pull request.
