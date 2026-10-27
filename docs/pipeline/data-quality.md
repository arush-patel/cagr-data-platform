# Data quality

**In short:** every file is checked when it is loaded. A **critical** failure stops the run. A
**warning** is recorded and the run continues. All results are saved in `ops.dq_result`.

## The checks

| Check | Level | Passes when |
|---|---|---|
| `file_shape` | critical | The file has the expected columns and every value fits its type |
| `trade_date_matches` | critical | The date inside the file is the day we asked for |
| `no_duplicates` | critical | Each stock and series (or index) appears once |
| `row_count` | critical | The number of price rows is within 10% (or 50 rows) of the median of the last 20 days |
| `benchmark_present` | critical | Nifty 50 (under any of its past names) is in the index file |
| `file_arrived` | critical | By 22:30 IST on a trading day, both the price and index files are in |
| `prices_positive` | warning | Open, high, low and close are above 0 when volume is above 0 |
| `ohlc_order` | warning | Low ≤ open, close ≤ high (for series T0, only the open is checked) |
| `isin_present` | warning | Every EQ row has an ISIN (from 22 Jun 2011) |
| `holiday_session` | warning | No file arrived on a listed holiday |
| `vendor_revision` | warning | NSE has not changed a file we already had |
| `special_session` | info | No file arrived on an unlisted weekend day |

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
