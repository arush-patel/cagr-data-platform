# Spec 0001: NSE daily market data fetch

| | |
|---|---|
| Status | Draft for review |
| Date | 2026-10-26 |
| Owner | Arush |
| Component | `platform` (the data platform), version 0.1.0 |
| Database | `cagr_test` (test database, see the "Set up the test database" thread) |
| Depends on | Nothing. This is step one. |
| Next | Spec 0002: reference data (listings, symbol changes, corporate actions). Spec 0003: clean bars. |

## 1. Goal

Every NSE end-of-day file the platform needs arrives every trading evening, is kept exactly as
NSE published it, is parsed into typed rows in the `raw` layer of the test database, and is
checked before anything downstream may use it. The same code backfills history.

When this step is done we can answer, for any date since the backfill start:

- Was it an NSE session? How do we know (holiday list, or a file proving it)?
- Which file did each number come from (URL, time, sha256, size)?
- Did NSE publish anything that day, and if not, did we expect it to?
- Has NSE ever changed a file after we first downloaded it?

## 2. Scope

### In scope

| Feed | Source | Why it is in step one |
|---|---|---|
| Equity bhavcopy (capital market segment) | NSE archives, one zip per session | D1: the primary daily price source |
| Index closes (`ind_close_all`) | NSE archives, one CSV per session | D6: all NSE indices in one file |
| Trading holidays | NSE holiday API, one call per year | D7: tells us which days are expected sessions |
| Listing masters (`EQUITY_L`, `SME_EQUITY_L`) | NSE archives | Snapshot only, stored raw. NSE publishes only today's version, so every day we don't collect is history we can never get back. Interpreting them is spec 0002. |
| Symbol changes (`symbolchange.csv`) | NSE archives | Same reason as the masters. |

Also in scope: the trading calendar built from these facts, file receipts and revisions, the
raw-layer data-quality checks, the Dagster assets and schedule, and the historical backfill.

### Out of scope (later specs)

- Mapping rows to listings and ISIN history, ticker changes, new-listing queue (spec 0002, D4).
- Corporate actions and price adjustment (spec 0002 and 0003, D3).
- Clean adjusted bars, weekly and monthly bars (spec 0003).
- Shares outstanding and market cap (D5), Yahoo cross-check (D2), index history before NSE's
  archive starts.
- Features, the lake export of clean data, the Feature API, the Observatory.
- Deployment to the VM. Step one runs on a laptop against `cagr_test`.

## 3. Decisions this spec relies on

From the project decisions (26 Oct 2026): start from zero with no code taken from earlier repos
or from the removed first slice; Dagster (D9); Polars for dataframes, Postgres as the store,
DuckDB over the lake (D10); checks as rows in `ops.dq_check` with critical checks failing the run
(D11); everything built in the test database, production `stockdb` untouched; per-component
versions; short branches with a PR into main and CI on every PR.

## 4. Sources

All dates are the session date in IST. URLs are what the design work tested on 8 to 10 Oct 2026;
the first build task (section 11, task 0) re-confirms each one from a machine that can reach NSE,
because this cloud environment cannot.

| Feed id | URL pattern | Format | Available from |
|---|---|---|---|
| `nse_cm_bhavcopy_udiff` | `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip` | UDiFF CSV in a zip | 8 Jul 2024 |
| `nse_cm_bhavcopy_legacy` | `https://nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip` | Legacy CSV in a zip | 2010 (no ISIN column before 2011) to 5 Jul 2024 |
| `nse_index_close` | `https://nsearchives.nseindia.com/content/indices/ind_close_all_{DDMMYYYY}.csv` | CSV | To be found in task 0 (404 for 2011 and 2012 dates tested) |
| `nse_holidays` | `https://www.nseindia.com/api/holiday-master?type=trading&year={YYYY}` | JSON | 2011. Without `year=` it returns only the current year. |
| `nse_equity_list` | `https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv` | CSV | Today's snapshot only |
| `nse_sme_equity_list` | `https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv` | CSV | Today's snapshot only |
| `nse_symbol_change` | `https://nsearchives.nseindia.com/content/equities/symbolchange.csv` | CSV | Full history in each snapshot |

The two bhavcopy formats are two feeds, each with the dates it covers on its `raw.feed` row
(section 6), not an `if` in code. If task 0 finds both formats published for an overlapping
period, we fetch both for the overlap and the overlap becomes a parity test.

