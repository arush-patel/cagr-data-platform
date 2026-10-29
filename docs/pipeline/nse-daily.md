# NSE daily pipeline

**In short:** Dagster fetches NSE's files. Every evening from 19:00 to 22:30 IST it tries for the
day's price, index and market-cap files. Each file is saved, recorded, loaded and checked in one
step, and a file we already have is never fetched again. Listings and clean bars are then built by
two commands.

## What Dagster runs (IST)

| When | What | Assets |
|---|---|---|
| Every 30 min, 19:00 to 22:30 | Fetch today's price, index and market-cap files | `nse_cm_bhavcopy`, `nse_index_close`, `nse_mcap` |
| 22:30 | Raise an alert if a trading day's price or index file is still missing | `nse_session_complete` |
| Daily 07:30 | Fetch the company, SME and ETF lists, symbol changes, and corporate actions | `nse_equity_list`, `nse_sme_equity_list`, `nse_etf_list`, `nse_symbol_change`, `nse_corporate_actions` |
| Mondays 07:00 | Fetch holiday lists and rebuild the calendar | `nse_holidays`, `xnse_calendar` |
| Backfill only | Shares of index members, 2010 to 2013 | `nse_index_issue_cap` |

NSE publishes only today's company, SME and ETF lists and symbol changes, so a day the 07:30 run
misses can't be fetched later. They have been collected since 29 Oct 2026.

Daily assets have one partition per calendar day from 1 Jan 2010, weekends included, because NSE
sometimes trades on a weekend. On a day with no trading, NSE answers "not found", and that is
expected.

## What happens to one file

1. **Skip if we have it.** If a file is already recorded for that feed and day, stop.
2. **Download.** One request a second. Retry up to 5 times on errors. "Not found" is an answer,
   not an error.
3. **Log the request** in `raw.fetch_attempt`, whatever the outcome.
4. **Parse.** If the file is malformed, record the failed check and stop.
5. **Save the original file** under `CAGR_RAW_ROOT`. Files are never overwritten.
6. **Record a receipt** in `raw.vendor_file` with the file's hash.
7. **Load the rows** into the `raw` table.
8. **Run the checks** and record the results in `ops.dq_result`.
9. **Commit.** Steps 5 to 8 succeed or fail together.

If a critical check fails, the Dagster run fails after the results are saved, so you can see why.

If we fetch a file again and its contents differ, it is stored as **revision 2** next to the
original, and a warning is raised. This happens for the holiday lists and corporate actions of this
year and next year, which are fetched again on every run.

## After the files: build listings and bars

Dagster doesn't run these yet. Run them after the files are in, in this order:

```sh
uv run python -m cagr_data.reference.listing_build            # listings, corporate actions, ref checks
uv run python -m cagr_data.clean.bars 2026-10-01 2026-10-26   # bars, adjustments, shares, clean checks
uv run python -m cagr_data.reference.universe                 # tracked list, scope new review items
```

| Command | Does | Details |
|---|---|---|
| `reference.listing_build` | Carries listings forward from the last day built, then parses corporate actions. Takes `--through DATE` (default today). | [Listings](../data-model/listings.md) |
| `clean.bars START END` | Rebuilds bars for the range, proposes price adjustments, rebuilds share periods and weekly and monthly bars | [Clean bars](../data-model/clean-bars.md) |
| `reference.universe` | Rebuilds the tracked list and sets aside the review items outside it | [What we track](../data-model/listings.md#what-we-track) |

Both run as the pipeline login, record an `ops.run`, and are safe to run again. Then decide any
new review items on the [Observatory](../observatory/README.md).

## Where files are saved

`{CAGR_RAW_ROOT}/nse/{feed}/{YYYY}/{MM}/{file name}`, for example
`nse/nse_cm_bhavcopy_udiff/2024/07/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip`.

List files get the download date in their name (`EQUITY_L_20261027.csv`). Yearly API responses
are named by year (`nse_corporate_actions_2026.json`).

**Monthly archives.** To keep the folder small, loaded files can be packed into one zip per feed
and month:

```sh
uv run python -m cagr_data.raw.archive --dry-run   # what would be packed
uv run python -m cagr_data.raw.archive             # pack every month that ended 7+ days ago
```

Every file is checked against its recorded hash before and after packing. Only then is the loose
file deleted and the receipt pointed at the archive (`.../2024/07.zip#BhavCopy_...zip`). A file
that fails a check stays loose and is reported.

## Backfill

To load past dates:

```sh
DAGSTER_HOME=~/.dagster uv run python -m cagr_data.orchestration.backfill 2024-01-01 2024-12-31
```

It runs, in order:

1. the price, index and PR zip files, day by day;
2. the holiday lists and the calendar;
3. the missing-file check, day by day.

- It is safe to run again: days already loaded are skipped, and so are weekends and holidays that
  NSE has already said have no file.
- If the database connection drops, it waits up to 30 minutes for it to come back.
- **To go faster, run several at once** over different years, each with `--rate 0.25`, so NSE
  still sees at most one request a second in total. Leave `DAGSTER_HOME` unset when you do this.

## Code

| File | Does |
|---|---|
| `orchestration/assets.py` | The Dagster assets |
| `orchestration/definitions.py` | Jobs and schedules |
| `orchestration/backfill.py` | The backfill command |
| `raw/fetch.py` | Steps 1 to 3 |
| `raw/ingest.py` | Steps 4 to 8 |
| `raw/receipts.py` | Receipts and revisions |
| `raw/archive.py` | Monthly archives |
| `vendors/http.py` | The polite HTTP client |
