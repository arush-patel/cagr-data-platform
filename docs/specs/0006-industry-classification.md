# Spec 0006: industry classification (group_3) and index tiers

| | |
|---|---|
| Status | Draft for review. Open questions are in section 8. |
| Date | 2026-10-27 |
| Owner | Arush |
| Component | `platform` (`cagr_data.reference`), version 0.4.0 |
| Database | `cagr_test` |
| Depends on | Spec 0002 (listings, the review queue) and spec 0005 (the Observatory review page) |
| Next | Market cap (D5), then the 52 custom industry indices as a nightly point-in-time feature |

## 1. Goal

Every listing in the tradable universe has Arush's industry, **group_3**, dated so that a
backtest sees the industry a stock had at the time. Every listing also has NSE's index tier
(Nifty 50, Next 50, Midcap 150, Smallcap 250, Microcap 250 or none).

group_3 is what the 52 custom industry indices and the industry features are built on.

## 2. What we found

- **group_3 is Arush's own taxonomy.** It has 52 values, such as `Private Bank`,
  `Auto Ancillaries I` and `Defence`. It lives only in `script_master.group_3` in the old
  `stockdb`, is keyed by symbol, holds only today's value and is edited by hand.
- **NSE can't give us group_3.** NSE's four-level industry sits behind an API that blocks
  scripts. The only bulk industry NSE publishes is a 22-value sector in the index constituent
  files, and only for the 755 stocks in an index. One NSE sector ("Capital Goods") spans 17
  group_3 values, so group_3 can't be derived from it.
- **The tier comes from NSE.** NSE publishes today's constituents of each tier index with ISINs
  (`ind_nifty50list.csv` and the others). The old `group_1` agrees with them for only 783 of
  1,191 stocks, so it is out of date and isn't carried over.
- **`group_2`** (the Nifty sector index for each industry) follows from group_3 through a fixed
  52-row map, so it is a lookup table, not a column per stock.

## 3. Decisions (agreed with Arush, 27 Oct 2026)

1. Seed group_3 once from the old master, by one read-only query of `script_master` (symbol and
   group_3 only). The result is committed as a CSV in the repo, so the starting point is on
   record. Nothing is written to `stockdb`.
2. A stock in the tradable universe without a group_3 goes on the Observatory review page, sorted
   by market cap, with NSE's sector shown as a hint. Arush picks its group_3 there.
3. Until it has one, the stock has no group_3 and is left out of the industry indices. Stocks
   below the universe floor don't need one.
4. The tier comes from NSE's constituent files, by ISIN.

## 4. Schema

| Table | One row per | Main columns |
|---|---|---|
| `ref.industry` | group_3 value | `industry_id`, `name` (the group_3 text), `sector_index` (group_2, such as `Nifty Private Bank`), `active` |
| `ref.listing_industry` | industry period of a listing | `listing_id`, `industry_id`, `valid_from`, `valid_to`, `change_id` |
| `ref.industry_change` | proposed or decided assignment | `change_id`, `listing_id`, `industry_id`, `effective_date`, `status` (`applied`, `pending`, `rejected`), `proposed_by`, `decided_by`, `decided_at`, `note`, `evidence` (jsonb: NSE sector, market cap, source) |
| `raw.nse_index_constituent` | row of a constituent file | `file_id`, `index_name`, `company`, `industry` (NSE's sector), `symbol`, `series`, `isin` |
| `ref.listing_tier` | tier period of a listing | `listing_id`, `tier`, `valid_from`, `valid_to` |

- Periods follow the rule from spec 0002: `valid_from` inclusive, `valid_to` exclusive and null
  while current. One industry and one tier per listing at a time (the same `EXCLUDE` as spec
  0002).
- `decided_by <> proposed_by` (D16), as for listing changes.
- Read interface: `ref.listing_industry_as_of(d)` returns `listing_id`, `industry`,
  `sector_index` and `tier` for every listing on *d*.

## 5. How it's filled

### 5.1 The seed

1. On the laptop, read `script, group_3` from `script_master` once, read-only.
2. Commit it as `db/seeds/group3_2026-10-27.csv`.
3. A loader matches each symbol to a listing with `ref.listing_by_symbol(symbol, 2026-10-27)`.
   Every match becomes an applied `industry_change` with `proposed_by = 'seed'`, dated from the
   listing's first session. The seed holds only today's value, so history uses today's
   industry until a change is recorded. This is a known limit, not look-ahead in prices.
4. Symbols with no listing are reported, not guessed.

### 5.2 New and unmapped stocks

Each night, after the listing build:

- Every active equity listing in the universe (market cap above the floor, once D5 exists; until
  then, every active main-board equity) with no current industry gets one pending
  `industry_change` with no `industry_id`. Its evidence holds NSE's sector, if the stock is in a
  constituent file.
- The review page lists them, largest first. Approving one means picking a group_3; the page
  writes the `industry_id` and the decision together.
- A change of industry for a stock that already has one is proposed the same way, by a person,
  and dated from the day it takes effect.

### 5.3 Tiers

A new daily snapshot feed fetches the five tier files. Each night, a listing's tier is the tier
whose file holds its ISIN. A change opens a new period from that day. History before the first
snapshot needs NSE's semi-annual rebalance notices, which is its own task (section 7).

## 6. Checks (`ref` layer)

| Check | Severity | Passes when |
|---|---|---|
| `industry_one_at_a_time` | critical | no listing has two industries on the same day (also enforced by the table) |
| `industry_known` | critical | every assignment points at an active `ref.industry` row |
| `universe_classified` | warn | every listing in the universe has an industry, or a pending item younger than 3 sessions |
| `tier_files_complete` | warn | the five tier files have 50, 50, 150, 250 and 250 rows (give or take a few) |

## 7. Plan

| # | Task | Output |
|---|---|---|
| 1 | Migration: the tables in section 4, the 52 industries with their sector index, grants | Tables in `cagr_test` |
| 2 | The seed: the read-only query on the laptop, the CSV, the loader | Every seeded symbol mapped, or listed |
| 3 | The tier feed and tier periods | Every index stock has its tier from today |
| 4 | Pending items for unmapped stocks, and the review page's industry picker (with the Observatory thread) | Arush can classify a new stock in one click |
| 5 | Tier history from the rebalance notices | Tiers point in time since 2012 |

## 8. Open questions

| # | Question | Recommendation |
|---|---|---|
| Q1 | Should the coworker's 26 coarser groups (Automobile, Pharma, Metal and so on) be a second level above group_3? | Yes, as a `parent` column on `ref.industry`, once Arush confirms the mapping |
| Q2 | Until market cap exists, which stocks need a group_3? | Every active main-board equity that was in `script_master` or trades above Rs 1 crore a day on average. It's replaced by the market-cap floor once D5 lands |
| Q3 | Should a group_3 change be backdated? | No. A change takes effect from the day it's approved, so history never moves. A wrong seed value is the exception: it's corrected from the listing's first session |
