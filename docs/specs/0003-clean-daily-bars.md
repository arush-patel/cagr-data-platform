# Spec 0003: clean daily bars

| | |
|---|---|
| Status | Agreed. Arush answered the open questions on 27 Oct 2026 (section 9). |
| Date | 2026-10-27 |
| Owner | Arush |
| Component | `platform` (`cagr_data.clean`), version 0.3.0 |
| Database | `cagr_test` |
| Depends on | Spec 0001 (raw bars and index closes, calendar) and spec 0002 (listings, adjustment factors) |
| Next | The lake export of bars (its own spec), then the toolkit's slice T2 and the feature engine |

## 1. Goal

One daily bar per listing per session it traded, keyed on `listing_id`, in two forms:

- **unadjusted:** exactly what NSE printed;
- **adjusted:** comparable across splits, bonuses, rights issues and demergers, with the factor
  that was applied, so a reader can undo it.

This is what every feature, backtest and chart reads. Once it's done, a feature written in the
lab sees the same prices as the nightly engine and the Observatory.

## 2. Scope

### In scope

- `clean.bar_1d` for equities, ETFs, REITs and InvITs (from the bhavcopy) and for indices
  (from `ind_close_all`), since 2010.
- Adjusted and unadjusted views over it, with the cumulative factor.
- Stored weekly and monthly bars, `clean.bar_1w` and `clean.bar_1mo` (Q2, section 5.1).
- The `clean`-layer checks.
- A nightly build after the listings and adjustments of spec 0002, plus a full rebuild command.

### Out of scope

- Total-return prices. Only extraordinary dividends adjust prices (spec 0002, Q3).
- The Yahoo cross-check (D2) and index history before NSE's archive (21 Feb 2012). Both are
  later specs.
- The lake export (`bars/1d/venue=XNSE/year=YYYY/*.parquet`), which gets its own spec.

## 3. What a bar is

**A bar is one listing's open, high, low, close and volume on one XNSE session on which it
traded.**

- **Which bhavcopy row.** A listing's row that day is the row in its listing series (EQ, BE, BZ,
  SM, ST, SZ, or the ETF, REIT or InvIT series). T0 rows are never a bar. A symbol is never in
  two of these series on the same day (checked over 2024 to 2026), so there is at most one row.
  The bar records which series it came from.
- **Zero-volume rows are bars (Q1).** NSE prints a row for a listed stock that didn't trade,
  with volume 0 and its prices at the previous close. That row is kept as a bar, so a reader can
  tell "listed but not traded" (volume 0) from "not in the file" (no bar).
- **No bar when there's no row.** Nothing is carried forward. The toolkit gets sessions from
  `ref.calendar_session` and sees the gap.
- **Only XNSE sessions.** That includes special sessions such as Saturday Budget days and Muhurat
  trading. A bar on a day the calendar doesn't call a session is a critical failure.
- **Index bars** come from `ind_close_all`. Some indices have no open, high or low on some days.
  Those stay null, and the close is always set. Indices are never adjusted.

## 4. Adjustment

**Adjusted price = unadjusted price × the product of every applied factor with an ex-date after
the bar's date. Adjusted volume = unadjusted volume ÷ the same product.** The factors come from
`ref.price_adjustment` (spec 0002).

- Unadjusted prices are stored once and never change, unless a raw revision does. Applying a
  new corporate action doesn't rewrite any bar. It adds one step to a small factor table,
  `clean.adjustment_step` (`listing_id`, `valid_from`, `valid_to`, `cum_factor`), with one row
  per listing per period between ex-dates. The adjusted view joins bars to it.
- **A pending adjustment stops the listing's adjusted history.** While an adjustment event is
  pending in spec 0002, the adjusted view shows that listing's bars only up to the session before
  the ex-date. A feature on it then stops for a few days instead of seeing a false 50% drop.
  Unadjusted bars are always complete. The view `clean.adjustment_hold` (`listing_id`,
  `held_from`, `adjustment_id`) lists every listing whose adjusted bars are held this way. That
  tells a reader "held for review" apart from "didn't trade", and tells the feature engine
  which listings to recompute once a hold is released.
- **Look-ahead.** Adjusted history uses every factor known today, as every vendor's adjusted
  series does. Price ratios such as returns are unaffected. A backtest that needs prices exactly
  as known on a past date reads the unadjusted view.

## 5. Schema

| Object | Kind | Columns |
|---|---|---|
| `clean.bar_1d` | table, partitioned by year like `raw.nse_cm_bar` | `listing_id`, `date`, `open`, `high`, `low`, `close`, `volume`, `value_traded`, `trade_count`, `prev_close` (as NSE printed it), `series`, `source_file_id`, `built_at`. Primary key `(listing_id, date)` |
| `clean.adjustment_step` | table | `listing_id`, `valid_from`, `valid_to`, `cum_factor` |
| `clean.bar_1d_unadjusted` | view | `listing_id`, `date`, `open`, `high`, `low`, `close`, `volume`, `value_traded`, `trade_count`, `series` |
| `clean.bar_1d_adjusted` | view | the same column names with adjusted prices and volume, plus `adj_factor` |
| `clean.adjustment_hold` | view | `listing_id`, `held_from` (the pending ex-date), `adjustment_id` |

