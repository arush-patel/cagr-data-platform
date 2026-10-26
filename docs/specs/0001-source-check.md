# Spec 0001, task 0: NSE source check

Checked on **26 Oct 2026** (evening IST) from Arush's laptop (macOS, home connection), with `curl`
and a desktop Chrome user agent (`Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36
(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36`). Requests were sequential with at least 1.1 s
between them: 103 in all, no 403, no throttling, no cookie needed anywhere. This note records what
the archive returned. It does not change [the spec](0001-nse-daily-market-data.md); section 6 lists
what the spec should take on board.

## 1. Summary

| Feed | URL in spec §4 | Result |
|---|---|---|
| `nse_cm_bhavcopy_udiff` | works | Published from **1 Jan 2024**, not 8 Jul 2024. All 2023 dates tried are 404. |
| `nse_cm_bhavcopy_legacy` | works | Last file **5 Jul 2024** (8 Jul 2024 is 404). 2010 files exist. **No ISIN column until 22 Jun 2011**, not "before 2011". |
| Overlap | | Both formats for **1 Jan to 5 Jul 2024**. On 5 Jul 2024 the two match exactly (2,775 rows, every field). |
| `nse_index_close` | works | Earliest file **21 Feb 2012**. Every session 2 to 17 Feb 2012 is 404. |
| `nse_holidays` | works | 200 JSON with no cookie and no 403. 2011 and 2026 have data; **2010 is 200 with `{}`**, not 404. |
| `nse_equity_list` | works | 2,629 rows. |
| `nse_sme_equity_list` | works as written | 583 rows. |
| `nse_symbol_change` | works | 1,070 rows. |
| Non-sessions | | Sunday 1 Feb 2026 (Budget) is 200; holiday 2 Oct 2026 and Saturday 24 Oct 2026 are 404. |

## 2. Findings per feed

### 2.1 UDiFF bhavcopy

- First file: `20240101`. `20231229`, `20231215` and the first session of every month of 2023 are 404.
  The spec's "8 Jul 2024" is the date NSE stopped publishing the legacy file, not the date UDiFF began.
- The inner file name equals the outer name without `.zip`, e.g.
  `BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv`.
- Two header variants:
  - **1 Jan to 20 Jun 2024**: reserved columns are `Rsvd01,Rsvd02,Rsvd03,Rsvd04,` with a trailing
    comma, so the header has 35 fields while every data row has 34.
  - **21 Jun 2024 onwards**: `Rsvd1,Rsvd2,Rsvd3,Rsvd4`, no trailing comma, 34 fields in header and
    rows. (20 Jun is the last old one, 21 Jun the first new one.)

  The parser should map by position for the 30 named columns and ignore the reserved ones, or
  normalise the names; a strict header comparison would reject half of 2024.
- The zip entry of the 2 Jan 2024 file is stamped 5 Jan 2024, so the early-2024 files may have been
  produced after the day. Our receipt records our own download time, so this changes nothing.
- Every row in the CM file is `Sgmt=CM`, `Src=NSE`, `FinInstrmTp=STK`; series (`SctySrs`) include
  EQ, BE, BZ, SM, ST, GB, GS, TB, N1 to N7, NA, SG.
- Line endings are LF. Dates are ISO (`2024-07-08`).

Header (from 21 Jun 2024):

```
TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4
```

Header (1 Jan to 20 Jun 2024): the same up to `Rmks`, then `Rsvd01,Rsvd02,Rsvd03,Rsvd04,`.

### 2.2 Legacy bhavcopy

- Exists for 4 Jan 2010 and runs to 5 Jul 2024. 8 Jul 2024 is 404, so the legacy feed ends on
  5 Jul 2024.
- Inner name `cm{DD}{MON}{YYYY}bhav.csv`.
- Two header variants:
  - **2010 to 21 Jun 2011** (12 fields, trailing comma):
    `SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,`
  - **22 Jun 2011 onwards** (14 fields, trailing comma):
    `SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,`

  So both `ISIN` **and** `TOTALTRADES` are missing before 22 Jun 2011. Pinned by fetching every
  session from 16 to 30 Jun 2011: 21 Jun has 12 fields, 22 Jun has 14.