### Being polite to NSE

- One shared HTTP client (httpx) with a browser user agent, a per-host rate limit (1 request a
  second for backfills, at most 2 a second at night) and retries with back-off (tenacity).
- Archive host first. The API host sits behind bot protection: one call per year for holidays,
  on a cookie session, backing off on 403.
- A file already received is never downloaded again unless we ask for a revision check
  (section 7.4). A rerun reads from storage.

## 5. How the fetch works

```
19:00 IST schedule (retries every 30 min until 22:30)
   │
   ├─ xnse_calendar          holiday list → ref.calendar_exception → ref.calendar_session
   │
   ├─ nse_bhavcopy_file      download one session's zip → object storage → raw.vendor_file receipt
   │     └─ nse_bhavcopy_rows   parse with Polars → raw.nse_cm_bar
   │
   ├─ nse_index_close_file   download → storage → receipt
   │     └─ nse_index_close_rows → raw.nse_index_close
   │
   └─ raw checks             ops.dq_result rows; a critical failure fails the run

07:30 IST daily: nse_equity_list, nse_sme_equity_list, nse_symbol_change snapshots (file + rows)
Mon 07:00 IST weekly: holiday list for this year and next
```

### 5.1 Dagster layout

- **Assets, not jobs.** Each box above is a software-defined asset. File assets and row assets
  are separate, so a parser fix re-materializes rows from stored files without touching NSE.
- **Partitions.** Daily partitions by calendar date in `Asia/Kolkata`, starting at the backfill
  start date. Every calendar day is a partition, not only weekdays, because NSE runs sessions on
  days a weekday rule misses (Saturday budget days, Sunday 1 Feb 2026, Muhurat trading). For a
  day the calendar says is not a session, a 404 is the expected outcome and is recorded as such;
  a 200 is proof of a special session (section 7.2).
- **Snapshots** (masters, symbol changes) are unpartitioned assets materialized daily; each run
  stores a new dated snapshot.
- **Asset checks** wrap the rows in `ops.dq_check`. Dagster shows them; the database stores them.
  Thresholds live in the table, not in code.
- **Schedule.** 19:00 IST for the session's files, retried every 30 minutes until 22:30. After
  22:30 a missing file on an expected session is a critical failure.
- **Thin.** Dagster code only wires functions together. Fetching, parsing, storing and checking
  are plain functions in the platform package, testable without Dagster.
- **Dagster's own storage** (run history, schedules) goes in a separate Postgres database or
  schema on the same server, never in `cagr_test`'s data schemas. Locally `dagster dev` uses its
  default SQLite storage.

### 5.2 Storage of the files themselves

The original bytes are kept, so any row can be re-derived and any dispute settled.

- Path: `{CAGR_RAW_ROOT}/nse/{feed_id}/{YYYY}/{MM}/{original file name}`, plus `@{sha256 prefix}`
  in the name when a revision arrives.
- `CAGR_RAW_ROOT` is a local folder in development and an Azure Blob container once deployed.
  Code goes through one small storage interface so the switch is configuration.
- Files are write-once. A revision is a new object, never an overwrite.

Size check: about 4,150 bhavcopy zips since 2010 at 40 to 210 KB each, plus index files at
about 18 KB each, is roughly 1 GB in total.

## 6. Schema

Schemas created by the first migrations: `ops`, `ref`, `raw`. (`clean` is created by spec 0003.)
Migrations live in `db/migrations/` at the repo root as numbered SQL files, one ordered history for
the whole monorepo. Owner role `cagr_platform` runs them; the fetch job connects as `cagr_writer`;
`cagr_read` reads. Roles and the database itself come from the test database thread.

Server facts (checked 26 Oct 2026): Azure Flexible Server, PostgreSQL 17.11. No extensions
are needed. `btree_gist` cannot be enabled, and TimescaleDB, pg_partman and pg_cron are not
available. So yearly partitions are created by migrations, nothing is scheduled inside the
database, and no table in this step uses an exclusion constraint. Dated reference rows in later
specs key on integer surrogate ids, so a no-overlap rule can use the built-in range opclass
(`EXCLUDE USING gist (int4range(id, id, '[]') WITH =, valid WITH &&)`); the fallback is
`valid_from` only, `UNIQUE (key, valid_from)`, and the end date derived with `LEAD` in a view.
UUIDs, where needed, come from the built-in `gen_random_uuid()`.

