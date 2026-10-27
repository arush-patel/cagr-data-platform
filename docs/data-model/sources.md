# Data sources

**In short:** we download seven files from NSE. Three are published every trading day (stock prices,
index closes, and the holiday list), and the other three are daily snapshots of listed companies
and symbol changes.

## The files

| What | NSE file | Available from | Lands in |
|---|---|---|---|
| Stock prices, old format | `cm{DD}{MON}{YYYY}bhav.csv.zip` | 2010 to 5 Jul 2024 | `raw.nse_cm_bar` |
| Stock prices, new format (UDiFF) | `BhavCopy_NSE_CM_0_0_0_{YYYYMMDD}_F_0000.csv.zip` | 1 Jan 2024 | `raw.nse_cm_bar` |
| Index closes | `ind_close_all_{DDMMYYYY}.csv` | 21 Feb 2012 | `raw.nse_index_close` |
| Holiday list | holiday-master API, one per year | 2011 | `raw.nse_holiday` |
| Main-board companies | `EQUITY_L.csv` | today's copy | `raw.nse_equity_list` |
| SME companies | `SME_EQUITY_L.csv` | today's copy | `raw.nse_equity_list` |
| Symbol changes | `symbolchange.csv` | today's copy | `raw.nse_symbol_change` |

Each of these is a row in the `raw.feed` table, which holds its full URL template and its dates.

## Things worth knowing

- **Both price formats exist from 1 Jan to 5 Jul 2024.** We keep both. Where both exist, the old
  format is the one read downstream. In tests they matched on every price, volume and value.
- **Old-format files before 22 Jun 2011 have no ISIN and no trade count.** Those columns are empty
  for those dates.
- **Index turnover is in crores**, as NSE sends it. It is not converted.
- **Index names change over time** (`S&P CNX Nifty`, then `CNX Nifty`, then `Nifty 50`). We keep them
  as sent.
- **There is no holiday list for 2010.** The calendar works 2010 out from which days had files (see
  [Trading calendar](calendar.md)).
- **Company list files have no date inside**, so they are stored under the day we downloaded them.
- **Nothing is filtered.** Every series (EQ, BE, SM, bonds, T0, ...) is kept.

## How files are read

Each file type has its own parser in `platform/src/cagr_data/vendors/nse/`. Every parser:

1. checks that the header has exactly the expected columns;
2. reads every value as text, treating `""` and `"-"` as empty;
3. converts each column to its type, and rejects the file if a value doesn't fit;
4. checks that the date inside the file is the date we asked for.

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