- `TIMESTAMP` is `DD-MON-YYYY` but **not zero-padded in 2010** (`4-JAN-2010`), padded in 2024
  (`05-JUL-2024`). Parse with a format that accepts both.
- The 4 Jan 2010 file has no SM or ST rows (the SME platform came later); BE is present.
- Line endings are LF.

### 2.3 Parity on the overlap

Legacy `cm05JUL2024bhav.csv` against UDiFF `20240705`, joined on (symbol, series): 2,775 rows each,
2,775 matched, none on one side only. OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE, TOTTRDQTY, TOTTRDVAL,
TOTALTRADES and ISIN agree on every row (prices compared numerically: legacy writes `3197`, UDiFF
`3197.00`). The spec's plan to fetch both and make the overlap a parity test works, and the overlap
is about 130 sessions rather than none.

### 2.4 Index closes (`ind_close_all`)

- `23102026` is 200 (167 indices).
- Years: 2 Jan 2012 is 404; the first session of every year 2013 to 2020 is 200.
- 2012 by month: 1 Feb is 404, 1 Mar onwards 200. Every session from 2 to 17 Feb 2012 is 404; 21 Feb
  to 29 Feb are 200. 20 Feb 2012 was Mahashivratri. **Earliest file: 21 Feb 2012** (30 indices).
- The header is the same in 2012, 2020 and 2026:
  `Index Name,Index Date,Open Index Value,High Index Value,Low Index Value,Closing Index Value,Points Change,Change(%),Volume,Turnover (Rs. Cr.),P/E,P/B,Div Yield`
- Index names change over time (`S&P CNX Nifty` in 2012 and 2013, `Nifty 50` by 2020; `CNX Nifty
  Junior` then `Nifty Next 50`). Mapping names to a stable index id belongs to the spec that models
  indices; raw keeps the name as sent.
- Line endings differ: the 1 Jan 2020 file is **CRLF**, the 2012 and 2026 files are LF.
- `Index Date` is `DD-MM-YYYY`. Percent change can be written without a leading zero (`.77`, `-.39`).
- One 404 body is 3,535 bytes of HTML, slightly different from the bhavcopy 404 (3,425 bytes).

### 2.5 Holiday API

- `https://www.nseindia.com/api/holiday-master?type=trading&year={YYYY}` returned 200
  `application/json` on the first try for 2010, 2011 and 2026, with no cookie and only a
  `Referer: https://www.nseindia.com/` header. The 403-then-cookie fallback was not needed today; keep
  it, since that behaviour is known to change.
- The body is gzip on the wire (1,070 bytes for 2026, 31,501 bytes decoded).
- **2010 returns 200 with `{}`**, so "no data" is an empty object, not a 404. The calendar builder
  must treat `{}` as "no list", not as "no holidays".
- Shape: an object keyed by segment code, each a list of
  `{tradingDate: "DD-Mon-YYYY", weekDay, description, morning_session, evening_session}`.
  2026 keys: CBM, CD, CM, CMOT, COM, EGR, FO, IRD, MF, NDM, NTRP, SLBS. 2011 keys: CBM, CD, CM, FO,
  IRD, MF, RDM, SLBS, WDM. **Use `CM`**: segments differ (2011 has 19 CBM entries but 14 CM entries),
  and CM is not the first key.
- The CM list includes holidays that fall on weekends (2026: Sun 15 Feb, Sat 21 Mar, Sat 15 Aug,
  Sun 8 Nov). A weekend date in the list is not a contradiction with a 404.
- 1 Feb 2026 (a Sunday with a session) is not in the list, as section 7.2 expects for a special
  session. `morning_session` and `evening_session` are null on every CM entry in 2026.

### 2.6 Masters and symbol changes

| File | Rows | Header | Notes |
|---|---|---|---|
| `EQUITY_L.csv` | 2,629 | `SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE` | Leading spaces in six header names; strip them. Dates `DD-MON-YYYY`. |
| `SME_EQUITY_L.csv` | 583 | `SYMBOL,NAME_OF_COMPANY,SERIES,DATE_OF_LISTING,PAID_UP_VALUE,ISIN_NUMBER,FACE_VALUE,` | URL in spec works. Underscored names, trailing comma on header and rows, **two-digit year** dates (`22-Oct-26`), series SM and ST, no market lot column. |
| `symbolchange.csv` | 1,070 | `NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE` | Company names carry a leading space. Dates `DD-MON-YYYY`. |