Conventions: session dates are `date` in the venue's time zone; timestamps are `timestamptz` in
UTC; prices are `numeric(18,4)` in raw (exactly what the file says); quantities are `bigint`;
money columns end in `_inr` and hold rupees, not crores; no column is silently rounded.

### 6.1 `ops`: runs and checks

| Table | Purpose | Key columns |
|---|---|---|
| `ops.run` | One row per Dagster run or CLI run | `run_id`, `dagster_run_id`, `kind` (nightly, backfill, revision_check), `started_at`, `finished_at`, `status`, `code_version` (git sha), `peak_mem_mb` |
| `ops.dq_check` | Check definitions as data | `check_id`, `layer`, `feed_id`, `severity` (critical, warn, info), `params jsonb`, `owner_action`, `active` |
| `ops.dq_result` | One row per check per partition per run | `run_id`, `check_id`, `partition_date`, `passed`, `observed jsonb`, `detail`, `checked_at` |

Every result is written before a critical failure raises, so a failed run still says why.

### 6.2 `ref`: venue and calendar

| Table | Purpose | Key columns |
|---|---|---|
| `ref.venue` | Exchanges as rows | `venue_id` (`XNSE`), `name`, `time_zone` (`Asia/Kolkata`), `weekend_days`, `regular_open`, `regular_close` |
| `ref.calendar_exception` | The facts the calendar is built from | `venue_id`, `date`, `kind` (holiday, special_session), `description`, `source` (holiday_api, archive_proof), `source_file_id`, `recorded_at` |
| `ref.calendar_session` | Derived, one row per calendar day, never edited by hand | `venue_id`, `date`, `is_session`, `session_index` (dense count of sessions), `reason`, `is_week_end`, `is_month_end` |

Before 2011 the holiday API has nothing. For those years the calendar comes from the archive: a
weekday with a 404 next to published days is a holiday, recorded with `source = archive_proof`.

### 6.3 `raw`: what NSE sent

| Table | Purpose | Key columns |
|---|---|---|
| `raw.vendor` | Vendors as rows | `vendor_id` (`nse_archives`, `nse_api`), `base_url`, `rate_limit_per_s` |
| `raw.feed` | Feeds as rows; the schedule and the parser choice read these | `feed_id`, `vendor_id`, `format` (`udiff_v1`, `legacy_v1`, …), `url_template`, `available_from`, `available_to` (null = still published), `cadence`, `schedule_cron`, `active` |
| `raw.fetch_attempt` | Every HTTP request, including 404s and failures | `attempt_id`, `run_id`, `feed_id`, `partition_date`, `url`, `http_status`, `error`, `elapsed_ms`, `attempted_at` |
| `raw.vendor_file` | A receipt for every file received | `file_id`, `feed_id`, `format`, `partition_date`, `url`, `sha256`, `bytes`, `storage_uri`, `received_at`, `run_id`, `revision` (1, 2, …), `supersedes_file_id`, `row_count` |
| `raw.nse_cm_bar` | One row per security per series per file | `file_id`, `trade_date`, `symbol`, `series`, `isin` (null before 2011), `instrument_type`, `open`, `high`, `low`, `close`, `last`, `prev_close`, `volume`, `traded_value_inr`, `trade_count`, `source_format` |
| `raw.nse_index_close` | One row per index per file | `file_id`, `trade_date`, `index_name`, `open`, `high`, `low`, `close`, `points_change`, `pct_change`, `volume`, `turnover_inr`, `pe`, `pb`, `div_yield` |
| `raw.nse_holiday` | One row per holiday per API response | `file_id`, `date`, `segment`, `description`, `weekday` |
| `raw.nse_equity_list` | One row per listing per snapshot | `file_id`, `snapshot_date`, `board` (main, sme), `symbol`, `name`, `series`, `listing_date`, `paid_up_value`, `market_lot`, `isin`, `face_value` |
| `raw.nse_symbol_change` | One row per change per snapshot | `file_id`, `snapshot_date`, `old_symbol`, `new_symbol`, `change_date`, `company_name` |

Rules for `raw`:

- **Append-only.** Rows are never updated or deleted. Grants give `cagr_writer` INSERT only on
  `raw` row tables.
