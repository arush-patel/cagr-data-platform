# Spec 0006: industry classification (cagr_industry) and index tiers

| | |
|---|---|
| Status | Agreed 27 Oct 2026, waiting on the seed export |
| Date | 2026-10-27 |
| Owner | Arush |
| Component | `platform` (`cagr_data.reference`), version 0.4.0 |
| Database | `cagr_test` |
| Depends on | Spec 0002 (listings, the review queue) and spec 0005 (the Observatory review page) |
| Next | Spec 0007 (daily market cap, D5), then the 52 custom industry indices as a nightly feature |

## 1. Goal

Every listing in the tradable universe has Arush's industry, **`cagr_industry`** (called
`group_3` in the old `stockdb`). Every listing also has NSE's index tier
(Nifty 50, Next 50, Midcap 150, Smallcap 250, Microcap 250 or none).

`cagr_industry` is what the 52 custom industry indices and the industry features are built on.

## 2. What we found

- **`cagr_industry` (the old `group_3`) is Arush's own taxonomy.** It has 52 values, such as
  `Private Bank`, `Auto Ancillaries I` and `Defence`. It lives only in `script_master.group_3` in the old
  `stockdb`, is keyed by symbol, holds only today's value and is edited by hand.
- **NSE can't give us `cagr_industry`.** NSE's four-level industry sits behind an API that blocks
  scripts. The only bulk industry NSE publishes is a 22-value sector in the index constituent
  files, and only for the 755 stocks in an index. One NSE sector ("Capital Goods") spans 17
  of its values, so it can't be derived from NSE.
- **The tier comes from NSE.** NSE publishes today's constituents of each tier index with ISINs
  (`ind_nifty50list.csv` and the others). The old `group_1` agrees with them for only 783 of
  1,191 stocks, so it is out of date and isn't carried over.
- **`group_2`** (the Nifty sector index for each industry) follows from the industry through a
  fixed 52-row map (`config/industry_index_map.json` in the old repo), so it is a column of
  `ref.industry`, not a column per stock.

## 3. Decisions (agreed with Arush, 27 Oct 2026)

1. Seed `cagr_industry` once from the old master, by one read-only query of `script_master`
   (symbol and group_3 only), run by Arush on the laptop. The result is committed as a CSV in
   the repo, so the starting point is on
   record. Nothing is written to `stockdb`.
2. A stock in the tradable universe without an industry goes on the Observatory review page,
   sorted by market cap, with NSE's sector shown as a hint. Arush picks its industry there.
3. Until it has one, the stock has no industry and is left out of the industry indices. Stocks
   that were never above the universe floor (₹1,500 crore, spec 0007) don't need one.
4. The tier comes from NSE's constituent files, by ISIN.
5. **One level only.** The coworker's 26 coarser groups are dropped.
6. **A change restates history.** An industry change applies from the listing's first session,
   so history always shows the current classification. Each change is kept in
   `ref.industry_change`, so what a past backtest used can still be recovered.
7. **The universe needs daily market cap.** Which stocks need an industry, and the industry
   indices themselves, depend on it, so market cap (D5) is the next spec.

## 4. Schema

| Table | One row per | Main columns |
|---|---|---|
| `ref.industry` | `cagr_industry` value | `industry_id`, `name` (such as `Private Bank`), `sector_index` (the old group_2, such as `Nifty Private Bank`), `active` |
| `ref.listing_industry` | listing | `listing_id`, `industry_id`, `change_id` |
| `ref.industry_change` | proposed or decided assignment | `change_id`, `listing_id`, `industry_id`, `effective_date`, `status` (`applied`, `pending`, `rejected`), `proposed_by`, `decided_by`, `decided_at`, `note`, `evidence` (jsonb: NSE sector, market cap, source) |
| `raw.nse_index_constituent` | row of a constituent file | `file_id`, `index_name`, `company`, `industry` (NSE's sector), `symbol`, `series`, `isin` |
| `ref.listing_tier` | tier period of a listing | `listing_id`, `tier`, `valid_from`, `valid_to` |

- Tier periods follow the rule from spec 0002: `valid_from` inclusive, `valid_to` exclusive and
  null while current. A listing's industry has a single current row (decision 6), so
  `ref.listing_industry` keeps one row per listing; earlier values live in the change log.
- `decided_by <> proposed_by` (D16), as for listing changes.
- Read interface: `ref.listing_classification(d)` returns `listing_id`, `cagr_industry`,
  `sector_index` and the tier on *d* for every listing.

## 5. How it's filled

### 5.1 The seed

1. On the laptop, read `script, group_3` from `script_master` once, read-only.
2. Commit it as `db/seeds/group3_2026-10-27.csv`.
3. A loader matches each symbol to a listing with `ref.listing_by_symbol(symbol, 2026-10-27)`.
   Every match becomes an applied `industry_change` with `proposed_by = 'seed'`.
4. Symbols with no listing are reported, not guessed.

### 5.2 New and unmapped stocks

Each night, after the listing build:

- Every equity listing that has been in the universe on any day since 2010 (₹1,500 crore or
  more, spec 0007), delisted ones included, with no industry
  gets one pending `industry_change` with no `industry_id`. Its evidence holds NSE's sector, if the stock is in a
  constituent file.
- The review page lists them, largest first. Approving one means picking an industry; the page
  writes the `industry_id` and the decision together.
- A change of industry for a stock that already has one is proposed the same way, by a person.
  Once approved it replaces the old one for all of history (decision 6), and the features and
  industry indices that use it are rebuilt for that listing.

### 5.3 Tiers

A new daily snapshot feed fetches the five tier files. Each night, a listing's tier is the tier
whose file holds its ISIN. A change opens a new period from that day. History before the first
snapshot needs NSE's semi-annual rebalance notices, which is its own task (section 7).

## 6. Checks (`ref` layer)

| Check | Severity | Passes when |
|---|---|---|
| `industry_known` | critical | every assignment points at an active `ref.industry` row |
| `universe_classified` | warn | every listing in the universe has an industry, or a pending item younger than 3 sessions |
| `tier_files_complete` | warn | the five tier files have 50, 50, 150, 250 and 250 rows (give or take a few) |

## 7. Plan

| # | Task | Output |
|---|---|---|
| 1 | Migration: the tables in section 4, the 52 industries with their sector index, grants | Tables in `cagr_test` |
| 2 | The seed: the read-only query on the laptop, the CSV, the loader | Every seeded symbol mapped, or listed |
| 3 | The tier feed and tier periods | Every index stock has its tier from today |
| 4 | Pending items for unmapped stocks, and the review page's industry picker (with the Observatory thread). Needs market cap (spec 0007) for the universe | Arush can classify a new stock in one click |
| 5 | Tier history from the rebalance notices | Tiers point in time since 2012 |

## 8. Open questions

None. Arush answered Q1 to Q3 on 27 Oct 2026: no coarser level, the universe follows daily
market cap (spec 0007), and an industry change restates history (section 3).