None has a byte-order mark.

### 2.7 Non-session days

| Date | Day | Expected | Got |
|---|---|---|---|
| 1 Feb 2026 | Sunday (Budget) | 200 | 200, 3,229 rows |
| 2 Oct 2026 | Friday, holiday | 404 | 404 |
| 24 Oct 2026 | Saturday | 404 | 404 |

A missing file is a real HTTP 404 with an HTML body (`text/html`), never a 200 error page, so status
alone is enough to tell "no file" from "file".

## 3. Fixtures

In `platform/tests/fixtures/nse/` (64 KB). Each is a real file trimmed to its header plus the first
14 to 18 rows and, where present, RELIANCE (EQ) and up to two rows each of BE, SM and ST. Kept lines
are byte-for-byte as sent. Bhavcopy zips are re-zipped with the original inner file name and entry
timestamp and keep the original outer name.

| File | Rows | Why |
|---|---|---|
| `BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip` | 21 | UDiFF, current header |
| `BhavCopy_NSE_CM_0_0_0_20240102_F_0000.csv.zip` | 16 | UDiFF, old `Rsvd01` header with trailing comma (extra, not in the task list) |
| `cm05JUL2024bhav.csv.zip` | 21 | Legacy with ISIN, last legacy day |
| `cm04JAN2010bhav.csv.zip` | 16 | Legacy without ISIN or TOTALTRADES, unpadded date |
| `ind_close_all_23102026.csv` | 17 | Index closes, includes Nifty 50, 500, Bank, Midcap 150, Smallcap 250 |
| `EQUITY_L.csv` | 17 | Includes RELIANCE and two BE rows |
| `SME_EQUITY_L.csv` | 16 | SM and ST rows |
| `symbolchange.csv` | 18 | |
| `holiday-master_trading_2026.json` | whole | Untrimmed, decoded JSON |

## 4. Every request

Bytes are as received on the wire (the holiday API is gzip). Time is total request time in ms.
Paths without a host are on `https://nsearchives.nseindia.com`; `www:` is `https://www.nseindia.com`.