- **Every row points at its file.** The primary key of each row table starts with `file_id`.
- **All series and instruments are kept.** EQ, BE, BZ, SM, ST, debt, ETFs, rights entitlements:
  raw stores everything the file has. Deciding what is an equity listing is spec 0002.
- **Both bhavcopy formats land in one table** with the same column names; `source_format` says
  which parser produced the row. Columns only one format has (UDiFF's instrument type, settlement
  price and so on) stay in the stored file and can be added later as new nullable columns.
- **Current view.** `raw.nse_cm_bar_current` and `raw.nse_index_close_current` show, per
  `trade_date`, only the rows of the latest revision. Downstream layers read these views.
- **Partitioning.** `raw.nse_cm_bar` is range-partitioned by `trade_date`, one partition a year,
  created by migration (about 3,000 to 3,700 rows a session, so roughly 0.9 M rows a year).
  `ANALYZE` runs after every bulk load, because autovacuum never analyzes a partitioned parent.

## 7. Behaviour

### 7.1 A normal evening

1. Refresh `ref.calendar_session` for the date (cheap; the holiday list is cached).
2. If the date is a session: fetch bhavcopy and index close. Retry on 404 or network errors every
   30 minutes until 22:30.
3. On success: store the bytes, write the receipt, parse with Polars, bulk insert with
   `COPY`, run the raw checks, record results.
4. A critical failure fails the run and alerts (an email for now; Teams later).

### 7.2 Non-session days

The partition still runs, makes one request, and expects a 404. A 404 is recorded in
`raw.fetch_attempt` and the partition succeeds with nothing to load. A 200 on a day the holiday
list does not call a holiday (a weekend) is a special session: the file is loaded and a
`special_session` exception with `source = archive_proof` is written. A 200 on a listed holiday
is a contradiction and a critical failure for a person to look at.

### 7.3 Idempotence

Running a partition twice writes nothing new: the file is already in storage with the same
sha256, the receipt exists, and its rows exist. Tests prove this.

### 7.4 Revisions

A weekly `revision_check` re-downloads the last 10 sessions' files. If the bytes differ, the new
file becomes revision 2 with `supersedes_file_id` set, its rows are inserted, the current views
move to it, and a `vendor_revision` warning lists what changed. Nothing is deleted.

### 7.5 Backfill

- Same assets, run as a Dagster backfill over a date range.
- Proposed start: 1 Jan 2011, the first year with ISIN in bhavcopy and with a holiday list. 2010
  can be added later; its rows need the symbol-change history to map, which is spec 0002 work.
  (Open question Q1.)
- About 4,000 sessions at 1 request a second: roughly 1 to 2 hours of downloads, run in chunks of
  a year so a failure costs one year at most.
- Index closes start where NSE's archive starts (found in task 0). Earlier index history is a
  separate one-off later (D6 says niftyindices.com or Yahoo).

## 8. Data-quality checks (raw layer)

Rows seeded into `ops.dq_check` by migration. Thresholds are parameters, so tuning is a data
change.

| Check | Rule | Severity |
|---|---|---|
| `file_arrived` | An expected session has a bhavcopy and an index file by 22:30 | critical |
| `file_shape` | The file has exactly the columns its format expects | critical |
| `trade_date_matches` | The date inside the file equals the partition date | critical |
| `no_duplicates` | One row per (symbol, series) in bhavcopy; one per index name in index closes | critical |
| `row_count` | Bhavcopy rows within 10% (or 50 rows, whichever is larger) of the median of the previous 20 sessions | critical |
| `holiday_contradiction` | No file on a listed holiday | critical |
| `benchmark_present` | Nifty 50 is in the index file | critical |
| `prices_positive` | open, high, low, close > 0 where volume > 0 | warn |
| `ohlc_order` | low ≤ open, close ≤ high | warn |
| `isin_present` | ISIN on every EQ-series row from 2011 | warn |
| `vendor_revision` | A re-downloaded file differs from the stored one | warn |
| `special_session` | A file arrived on a non-listed weekend day | info |

Checks that need listings, adjusted prices or Yahoo (coverage of the universe, previous-close
continuity, return outliers, vendor mismatch) belong to specs 0002 and 0003.

## 9. Code layout for step one

Only what this step needs; the rest of the planned layout arrives with later specs.

