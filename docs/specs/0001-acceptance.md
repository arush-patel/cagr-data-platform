# Spec 0001 acceptance in cagr_test

Results of the hand-run acceptance steps in section 10 of
[0001-nse-daily-market-data.md](0001-nse-daily-market-data.md), run on Arush's laptop against
`cagr_test` as `cagr_writer`.

## 1. Trial backfill, Jun to Aug 2024 (27 Oct 2026)

`backfill 2024-06-01 2024-08-31`, at 1 request a second. The first run loaded 1 to 26 Jun and
then failed every remaining day when the database tunnel dropped. The rerun skipped the stored
days and finished in 15 minutes with `92 files: ok, 92 file_arrived: ok, holiday lists: ok,
calendar: ok, 0 failure(s)`. The backfill now waits for the database to come back instead.

Files received:

| Feed | Files | Rows | First | Last |
|---|---|---|---|---|
| nse_cm_bhavcopy_legacy | 24 | 66,632 | 2024-06-03 | 2024-07-05 |
| nse_cm_bhavcopy_udiff | 62 | 173,285 | 2024-06-03 | 2024-08-30 |
| nse_index_close | 62 | 7,330 | 2024-06-03 | 2024-08-30 |
| nse_holidays | 17 | 3,404 | 2011 | 2027 |

- Requests: 165 returned 200 and 98 returned 404 (weekends, holidays, and legacy after 5 Jul).
  There were no other statuses.
- Calendar: 62 sessions. The weekday holidays 17 Jun, 17 Jul and 15 Aug are not sessions.
- Current view: 173,285 bars. It takes legacy up to 5 Jul (legacy has priority) and UDiFF after.
- Raw files: 165, 14 MB.

Legacy vs UDiFF over the 24 shared sessions (1 Jun to 5 Jul): 66,632 rows on each side and the
same keys. Close, volume, previous close, open, high, low, traded value and trade count are
equal on every row. Two kinds of difference:

- `last` on 46 bond rows (series NT, YT, AT, ZT): legacy writes 0 where UDiFF is empty. Since
  this run, a `last` of 0 is stored as null.
- ISIN of KAMOPAINTS EQ on 14 Jun 2024: INE0BTI01029 in legacy and INE0BTI01037 in UDiFF. This
  is left for spec 0002's ISIN mapping.

Checks: all passed except `ohlc_order` (warn), which flagged 6 rows, all in series T0 (NSE's
same-day settlement segment): BANKBARODA 3 Jun, ONGC 14 Jun, ASHOKLEY 28 Jun, MOTHERSON 1 Jul,
VEDL 9 Jul, SBIN 19 Aug. In T0 the close is the normal segment's closing price. For example
BANKBARODA had open, high and low of 289.75 and a close of 296.95. Migration 0006 now checks only
the open for T0.

## 2. Backfill, 1 Jan 2024 to 26 Oct 2026 (27 Oct 2026)

This ran in three year chunks from 13:08 to 15:19 IST and every chunk exited 0. There were no
waits for the database and no fetch errors. It wrote 1,543 files (142 MB) and no file has a
second revision.

| Year | Sessions | Weekend sessions | Bars (current view) | Index-close rows |
|---|---|---|---|---|
| 2024 | 249 | 20 Jan, 2 Mar, 18 May | 693,138 | 29,080 |
| 2025 | 249 | 1 Feb (Budget) | 763,792 | 34,648 |
| 2026 to 26 Oct | 201 | 1 Feb (Budget) | 691,207 | 30,946 |

Every session has a UDiFF file and index-close file, and no file fell on a non-session.

Warnings:

- `ohlc_order`: 10. These are the T0 rows from step 1, checked before migration 0006. There
  are none after it.
- `special_session` (info): 10, for the four weekend sessions, one result per feed.
- `holiday_session`: 5.
  - Sat 2 Mar 2024 is listed as "Special Live Trading". Since this run that entry is no longer
    read as a holiday.
  - Tue 21 Oct 2025 is Diwali Muhurat trading. This is expected once a year (section 7.2) and
    needs no code change. NSE leaves `morning_session` and `evening_session` empty even for
    Muhurat, so the list can't tell us about the session ahead of time.

In every case the calendar marks the day as a session.

## 3. Full history, 4 Jan 2010 to 27 Oct 2026, and the builds on top (27 Oct 2026)