| # | URL | Status | Bytes | ms | Content type |
|---|---|---|---|---|---|
| 1 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20261023_F_0000.csv.zip` | 200 | 209421 | 115 | application/zip |
| 2 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip` | 200 | 166236 | 192 | application/zip |
| 3 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240705_F_0000.csv.zip` | 200 | 164296 | 119 | application/zip |
| 4 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240102_F_0000.csv.zip` | 200 | 151130 | 196 | application/zip |
| 5 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230102_F_0000.csv.zip` | 404 | 3425 | 132 | text/html |
| 6 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20200102_F_0000.csv.zip` | 404 | 3425 | 134 | text/html |
| 7 | `/content/historical/EQUITIES/2024/JUL/cm05JUL2024bhav.csv.zip` | 200 | 109203 | 126 | application/zip |
| 8 | `/content/historical/EQUITIES/2024/JUL/cm08JUL2024bhav.csv.zip` | 404 | 3425 | 101 | text/html |
| 9 | `/content/historical/EQUITIES/2011/JAN/cm03JAN2011bhav.csv.zip` | 200 | 42678 | 92 | application/zip |
| 10 | `/content/historical/EQUITIES/2010/JAN/cm04JAN2010bhav.csv.zip` | 200 | 39269 | 181 | application/zip |
| 11 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230201_F_0000.csv.zip` | 404 | 3425 | 237 | text/html |
| 12 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230301_F_0000.csv.zip` | 404 | 3425 | 121 | text/html |
| 13 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230403_F_0000.csv.zip` | 404 | 3425 | 107 | text/html |
| 14 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230502_F_0000.csv.zip` | 404 | 3425 | 172 | text/html |
| 15 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230601_F_0000.csv.zip` | 404 | 3425 | 136 | text/html |
| 16 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230703_F_0000.csv.zip` | 404 | 3425 | 109 | text/html |
| 17 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230801_F_0000.csv.zip` | 404 | 3425 | 161 | text/html |
| 18 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20230901_F_0000.csv.zip` | 404 | 3425 | 104 | text/html |
| 19 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20231003_F_0000.csv.zip` | 404 | 3425 | 218 | text/html |
| 20 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20231101_F_0000.csv.zip` | 404 | 3425 | 85 | text/html |
| 21 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20231201_F_0000.csv.zip` | 404 | 3425 | 167 | text/html |
| 22 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20231215_F_0000.csv.zip` | 404 | 3425 | 116 | text/html |
| 23 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20231229_F_0000.csv.zip` | 404 | 3425 | 167 | text/html |
| 24 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240101_F_0000.csv.zip` | 200 | 151548 | 123 | application/zip |
| 25 | `/content/indices/ind_close_all_23102026.csv` | 200 | 17927 | 158 | text/csv |
| 26 | `/content/indices/ind_close_all_02012012.csv` | 404 | 3535 | 127 | text/html |
| 27 | `/content/indices/ind_close_all_01012013.csv` | 200 | 3153 | 262 | text/csv |
| 28 | `/content/indices/ind_close_all_01012014.csv` | 200 | 3292 | 245 | text/csv |
| 29 | `/content/indices/ind_close_all_01012015.csv` | 200 | 4232 | 153 | text/csv |
| 30 | `/content/indices/ind_close_all_01012016.csv` | 200 | 5254 | 143 | text/csv |
| 31 | `/content/indices/ind_close_all_02012017.csv` | 200 | 6426 | 134 | text/csv |
| 32 | `/content/indices/ind_close_all_01012018.csv` | 200 | 7240 | 107 | text/csv |
| 33 | `/content/indices/ind_close_all_01012019.csv` | 200 | 7424 | 130 | text/csv |
| 34 | `/content/indices/ind_close_all_01012020.csv` | 200 | 7418 | 139 | text/csv |
| 35 | `/content/indices/ind_close_all_01022012.csv` | 404 | 3535 | 279 | text/html |
| 36 | `/content/indices/ind_close_all_01032012.csv` | 200 | 2985 | 146 | text/csv |
| 37 | `/content/indices/ind_close_all_02042012.csv` | 200 | 2928 | 155 | text/csv |
| 38 | `/content/indices/ind_close_all_02052012.csv` | 200 | 2957 | 119 | text/csv |
| 39 | `/content/indices/ind_close_all_01062012.csv` | 200 | 3003 | 1135 | text/csv |
| 40 | `/content/indices/ind_close_all_02072012.csv` | 200 | 2936 | 182 | text/csv |
| 41 | `/content/indices/ind_close_all_01082012.csv` | 200 | 2936 | 107 | text/csv |
| 42 | `/content/indices/ind_close_all_03092012.csv` | 200 | 2918 | 133 | text/csv |
| 43 | `/content/indices/ind_close_all_01102012.csv` | 200 | 2900 | 248 | text/csv |
| 44 | `/content/indices/ind_close_all_01112012.csv` | 200 | 2926 | 162 | text/csv |
| 45 | `/content/indices/ind_close_all_03122012.csv` | 200 | 3180 | 138 | text/csv |
| 46 | `/content/indices/ind_close_all_02022012.csv` | 404 | 3535 | 100 | text/html |
| 47 | `/content/indices/ind_close_all_03022012.csv` | 404 | 3535 | 108 | text/html |
| 48 | `/content/indices/ind_close_all_06022012.csv` | 404 | 3535 | 119 | text/html |
| 49 | `/content/indices/ind_close_all_07022012.csv` | 404 | 3535 | 111 | text/html |
| 50 | `/content/indices/ind_close_all_08022012.csv` | 404 | 3535 | 160 | text/html |
| 51 | `/content/indices/ind_close_all_09022012.csv` | 404 | 3535 | 129 | text/html |
| 52 | `/content/indices/ind_close_all_10022012.csv` | 404 | 3535 | 75 | text/html |
| 53 | `/content/indices/ind_close_all_13022012.csv` | 404 | 3535 | 231 | text/html |
| 54 | `/content/indices/ind_close_all_14022012.csv` | 404 | 3535 | 215 | text/html |
| 55 | `/content/indices/ind_close_all_15022012.csv` | 404 | 3535 | 91 | text/html |
| 56 | `/content/indices/ind_close_all_16022012.csv` | 404 | 3535 | 219 | text/html |
| 57 | `/content/indices/ind_close_all_17022012.csv` | 404 | 3535 | 138 | text/html |
| 58 | `/content/indices/ind_close_all_21022012.csv` | 200 | 2902 | 128 | text/csv |
| 59 | `/content/indices/ind_close_all_22022012.csv` | 200 | 2997 | 107 | text/csv |
| 60 | `/content/indices/ind_close_all_23022012.csv` | 200 | 2953 | 145 | text/csv |
| 61 | `/content/indices/ind_close_all_24022012.csv` | 200 | 2937 | 86 | text/csv |
| 62 | `/content/indices/ind_close_all_27022012.csv` | 200 | 3027 | 263 | text/csv |
| 63 | `/content/indices/ind_close_all_28022012.csv` | 200 | 2980 | 117 | text/csv |
| 64 | `/content/indices/ind_close_all_29022012.csv` | 200 | 2935 | 117 | text/csv |
| 65 | `www:/api/holiday-master?type=trading&year=2010` | 200 | 2 | 249 | application/json |
| 66 | `www:/api/holiday-master?type=trading&year=2011` | 200 | 690 | 200 | application/json |
| 67 | `www:/api/holiday-master?type=trading&year=2026` | 200 | 1070 | 236 | application/json |
| 68 | `/content/equities/EQUITY_L.csv` | 200 | 185689 | 171 | text/csv |
| 69 | `/emerge/corporates/content/SME_EQUITY_L.csv` | 200 | 40466 | 196 | text/csv |
| 70 | `/content/equities/symbolchange.csv` | 200 | 69403 | 163 | text/csv |
| 71 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20260201_F_0000.csv.zip` | 200 | 180592 | 152 | application/zip |
| 72 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20261002_F_0000.csv.zip` | 404 | 3425 | 131 | text/html |
| 73 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20261024_F_0000.csv.zip` | 404 | 3425 | 114 | text/html |
| 74 | `/content/historical/EQUITIES/2011/APR/cm01APR2011bhav.csv.zip` | 200 | 42616 | 1333 | application/zip |
| 75 | `/content/historical/EQUITIES/2011/JUL/cm01JUL2011bhav.csv.zip` | 200 | 53540 | 165 | application/zip |
| 76 | `/content/historical/EQUITIES/2011/OCT/cm03OCT2011bhav.csv.zip` | 200 | 53080 | 149 | application/zip |
| 77 | `/content/historical/EQUITIES/2012/JAN/cm02JAN2012bhav.csv.zip` | 200 | 52248 | 252 | application/zip |
| 78 | `/content/historical/EQUITIES/2013/JAN/cm01JAN2013bhav.csv.zip` | 200 | 55769 | 463 | application/zip |
| 79 | `/content/historical/EQUITIES/2011/MAY/cm02MAY2011bhav.csv.zip` | 200 | 42363 | 1325 | application/zip |
| 80 | `/content/historical/EQUITIES/2011/JUN/cm01JUN2011bhav.csv.zip` | 200 | 42553 | 128 | application/zip |
| 81 | `/content/historical/EQUITIES/2011/JUN/cm15JUN2011bhav.csv.zip` | 200 | 42335 | 145 | application/zip |
| 82 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240401_F_0000.csv.zip` | 200 | 154839 | 183 | application/zip |
| 83 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240603_F_0000.csv.zip` | 200 | 161229 | 146 | application/zip |
| 84 | `/content/historical/EQUITIES/2011/JUN/cm16JUN2011bhav.csv.zip` | 200 | 42032 | 96 | application/zip |
| 85 | `/content/historical/EQUITIES/2011/JUN/cm17JUN2011bhav.csv.zip` | 200 | 42613 | 133 | application/zip |
| 86 | `/content/historical/EQUITIES/2011/JUN/cm20JUN2011bhav.csv.zip` | 200 | 42756 | 137 | application/zip |
| 87 | `/content/historical/EQUITIES/2011/JUN/cm21JUN2011bhav.csv.zip` | 200 | 42395 | 123 | application/zip |
| 88 | `/content/historical/EQUITIES/2011/JUN/cm22JUN2011bhav.csv.zip` | 200 | 53301 | 154 | application/zip |
| 89 | `/content/historical/EQUITIES/2011/JUN/cm23JUN2011bhav.csv.zip` | 200 | 53263 | 126 | application/zip |
| 90 | `/content/historical/EQUITIES/2011/JUN/cm24JUN2011bhav.csv.zip` | 200 | 53788 | 280 | application/zip |
| 91 | `/content/historical/EQUITIES/2011/JUN/cm27JUN2011bhav.csv.zip` | 200 | 53231 | 393 | application/zip |
| 92 | `/content/historical/EQUITIES/2011/JUN/cm28JUN2011bhav.csv.zip` | 200 | 53145 | 145 | application/zip |
| 93 | `/content/historical/EQUITIES/2011/JUN/cm29JUN2011bhav.csv.zip` | 200 | 53951 | 501 | application/zip |
| 94 | `/content/historical/EQUITIES/2011/JUN/cm30JUN2011bhav.csv.zip` | 200 | 53840 | 569 | application/zip |
| 95 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240614_F_0000.csv.zip` | 200 | 161793 | 180 | application/zip |
| 96 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240625_F_0000.csv.zip` | 200 | 161947 | 226 | application/zip |
| 97 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240701_F_0000.csv.zip` | 200 | 165838 | 154 | application/zip |
| 98 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240703_F_0000.csv.zip` | 200 | 164165 | 189 | application/zip |
| 99 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240618_F_0000.csv.zip` | 200 | 164411 | 230 | application/zip |
| 100 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240619_F_0000.csv.zip` | 200 | 163205 | 273 | application/zip |
| 101 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240620_F_0000.csv.zip` | 200 | 162900 | 808 | application/zip |
| 102 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240621_F_0000.csv.zip` | 200 | 160960 | 255 | application/zip |
| 103 | `/content/cm/BhavCopy_NSE_CM_0_0_0_20240624_F_0000.csv.zip` | 200 | 163792 | 157 | application/zip |

## 5. Earliest dates, in one place

| Feed | First date that exists | Last date |
|---|---|---|
| Legacy bhavcopy | 4 Jan 2010 or earlier (not searched further back) | 5 Jul 2024 |
| Legacy bhavcopy with ISIN and TOTALTRADES | 22 Jun 2011 | 5 Jul 2024 |
| UDiFF bhavcopy | 1 Jan 2024 | ongoing |
| UDiFF with `Rsvd1` header | 21 Jun 2024 | ongoing |
| Index closes | 21 Feb 2012 | ongoing |
| Holiday API with data | 2011 (2010 is `{}`) | ongoing |

## 6. What contradicts or should update spec section 4 (and 7.5)

1. **UDiFF "Available from 8 Jul 2024" is wrong**: it is 1 Jan 2024. Legacy runs to 5 Jul 2024, so
   the two overlap for 1 Jan to 5 Jul 2024 and the parity test in section 4 applies to that range.
2. **Legacy "no ISIN column before 2011" is wrong**: the ISIN and TOTALTRADES columns start on
   22 Jun 2011. Section 7.5 should say rows from 2010 to 21 Jun 2011 land with `isin` null and
   no trade count, not only 2010. That is about 370 sessions, not 250.
3. **Index closes "to be found"**: 21 Feb 2012. Section 7.5's "index closes start where NSE's
   archive starts" is therefore 21 Feb 2012; anything before is the later one-off.
4. **Holidays**: the API returns `{}` (200) for 2010 rather than failing, and needs no cookie today.
   The calendar must read the `CM` key and accept weekend dates in it.
5. Not a contradiction, but new to the format seeds in section 6: the UDiFF format has two header
   variants (switch on 21 Jun 2024), the legacy format has two (switch on 22 Jun 2011), and
   `ind_close_all` line endings vary (CRLF in 2020, LF in 2012 and 2026).
