# Data quality

**In short:** checks run at three points: when a file is loaded (`raw`), when listings are built
(`ref`), and when bars are built (`clean`). A **critical** failure stops the run. A **warning** is
recorded and the run continues. All results are saved in `ops.dq_result`.

## When a file is loaded (`raw`)

| Check | Level | Passes when |
|---|---|---|
| `file_shape` | critical | The file has the expected columns and every value fits its type |
| `trade_date_matches` | critical | The date inside the file is the day we asked for |
| `no_duplicates` | critical | Each stock and series (or index) appears once |
| `row_count` | critical | The number of price rows is within 10% (or 50 rows) of the median of the last 20 sessions, looking back at most 40 calendar days. With nothing loaded in that window, such as the first day of a backfill chunk, the check is skipped |
| `benchmark_present` | critical | Nifty 50 (under any of its past names) is in the index file |
| `file_arrived` | critical | By 22:30 IST on a trading day, both the price and index files are in |
| `prices_positive` | warning | Open, high, low and close are above 0 when volume is above 0 |
| `ohlc_order` | warning | Low ≤ open, close ≤ high (for series T0, only the open is checked) |
| `isin_present` | warning | Every EQ row has an ISIN (from 22 Jun 2011) |
| `index_conflict` | warning | No index is listed twice with different values. If one is, that index gets no close for the day and the rest of the file loads (CNX Alpha Index on 8 Feb 2013) |
| `holiday_session` | warning | No file arrived on a listed holiday |
| `vendor_revision` | warning | NSE has not changed a file we already had |
| `special_session` | info | No file arrived on an unlisted weekend day |

## When listings are built (`ref`)

| Check | Level | Passes when |
|---|---|---|
| `listing_unique_symbol` | critical | No symbol belongs to two listings on the same day |
| `listing_unique_isin` | critical | No ISIN belongs to two listings on the same day |
| `listing_rows_mapped` | critical | Every price row in a listing series maps to exactly one listing |
| `listing_isin_present` | warning | Every active equity listing has an ISIN (from 22 Jun 2011) |
| `symbol_change_confirmed` | warning | Each symbol change the rules made matches a row in `symbolchange.csv` |
| `master_coverage` | warning | NSE's company and ETF lists and our listings agree |
| `corporate_action_parsed` | warning | Every action with an ex-date in the next 3 sessions was parsed |
| `review_waiting` | warning | No review item has waited more than 3 sessions |

## When bars are built (`clean`)

| Check | Level | Passes when |
|---|---|---|
| `bar_on_session` | critical | Every bar's date is a trading day |
| `bars_complete` | critical | Every raw row in a listing series made exactly one bar |
| `index_benchmark_present` | critical | Nifty 50 has a bar on every trading day since 21 Feb 2012 |
| `period_bars_match` | critical | Each weekly and monthly bar agrees with its daily bars |
| `bar_ohlc_order` | warning | Low ≤ open, close ≤ high on each bar |
| `adjusted_jump` | warning | No adjusted close moves more than 25% in a day without an adjustment |
| `adjustment_explained` | warning | Every price jump has an adjustment, applied or waiting for review |
| `mcap_rows_mapped` | warning | Every row of NSE's mcap file maps to a listing |
| `shares_present` | warning | Every equity listing that traded has a share count |
| `mcap_matches_nse` | warning | Our market cap is within 1% of NSE's |
| `walk_back_vs_index` | warning | Share counts worked out from corporate actions are within 5% of NSE's 2010 to 2013 counts |

Two industry checks are defined but not run by any command yet: `industry_known` (every
listing's industry is an active one) and `universe_classified` (every stock ever in the universe
has an industry).

## Changing a threshold

Thresholds are stored in `ops.dq_check.params`, not in code. To change one, write a new migration
that updates that row, as `0006_ohlc_t0.sql` does.

## Seeing failures

```sql
SELECT partition_date, check_id, observed
  FROM ops.dq_result
 WHERE NOT passed
 ORDER BY partition_date DESC
 LIMIT 50;
```

`observed` shows the evidence: counts, the threshold used, and up to 10 example rows.
