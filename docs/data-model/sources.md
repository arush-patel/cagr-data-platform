# Data sources

**In short:** we download eleven kinds of file from NSE. Some are published every trading day:
stock prices, index closes and market cap. Others are daily or yearly lists: holidays, companies,
ETFs, symbol changes and corporate actions.

## The files

| What | NSE file | Available from | Lands in |
|---|---|---|---|
| Stock prices, old format | `cm{DD}{MON}{YYYY}bhav.csv.zip` | 2010 to 5 Jul 2024 | `raw.nse_cm_bar` |
| Stock prices, new format (UDiFF) | `BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip` | 1 Jan 2024 | `raw.nse_cm_bar` |
| Index closes | `ind_close_all_{DDMMYYYY}.csv` | 21 Feb 2012 | `raw.nse_index_close` |
| Market cap and shares outstanding | `mcap` file inside `PR{DDMMYY}.zip` | 1 Feb 2024 | `raw.nse_mcap` |
| Shares of index members | `Ix` and `ffix` files inside `PR{DDMMYY}.zip` | 2010 to 2013 | `raw.nse_index_issue_cap` |
| Holiday list | holiday-master API, one per year | 2011 | `raw.nse_holiday` |
| Corporate actions, main board and SME | corporate-actions API, one response per year of ex-dates | 2010 | `raw.nse_corporate_action` |
| Main-board companies | `EQUITY_L.csv` | today's copy | `raw.nse_equity_list` |
| SME companies | `SME_EQUITY_L.csv` | today's copy | `raw.nse_equity_list` |
| ETFs | `eq_etfseclist.csv` | today's copy | `raw.nse_etf_list` |
| Symbol changes | `symbolchange.csv` | today's copy | `raw.nse_symbol_change` |

Each of these is a row in the `raw.feed` table, which holds its full URL template and its dates.

## Things worth knowing

- **Both price formats exist from 1 Jan to 5 Jul 2024.** We keep both. Where both exist, the old
  format is the one read downstream. In tests they matched on every price, volume and value.
- **Old-format files before 22 Jun 2011 have no ISIN and no trade count.** Those columns are empty
  for those dates.
- **Shares outstanding have a gap.** The PR zip carries them from 2010 to 2013 (index members
  only) and from 1 Feb 2024 (every security). From 2014 to January 2024 it carries neither, so
  those years are filled from corporate actions (see
  [Clean bars and market cap](clean-bars.md#shares-outstanding-and-market-cap)).
- **Corporate actions are free text.** Ratios and amounts appear only in the subject line ("Bonus
  1:1", "Face Value Split From Rs 10 To Rs 2"), so the platform parses that text. See
  [Listings and corporate actions](listings.md#corporate-actions).
- **This year's and next year's** holiday list and corporate actions are fetched again on every
  run. A changed response is stored as a new revision.
- **Index turnover is in crores**, as NSE sends it. Clean bars convert it to rupees.
- **Index names change over time** (`S&P CNX Nifty`, then `CNX Nifty`, then `Nifty 50`). Raw keeps
  them as sent, and the listing builder joins them up.
- **There is no holiday list for 2010.** The calendar works 2010 out from which days had files (see
  [Trading calendar](calendar.md)).
- **List files have no date inside**, so they are stored under the day we downloaded them.
- **Nothing is filtered in raw.** Every series (EQ, BE, SM, bonds, T0, ...) is kept.

## How files are read

Each file type has its own parser in `platform/src/cagr_data/vendors/nse/`. Every parser:

1. checks that the header has exactly the expected columns;
2. reads every value as text, treating `""` and `"-"` as empty;
3. converts each column to its type, and rejects the file if a value doesn't fit;
4. checks that the date inside the file is the day we asked for, where the file has one.

A rejected file is not loaded, and the reason is recorded as a failed check (see
[Data quality](../pipeline/data-quality.md)).

## Stock price columns

Both formats are turned into the same columns.

| Column | New format | Old format |
|---|---|---|
| `trade_date` | `TradDt` | `TIMESTAMP` |
| `symbol`, `series` | `TckrSymb`, `SctySrs` | `SYMBOL`, `SERIES` |
| `isin` | `ISIN` | `ISIN` (from 22 Jun 2011) |
| `instrument_type` | `FinInstrmTp` | none |
| `open`, `high`, `low`, `close` | `OpnPric`, `HghPric`, `LwPric`, `ClsPric` | `OPEN`, `HIGH`, `LOW`, `CLOSE` |
| `last`, `prev_close` | `LastPric`, `PrvsClsgPric` | `LAST` (0 stored as empty), `PREVCLOSE` |
| `volume` | `TtlTradgVol` | `TOTTRDQTY` |
| `traded_value_inr` | `TtlTrfVal` | `TOTTRDVAL` |
| `trade_count` | `TtlNbOfTxsExctd` | `TOTALTRADES` (from 22 Jun 2011) |
