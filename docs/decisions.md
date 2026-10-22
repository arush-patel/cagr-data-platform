# Design decisions: what this slice builds

The design lives in three documents: the Handbook, the Platform design (its Data platform section holds
decisions D1–D17) and the Schema blueprint (the Postgres 17 DDL). This page says which of them the first
slice implements, where the code differs from the blueprint and why, and what comes next.

## Decisions

| | Decision | In this slice |
|---|---|---|
| D1 | NSE bhavcopy is the primary daily price source | Built. UDiFF from 8 Jul 2024, the legacy format before that. Each file gets a receipt (URL, sha256, size, local path) before its rows are written. |
| D2 | Yahoo as a secondary cross-check | Not built (decision still open). |
| D3 | Corporate actions from the NSE API, parsed, with a review queue | Built. Bonus, split, consolidation and dividends parse to a price factor; rights, demergers and arrangements go to `needs_review`. Unparsed text with an ex-date within 3 sessions fails the run (`ca_needs_review`, critical). Nightly fetches today−7 … today+30. |
| D4 | Listing masters, symbol changes and an ISIN check on every bhavcopy | Built. All changes go through `ref.listing_change`; only exact-ISIN cases auto-apply. |
| D5 | Market cap from shares outstanding | Not built. Tables exist (`ref.shares_outstanding`, `ref.market_cap_daily`). |
| D6 | Index data from ind_close_all | Not built. |
| D7 | Calendar from NSE's holiday list, proven by the archive | Built. See "Calendar exceptions" below. |
| D9 | Prefect 3, thin flows | Built: `flows/xnse.py` wraps `pipeline.py`; `flows/deploy.py` serves one deployment per `ref.feed` row. Not yet run against a Prefect server. |
| D10 | pandas first, Postgres as the store | Followed. |
| D11 | Checks as rows in `ops.dq_check`, critical checks fail the flow | Built. 13 of the 23 seeded checks run; the other 10 need parts not built yet (index feed, Yahoo cross-check, market cap, feature builds, serving). Every result is stored before a critical failure raises. |
| D12 | Feature contract | Registry tables (`fb.*`) exist; no feature families yet. |
| D13 | One repo, layered packages, thin flows | Followed. |
| D14–D17 | Classification, restatement, approval workflow | Tables exist (`ref.classification_scheme`, `ref.industry`, `ref.instrument_classification`, `ref.listing_change`). The review page, Teams cards and the "proposer cannot approve" rule are not built. |

## Where the code differs from the blueprint

Each of these is a migration in `migrations/`, so the difference is visible in one place.

1. **Migration runner.** A small runner in `core/migrate.py` instead of a migration library: numbered SQL
   files, each applied in one transaction, recorded with its sha256 in `ops.schema_migration`. Editing a file
   that was already applied is refused, so a schema change is always a new file. `cagr migrate --reset` only
   works on databases whose name ends in `_dev` or `_test`.
2. **File receipts** (`0003`). `raw.vendor_file` records every file we loaded (URL, sha256, bytes, HTTP status,
   local path, ingest run). `raw.vendor_file_missing` records a 404 on a day we expected a file. Together
   they answer "which file did this number come from" and "did NSE publish anything that day".
3. **More bhavcopy columns on `raw.vendor_bar`** (`0003`): `prev_close`, `trade_count`, `series`. The
   previous close is the evidence that confirms a corporate action and detects an unannounced one.
4. **Calendar exceptions** (`0009`). `ref.calendar_exception` holds the facts the sessions are built from:
   holidays from the NSE list and special sessions proven by a bhavcopy on a non-weekday. `ref.calendar` gains
   its regular hours and weekend days. Sessions are rebuilt from these facts, never edited by hand.
5. **`clean.dirty_range.clean_rebuilt_at`** (`0009`). The clean layer marks the ranges it has rebuilt, so the
   feature builder and the clean builder can consume the same dirty range independently.
6. **Ticker history starts at the effective date** (`0007`). In `ref.apply_listing_change`, a new listing's
   first ticker is valid from the change's effective date, not from the master's listing date. Otherwise a
   ticker the master reports today would claim history from before a rename.
7. **Three data-quality checks added** (`0009`): `raw_row_count` (a session's listing count against the median of the
   previous 20 sessions, within 10% or 5 listings), `prev_close_continuity` (a jump between our close and NSE's previous close with
   no confirmed or applied action; critical), `return_outlier` (a daily move over 25%; warning).
8. **Left out for now**: the `llm` schema, the legacy feature tables and compatibility views, `aif_base`, and
   the strategy, observatory, models, futures and costs tables. They belong to later slices and nothing in
   this one reads them.

## Known gaps

- **History before 2011.** Those bhavcopies have no ISIN. Rows are mapped through the ticker history when a
  ticker is known on that date; otherwise they are counted as unmapped. Building the old ticker history from
  `symbolchange.csv` is the next step for a full backfill.
- **The coverage check's cohort** is "listings that traded in the previous 5 sessions", a stand-in until the
  universe tables are filled.
- **Index feed, market cap, universe membership** are not loaded yet (D5, D6).
- **NSE was not reachable from the build environment**, so every test runs on synthetic files that follow the
  real formats. The first run against the real archive should be a short backfill (a few weeks of 2024 on
  both sides of the 8 Jul format change) before any long one.

## Next slices

1. A real backfill from 2011 on a machine that can reach NSE, and the checks' first real numbers.
2. The pre-2011 ticker history, the index feed and market cap.
3. The review page for `ref.listing_change` and `needs_review` corporate actions.
4. The first feature families on the `fb` registry, with parity against today's values.