The backfill ran as four parallel processes at 2 requests a second from 21:40 to 22:45 IST. NSE
returned 9,741 × 200 and 2,478 × 404, with no 403 or 429. Seven NSE format quirks surfaced
mid-run and were fixed (PRs #40 to #47). The 17 failed days were rerun one at a time. 15 of
them now pass, and the other two are NSE gaps (below). The calendar, listings, corporate
actions, industry seed (spec 0006), clean bars (spec 0003) and the monthly raw archive followed.
Three more fixes landed on the way: #48 (1 Jan 2010), #50 (a misread dividend) and #51 (weekly
and monthly bars, from 23 minutes a year to about 10 seconds).

Raw layer:

| Feed | Files | Rows | Range |
|---|---|---|---|
| nse_cm_bhavcopy_legacy | 3,602 | 6,567,984 | 2010-01-04 to 2024-07-05 |
| nse_cm_bhavcopy_udiff | 700 | 2,151,807 | 2024-01-01 to 2026-10-27 |
| nse_index_close | 3,626 | 297,731 | 2012-02-21 to 2026-10-27 |
| nse_index_issue_cap | 1,002 | 820,022 | 2010 to 2013 |
| nse_mcap | 678 | 1,792,538 | 2024-02-01 to 2026-10-27 |
| nse_corporate_actions (+ SME) | 36 | 37,094 | 2010 to 2027 |
| nse_holidays | 17 | 3,404 | 2011 to 2027 |

`raw.nse_cm_bar` holds 8,719,791 rows over 4,174 sessions. The archive packed 9,607 files
(1.06 GB) into 514 feed-months. The 20 files of the last 7 days stay loose.

Calendar: 4,218 sessions in 2010 to 2026, between 244 and 253 a year. 1 Jan 2010 has no
bhavcopy, so it is a holiday by archive proof (#48).

Reference layer:

- Listings: 4,811, with 5,342 symbol periods and 5,097 ISIN periods. Listing changes: 4,811
  new listings, 529 symbol changes and 2,106 ISIN changes applied, plus 2 index renames. 129
  index renames and 13 merges are pending review.
- Corporate actions: 45,918. 224 are unparsed: 85 dividends, 108 others, 19 rights and 12
  splits.
- Industries (cagr_industry): 1,604 listings. The seed's 1,394 coarse values are not loaded,
  and 42 seed symbols have no NSE listing.
- Share periods: 8,678 from nse_mcap, 3,508 from the index files and 2,851 walked back.

Price adjustments: 5,193 applied and 4,043 pending, with no duplicates.

| Status | Note | Rows |
|---|---|---|
| applied | extraordinary dividend from the parsed amount | 3,577 |
| applied | confirmed by the close | 1,275 |
| applied | rights from the parsed amount | 304 |
| applied | clean-ratio jump with an ISIN change and no action, applied from history (Q5) | 37 |
| pending | price jump, near a clean ratio | 2,178 |
| pending | price jump, no action explains it | 1,559 |
| pending | demerger: confirm the factor from prices | 193 |
| pending | an action on this date is unparsed | 110 |
| pending | the close disagrees with the action | 3 |

Clean layer: `bar_1d` 8,039,726, `bar_1w` 1,745,324, `bar_1mo` 407,894, `market_cap_1d`
6,725,801 and `adjustment_step` 5,193. The clean checks took 8.5 minutes.

Passed: `bar_on_session`, `bars_complete`, `bar_ohlc_order`, `mcap_matches_nse` and
`period_bars_match`.

Open results:

- `index_benchmark_present` (critical), 13, and `file_arrived`, 14. NSE's archive has no
  index-close file for 2012-11-11 (muhurat), 2013-10-09, 2014-03-19, 2014-12-15, 2015-02-02,
  2015-03-12, 2015-03-13, 2015-05-19, 2015-07-08, 2015-09-04, 2015-10-16, 2015-12-01 and
  2016-06-20. They need another source or accepting as gaps.
- `mcap_rows_mapped`, 2,079: market-cap rows in series IV and BE (InvITs, REITs) have no
  listing.
- `shares_present` 575, `walk_back_vs_index` 483, `adjusted_jump` 258 and
  `adjustment_explained` 32. Most follow from the pending adjustments and the walked-back
  share counts.
- `row_count`: 2022-10-03 (2,616 rows against a median of about 2,272; the data is stored) and
  the small weekend and holiday-list sessions.
- `holiday_session` 12 and `special_session` 11: weekend and muhurat sessions on dates NSE's
  list calls holidays (2012-01-07, 2012-03-03, 2012-04-28, 2012-09-08, 2012-11-11, 2013-05-11
  and others). The calendar marks them as sessions.
- `index_conflict`: 2013-02-08, where CNX Alpha Index is listed twice with different values
  and is left out for that day (#47).
- `ohlc_order` 6: the 2024 T0 rows from step 1, checked before migration 0006.
