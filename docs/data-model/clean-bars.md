# Clean bars and market cap

**In short:** the `clean` schema has one bar per listing per trading day, built from raw. It also
has weekly and monthly bars, adjusted and unadjusted prices, and a daily market cap. **For
research, read `clean`, not `raw`.**

Code: `platform/src/cagr_data/clean/bars.py` and `platform/src/cagr_data/reference/shares.py`.

## Which table to read

| Read | For |
|---|---|
| `clean.bar_1d_adjusted` | Daily prices comparable across splits and bonuses: returns, indicators, backtests |
| `clean.bar_1d_unadjusted` | Daily prices as traded |
| `clean.bar_1w`, `clean.bar_1mo` | Weekly and monthly bars, with both unadjusted and `adj_` columns |
| `clean.market_cap_1d` | Daily shares outstanding and market cap |

All of them are keyed on `listing_id` (see [Listings](listings.md)) and `date`.

## Daily bars: `clean.bar_1d`

- There is one bar per listing per trading day, taken from the raw file that the `_current` views
  pick.
- Stocks come from the listing series (EQ, BE, SM, ...). Days with zero volume are kept.
- Indices are bars too, with `series` set to `''`. Their traded value is converted from crores to
  rupees.
- `prev_close` is kept exactly as NSE printed it.
- Prices are unadjusted decimals. `source_file_id` points to the raw file the bar came from.

## Adjusted prices

`clean.bar_1d_adjusted` multiplies open, high, low and close by the listing's cumulative
adjustment factor for that date, and divides volume by it. The factor is the product of every
applied [price adjustment](listings.md#price-adjustments) with a later ex-date. These factors are
kept as periods in `clean.adjustment_step`.

**Held back while pending:** if a listing has an adjustment waiting for review, its adjusted bars
stop the day before that ex-date. You never get adjusted prices that might be wrong. Once the item
is decided, rebuild and they come back.

## Weekly and monthly bars

- A week or month runs from its first trading day to its last.
- `session_count` says how many trading days it has.
- `complete` is true once the period's last trading day has a bar. The current week is
  incomplete.
- The `adj_` columns hold the adjusted open, high, low, close and volume.

## Shares outstanding and market cap

`ref.listing_shares` holds each listing's share count as periods. The counts come from:

| Source | Covers |
|---|---|
| `nse_mcap`: NSE's mcap file | Every security, from 1 Feb 2024 |
| `index_file`: the Ix and ffix files | Index members, 2010 to 2013 |
| `walk_back`: worked out from corporate actions | Days between reports, including 2014 to Jan 2024 |

On a day without a report, the count is worked out from the nearest report through the listing's
applied corporate actions. Splits, bonuses and consolidations change it by their ratio, and rights
issues as if fully taken up.

`clean.market_cap_1d` is that day's unadjusted close times that day's share count. Both are as of
the same day, so no adjustment is needed. Market cap is in rupees.

## Building it

```sh
uv run python -m cagr_data.clean.bars 2010-01-01 2026-10-26
```

This one command, run after the [listing build](../pipeline/nse-daily.md#after-the-files-build-listings-and-bars),
does all of it in order:

1. daily bars, a year at a time;
2. price adjustments (new events are proposed, see [Listings](listings.md#price-adjustments));
3. adjustment steps;
4. share periods;
5. weekly and monthly bars;
6. the clean and market-cap checks (see [Data quality](../pipeline/data-quality.md)).

Every step deletes and rewrites its rows, so running it again gives the same result.
