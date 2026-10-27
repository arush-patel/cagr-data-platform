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