- Unadjusted prices are `numeric`, exactly as in raw. Adjusted prices and `adj_factor` are
  `double precision`.
- `value_traded` is in rupees. Spec 0001 keeps index turnover in crores, as sent, and this
  converts it.
- `clean` is derived. Unlike raw it's rebuilt freely: a day's bars are deleted and inserted again
  in one transaction, so a rerun leaves the same rows.
- `cagr_writer` writes `clean`. `cagr_read` can select from it.

### 5.1 Weekly and monthly bars

`clean.bar_1w` and `clean.bar_1mo` have one row per listing per week or month in which it has a
daily bar. Weeks and months follow the calendar's `is_week_end` and `is_month_end` flags, so a
holiday Friday ends the week on Thursday.

| Column | Meaning |
|---|---|
| `listing_id`, `period_start`, `period_end` | the period's first and last XNSE session. Primary key `(listing_id, period_start)` |
| `open`, `high`, `low`, `close`, `volume`, `value_traded`, `trade_count`, `session_count` | unadjusted: first open, highest high, lowest low, last close, sums, number of daily bars |
| `adj_open`, `adj_high`, `adj_low`, `adj_close`, `adj_volume` | the same built from adjusted daily bars |
| `complete` | false for the current week or month until its last session |
| `adjustment_state` | the highest applied `adjustment_id` when the row was built |

An adjusted period bar can't be a stored bar times one factor, because an ex-date can fall
inside the week. So both forms are stored, and a new adjustment rebuilds every period bar of
that listing. A period that has a held daily bar (section 4) gets null adjusted columns.

## 6. How it's built

After spec 0002's listing and adjustment steps for session *d*:

1. Read *d*'s current raw rows (`raw.nse_cm_bar_current`, `raw.nse_index_close_current`).
2. Map each row to its listing with `ref.listing_symbol` and `ref.listing_isin` as of *d*. A row
   in a listing series that doesn't map is a critical failure (it means 0002's step missed it).
3. Pick one row per listing (section 3) and replace *d*'s bars.
4. Rebuild `clean.adjustment_step` for listings whose adjustments changed today.
5. Rebuild the week and month that contain *d*, and every weekly and monthly bar of a listing
   whose adjustments changed today.
6. Run the checks.

The full rebuild (`python -m cagr_data.clean.rebuild 2010-01-01 2026-10-26`) runs the same steps
for every session. It's also what a raw revision or a listing merge triggers, for the listings
and dates they touch.

## 7. Checks (`clean` layer)

| Check | Severity | Passes when |
|---|---|---|
| `bar_on_session` | critical | every bar's date is an XNSE session |
| `bars_complete` | critical | every raw row in a listing series produced exactly one bar |
| `period_bars_match` | critical | each weekly and monthly bar agrees with its daily bars (open, high, low, close, volume, count) |
| `bar_ohlc_order` | warn | low ≤ open, close ≤ high (the raw `ohlc_order` check, now per listing) |
| `adjusted_jump` | warn | no adjusted close moves more than 25% in one session without an adjustment event. Circuit limits are 20% at most, so a bigger move usually means a missed action. Re-listings and first days are exempt |
| `index_benchmark_present` | critical | Nifty 50 has a bar on every session since 21 Feb 2012 |

## 8. Plan

| # | Task | Output |
|---|---|---|
| 1 | Migration: `clean` schema, tables, views, grants, checks | Objects in `cagr_test` |
| 2 | Builder and full rebuild, daily then weekly and monthly | Bars for all history; the checks pass or every warning is explained |
| 3 | Dagster assets after spec 0002's, and the nightly run | Bars current every evening |
| 4 | Toolkit read check with the toolkit thread | `cagr.data` reads adjusted and unadjusted bars for any listing and date |

Acceptance in `cagr_test`:

1. Every listing-series row since 2010 has one bar, and no bar is on a non-session.
2. RELIANCE's adjusted close is continuous across 28 Oct 2024 (1:1 bonus), and its unadjusted
   close halves.
3. Adjusted prices before a split match the unadjusted price × factor exactly for a sample of
   20 actions.
4. Rebuilding any range gives the same rows.

## 9. Open questions

| # | Question | Recommendation |
|---|---|---|
| Q1 | Should a zero-volume row be a bar? | **Decided:** yes, zero-volume candles are kept |
| Q2 | Should weekly and monthly bars be stored? | **Decided:** yes, both are stored (section 5.1) |
| Q3 | While an adjustment is pending, should the adjusted view stop at the ex-date (section 4)? | **Agreed** |
