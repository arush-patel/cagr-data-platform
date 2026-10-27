# Spec 0007: daily shares outstanding and market cap

| | |
|---|---|
| Status | Draft, 27 Oct 2026 |
| Date | 2026-10-27 |
| Owner | Arush |
| Component | `platform` (`cagr_data.vendors.nse`, `cagr_data.reference`, `cagr_data.clean`) |
| Database | `cagr_test` |
| Depends on | Spec 0002 (listings, corporate actions, adjustments) and spec 0003 (clean daily bars) |
| Next | The universe floor and `cagr_industry` review items (spec 0006 task 4), then the 52 industry indices |

## 1. Goal

Every equity listing has its shares outstanding and market cap on every session since 2010,
point in time: a value on day *d* uses only what was known by *d*'s close. The universe, the
industry review list and the cap-weighted industry indices all read it (decision D5).

## 2. What we found

- **The bhavcopy has no shares outstanding.** Neither the legacy nor the UDiFF format does.
- **From early 2024 NSE publishes market cap for every security.** The daily PR zip
  (`nsearchives.nseindia.com/archives/equities/bhavcopy/pr/PR{DDMMYY}.zip`) holds
  `mcap{DDMMYYYY}.csv`, about 3,200 rows a day:
  `Trade Date, Symbol, Series, Security Name, Category, Last Trade Date, Face Value(Rs.),
  Issue Size, Close Price/Paid up value(Rs.), Market Cap(Rs.)`. `Issue Size` is shares
  outstanding. There is no ISIN, so rows join on symbol and series. It is in the zip on
  1 Apr 2024 and not on 1 Jan 2024; the first date is found by the backfill.
- **2010 to 2013, index members only.** The same zip's `Ix` and `ffix` files carry `ISSUE_CAP`
  for about 700 index constituents. They stop in 2014.
- **2014 to early 2024, nothing in bulk.** We checked NSE's PR zips (2015, 2019, 2022), NSE's
  market summary files (totals only), BSE's bhavcopy (`NO_OF_SHRS` is shares traded, not
  outstanding), BSE's scrip master (refuses scripts) and the NSDL, MCA and SEBI sites (no bulk
  history). The only official per-company record is each quarter's shareholding-pattern
  filing, which NSE's API serves from about 2021.
- **The old repos** took today's market cap and scaled it back by price. That leaks today's
  share count into history and jumps on every split.

## 3. Decisions (agreed with Arush, 27 Oct 2026)

1. **From the first mcap file on:** a nightly raw feed of the mcap file. Shares come from
   `Issue Size` directly.
2. **Before it, walk back (option A).** Start from each listing's first `Issue Size` and walk
   backwards through every applied split, bonus, consolidation and rights issue in
   `ref.corporate_action` and `ref.price_adjustment`. For 2010 to 2013, the `Ix`/`ffix`
   `ISSUE_CAP` of index members overrides the walk-back where they differ.
3. **Correct it later with the filings (option B).** Quarterly shareholding filings, from about
   2021, replace the walked-back counts at each quarter end. That catches fresh issues,
   buybacks and QIPs, which aren't corporate actions. It is task 5, after the rest works.
4. Every value records where it came from (`source`), so a reader can tell a reported count
   from an estimate.

## 4. Schema

| Table | One row per | Main columns |
|---|---|---|
| `raw.nse_mcap` | row of a mcap file | `file_id`, `trade_date`, `symbol`, `series`, `category`, `face_value`, `issue_size`, `close`, `market_cap_inr` |
| `raw.nse_index_issue_cap` | row of an `Ix` or `ffix` file, 2010 to 2013 | `file_id`, `trade_date`, `index_name`, `symbol`, `series`, `issue_cap`, `market_cap_inr`, `ff_market_cap_inr` |
| `ref.listing_shares` | period of a listing's share count | `listing_id`, `valid_from`, `valid_to`, `shares`, `source` (`nse_mcap`, `index_file`, `walk_back`, `filing`), `evidence` (jsonb) |
| `clean.market_cap_1d` (view) | listing and session | `listing_id`, `date`, `shares`, `close`, `market_cap_inr`, `source` |

- Share periods follow spec 0002: `valid_from` inclusive, `valid_to` exclusive, non-overlapping
  per listing (the same `EXCLUDE` constraint as the other period tables).
- Market cap is unadjusted close × shares on that day. Both are point in time, so the product
  doesn't need adjusting.

## 5. How it's filled

### 5.1 The mcap feed

A new daily feed, `nse_mcap`, fetches the PR zip after the bhavcopy and keeps only the mcap
file. It follows spec 0001's rules (politeness, receipts, revisions). The backfill walks back
from today until the file is missing for 10 sessions in a row, and records the first date.

### 5.2 Share periods from the feed

Each night, each listing's row maps by symbol and series on that day
(`ref.listing_symbol`). A new period opens whenever `Issue Size` changes.

### 5.3 The walk-back

For each listing, from its first reported count *S* going backwards:

| Action on ex-date *e* | Shares before *e* |
|---|---|
| Split or consolidation *F₁* to *F₂* | *S × F₂ / F₁* |
| Bonus *a*:*b* | *S × b / (a + b)* |
| Rights *a*:*b* | *S × b / (a + b)* |
| Anything else, or a pending adjustment | unchanged; the period is marked in `evidence` |

A listing that stopped trading before the first mcap file has no count to start from. It gets
its last `Ix`/`ffix` count if it was an index member, and none otherwise. Those listings are
counted in a check, not guessed.

### 5.4 Checks

| Check | Severity | Passes when |
|---|---|---|
| `mcap_rows_mapped` | warn | every mcap row in a listing series maps to one listing |
| `shares_present` | warn | every active equity listing that traded has a share count |
| `mcap_matches_nse` | warn | our close × shares is within 1% of NSE's `Market Cap` |
| `walk_back_vs_index` | warn | 2010 to 2013 walk-back counts are within 5% of `ISSUE_CAP` for index members |

## 6. Plan

| # | Task | Output |
|---|---|---|
| 1 | Migration: the tables in section 4, the checks, grants | Tables in `cagr_test` |
| 2 | The mcap feed and its backfill (from the laptop) | `raw.nse_mcap` from its first date |
| 3 | Share periods from the feed, and the walk-back | Every listing with a count since 2010, or listed |
| 4 | The `Ix`/`ffix` files for 2010 to 2013 and the cross-check | `walk_back_vs_index` reported |
| 5 | Quarterly shareholding filings from about 2021 (option B) | Walk-back corrected at quarter ends |

## 7. Open questions

| # | Question | Recommendation |
|---|---|---|
| Q1 | What market-cap floor defines the universe that needs a `cagr_industry`? | A rank: the top 1,000 by market cap on the day. A fixed rupee floor moves with the market, which skews backtests |
| Q2 | Should a listing with no count before 2024 (delisted, never an index member) be left out of the industry indices? | Yes. It is counted by `shares_present`, so we can see how much history it affects |