```
cagr-data-platform/
├── pyproject.toml              # uv workspace root
├── platform/
│   ├── pyproject.toml          # package cagr-data, version 0.1.0
│   ├── src/cagr_data/
│   │   ├── core/               # settings (pydantic-settings), db (psycopg 3), storage, logging
│   │   ├── vendors/nse/        # http client, url builders, one parser per feed and format
│   │   ├── raw/                # receipts, revision logic, COPY loaders
│   │   ├── reference/calendar.py
│   │   ├── quality/            # check runner over ops.dq_check
│   │   └── orchestration/      # Dagster definitions only: assets, partitions, schedules, checks
│   └── tests/
│       ├── fixtures/nse/       # small real files, trimmed, fetched fresh in task 0
│       ├── unit/               # parsers, url builders, calendar rules
│       └── db/                 # scenarios against Postgres 17
├── db/migrations/              # 0001_ops.sql, 0002_ref_calendar.sql, 0003_raw.sql, 0004_seed_feeds_checks.sql
├── docs/specs/                 # this spec and the ones after it
└── .github/workflows/ci.yml    # ruff, mypy, pytest with a Postgres 17 service
```

Stack: Python 3.12, uv, ruff, mypy (strict), pytest, httpx, tenacity, Polars, psycopg 3,
pydantic-settings, Dagster. Migrations with a small, plain-SQL tool (open question Q3).

## 10. How we will know it works

Tests (in CI on every PR):

- Parser tests on trimmed real files for each feed and format, including a day either side of the
  legacy-to-UDiFF switch, a pre-2011 file without ISIN, and a special session.
- URL builder tests for every feed and format.
- Calendar tests: holidays, weekends, a special session proven by a file, a contradiction.
- Database scenarios on Postgres 17: receipts, append-only grants, a revision moving the current
  view, idempotent reruns, partitions existing for every year.
- Dagster asset tests with HTTP mocked: 200, 404 on a session, 404 on a holiday, 403 back-off.

Acceptance in `cagr_test` (run by hand, results written into the PR):

1. Backfill Jun to Aug 2024 (crosses the format switch): every session has a file, every check
   passes or has an explained warning.
2. Full backfill from the agreed start date: a session count per year that matches NSE's
   published holiday lists, and a summary of every warning.
3. Five consecutive real evenings run on schedule with no hand fixes.
4. Rerunning any partition writes zero new rows.

## 11. Plan

Each task is one short branch and one PR into main.

| # | Task | Output |
|---|---|---|
| 0 | Source spike, run on a machine that can reach NSE (Arush's laptop or the VM) | Every URL confirmed, earliest index-close date, overlap of the two bhavcopy formats, trimmed fixture files, notes added to this spec |
| 1 | Repo skeleton | uv workspace, `platform` package, ruff, mypy, pytest, CI with Postgres 17, `docs/` |
| 2 | Migrations 0001 to 0004 | `ops`, `ref`, `raw` schemas, seeds for venue, vendors, feeds, formats and checks, grants for the three roles |
| 3 | NSE client and bhavcopy parsers (both formats) | Tested against fixtures |
| 4 | Index close, holidays, masters and symbol-change parsers; calendar builder | Tested |
| 5 | Receipts, storage, loaders, check runner | DB scenario tests |
| 6 | Dagster assets, partitions, schedule, asset checks | Runs locally with `dagster dev` |
| 7 | Backfill Jun to Aug 2024 into `cagr_test`, then the full history | Acceptance results in the PR |
| 8 | Five nightly runs | Step one done; spec 0002 starts |

## 12. Open questions for Arush

| # | Question | Recommendation |
|---|---|---|
| Q1 | Backfill start: 2011 (ISIN and holiday list exist) or 2010? | 2011 now; 2010 later with spec 0002 |
| Q2 | Fetch the listing masters and symbol changes as raw snapshots now, even though they are interpreted in spec 0002? | Yes: they cannot be fetched for the past |
| Q3 | Migration tool: yoyo (what stockdb uses today, plain SQL), Alembic, or a tiny runner of our own? | yoyo, for plain SQL files and familiarity |
| Q4 | Where do raw files live during the test phase: the laptop, or the Azure Blob account straight away? | Laptop now, Blob when deployed; the storage interface makes it configuration |
| Q5 | Who gets alerts for a critical failure in the test phase, and how? | Email to Arush only, until deployment |
| Q6 | Where does the nightly run happen before deployment? NSE must be reachable from it. | Arush's laptop with `dagster dev`, or the VM in a test folder |
