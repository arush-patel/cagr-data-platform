# Trading calendar

**In short:** the table `ref.calendar_session` has one row for every day from 1 Jan 2010 to the end of
next year. It says whether NSE traded that day and, if so, gives the session a running number.

## How a day is decided

The first rule that matches wins:

1. **Special session**, so a trading day. This covers Muhurat trading on Diwali and Saturday or
   Sunday sessions such as Budget day.
2. **Listed holiday**, so not a trading day.
3. **Saturday or Sunday**, so not a trading day.
4. **Anything else** is a regular trading day.

## Where holidays and special sessions come from

| Source | Covers |
|---|---|
| NSE's holiday list (CM segment) | Holidays from 2011 onward. Entries named "Special Live Trading" are trading days, so they are skipped. |
| Which days had a price file (2010 only) | A weekday with no file is a holiday, and a weekend day with a file is a special session. |
| A price or index file arriving on a weekend or a listed holiday | That day is recorded as a special session. |

These facts are kept in `ref.calendar_exception`. They are only ever added, never changed.

## Columns

| Column | Meaning |
|---|---|
| `date` | The calendar day |
| `is_session` | Did NSE trade? |
| `session_index` | 1, 2, 3, ... counting trading days from 1 Jan 2010, with no gaps. Empty on non-trading days. |
| `reason` | `regular`, `weekend`, `holiday` or `special_session` |
| `is_week_end`, `is_month_end` | Last trading day of the week or month |

The running number lets you count trading days by subtracting: "20 sessions later" is
`session_index + 20`.

## Example: June 2024

| Date | Day | Trading? | Reason | Week end? | Month end? |
|---|---|---|---|---|---|
| 14 Jun | Fri | yes | regular | yes | |
| 15 Jun | Sat | no | weekend | | |
| 16 Jun | Sun | no | weekend | | |
| 17 Jun | Mon | no | holiday | | |
| 18 Jun | Tue | yes | regular | | |
| 28 Jun | Fri | yes | regular | yes | yes |

## When it is rebuilt

The whole table is rebuilt every Monday at 07:00 IST, and once in every backfill. It is always
rebuilt in full, because a new holiday early on shifts every session number after it.

In Python, the toolkit's `Calendar` reads this table (see [Toolkit](../toolkit/README.md#cagrcalendar)).
