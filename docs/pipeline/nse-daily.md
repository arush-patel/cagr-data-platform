# NSE daily pipeline

**In short:** every evening from 19:00 to 22:30 IST, Dagster tries to fetch the day's price and index
files from NSE. Each file is saved, recorded, loaded and checked in one step. A file we already
have is never fetched again.

## What runs when (IST)

| When | What | Assets |
|---|---|---|
| Every 30 min, 19:00 to 22:30 | Fetch today's price and index files | `nse_cm_bhavcopy`, `nse_index_close` |
| 22:30 | Raise an alert if a trading day's files are still missing | `nse_session_complete` |
| Mondays 07:00 | Fetch holiday lists and rebuild the calendar | `nse_holidays`, `xnse_calendar` |
| Daily 07:30 | Fetch the company lists and symbol changes | `nse_equity_list`, `nse_sme_equity_list`, `nse_symbol_change` |

Price and index assets have one partition per calendar day from 1 Jan 2010, weekends included,
because NSE sometimes trades on a weekend. On a day with no trading, NSE answers "not found", and
that is expected.

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

## If NSE changes a file

If we fetch a file again and its contents differ, it is stored as **revision 2** next to the
original, not over it, and a warning is raised. Today this only happens for the holiday lists of
this year and next year, which are fetched again every week.

## Where files are saved

`{CAGR_RAW_ROOT}/nse/{feed}/{YYYY}/{MM}/{file name}`, for example
`nse/nse_cm_bhavcopy_udiff/2024/07/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip`.

Company lists get the download date in their name (`EQUITY_L_20261027.csv`).

## Backfill

To load past dates:

```sh
DAGSTER_HOME=~/.dagster uv run python -m cagr_data.orchestration.backfill 2024-01-01 2024-12-31
```

It runs, in order:

1. the price and index files, day by day;
2. the holiday lists and the calendar;
3. the missing-file check, day by day.

It is safe to run again, because days already loaded are skipped. If the database connection drops,
it waits up to 30 minutes for it to come back. At one request a second, a year takes about
12 minutes.

## Code

| File | Does |
|---|---|
| `orchestration/assets.py` | The Dagster assets |
| `orchestration/definitions.py` | Jobs and schedules |
| `orchestration/backfill.py` | The backfill command |
| `raw/fetch.py` | Steps 1 to 3 |
| `raw/ingest.py` | Steps 4 to 8 |
| `raw/receipts.py` | Receipts and revisions |
| `vendors/http.py` | The polite HTTP client |
