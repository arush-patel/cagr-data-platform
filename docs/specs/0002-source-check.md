# Spec 0002 source check

This was run read-only on 27 Oct 2026 from Arush's laptop, against the 2024 to 2026 bhavcopy
rows in `cagr_test` and NSE's corporate-actions API. Nothing was written to the database. The raw
API responses and the query output are kept on the laptop in `~/cagr-data/probe-0002/`.

## What it changed in the spec

- **NSE doesn't adjust `prev_close` on ex-dates.** It is always the previous session's close. The
  draft's idea of reading the factor from it was dropped. Factors now come from the parsed action,
  and the jump in the close confirms them (section 7.3).
- **The corporate-actions API is easy to backfill.** It needs no cookie, returns a full year per
  call, and has history from at least 2010 plus announced future ex-dates.
- **ETFs need their own master.** 355 of the 2,750 EQ symbols traded in Oct 2026 are ETFs or fund
  units (ISIN `INF...`) that `EQUITY_L` doesn't list.

## Bhavcopy, 2024-01-01 to 2026-10-26

| Question | Finding |
|---|---|
| Series | 144 in all. The largest are EQ (1.50 M rows, 3,117 symbols), SM (203 k, 619), BE (168 k, 1,263), ST (64 k, 610), GB, GS, BZ (20 k, 77) and TB, followed by bond series |
| A symbol in two of EQ, BE, BZ, SM, ST, SZ on one day | Never |
| ISIN changes under one symbol | 204, on 200 symbols. The old ISIN never came back. Almost all are face-value splits (NESTLEIND 5 Jan 2024, COCHINSHIP 10 Jan 2024), plus some ETFs |
| One ISIN under more than one symbol | 133 ISINs. `symbolchange.csv` explains 132 of them. The exception is INF174K01Z71 (KOTAKNV20, then NV20 from 19 Mar 2024). No ISIN was on two symbols at once |
| `prev_close` against the previous close | Equal (ratio between 0.999 and 1.001) on 1,948,085 of 1,948,115 consecutive pairs. On RELIANCE's 1:1 bonus (28 Oct 2024), `prev_close` was 2655.70, unadjusted. The 27 exceptions are consolidations (KAUSHALYA ×100 on 6 Feb 2024, CCAVENUE ×10 on 19 Oct 2026), re-listings and series moves |
| Close jumps at clean ratios (EQ, previous close ≥ Rs 20) | 110 in all. Factor 1/2: 29, 2/3: 29, 1/10: 29, 1/5: 15, 1/3: 6, 1/4: 2 |
| Gaps of more than 5 sessions | 1,840 gaps on 306 symbols, mostly illiquid SME and trade-for-trade stocks: SM 1,040, ST 452, BZ 134, BE 116, EQ 16. Five gaps are longer than 250 sessions |
| Masters against the bhavcopy | 2 `EQUITY_L` symbols never traded since 2024. 356 EQ symbols traded in Oct 2026 aren't in `EQUITY_L`: 355 ETFs and fund units, and ICICIPRULI |

`raw.nse_equity_list` and `raw.nse_symbol_change` are still empty in `cagr_test`, because no
daily snapshot has run yet. The master and symbol-change checks used the files downloaded on
26 Oct.

## Corporate-actions API

- **URL:** `https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`.
  Use `index=sme` for the SME board.
- **Access:** 7 requests, all 200 with JSON. No cookie or Referer was needed; we sent a desktop
  user agent and `Accept: application/json`. The response sets Akamai cookies, so the client
  keeps a cookie session in case NSE tightens access.
- **Body:** a JSON array. Empty values are the string `"-"`. Fields: `symbol`, `comp`, `series`,
  `isin`, `faceVal`, `subject`, `exDate` (`DD-Mon-YYYY`), `recDate`, `bcStartDate`, `bcEndDate`,
  `ndStartDate`, `ndEndDate`, `ind`, `caBroadcastDate` (always null).
- **Range:** the date window filters on ex-date.

  | Window | Rows |
  |---|---|
  | Jan to Mar 2010 | 158 |
  | Jan 2015 | 30 |
  | Oct 2024 | 76 |
  | Oct 2024, SME (all series SM) | 11 |
  | Oct to Nov 2026 | 59, 8 of them with ex-dates after 27 Oct |
  | All of 2024, in one call | 2,693. Its October rows equal the October-only call |
- **2024 by subject:** dividend 1,528, AGM or EGM 838, interest 89, split 65, bonus 64,
  rights 56, buyback 39, demerger 11, capital reduction 2, distribution 1, consolidation 0.
- **Sample subjects:**
  - `Face Value Split (Sub-Division) - From Rs10/- Per Share To Re 1/- Per Share`
  - `Bonus 1:1`, `Bonus 3:1`
  - `Rights 6:179 @ Premium Rs 1810/-`, `Rights 1:2 @ Premium Re 1/-`
  - `Dividend - Rs 10 Per Share`, `Interim Dividend - Re 0.01 Per Share`
  - `Annual General Meeting/Dividend - Rs  2.25 Per Share`
  - `Demerger`, `Capital Reduction`, `Buy Back`
  - `Distribution - Rs 2.82 Per Unit Comprising Of Return Of Capital - Rs 2.82 Per Unit`
  - `Interest Payment`, `Extra Ordinary General Meeting`
  - 2010 wording: `Fv Split Rs.10 To Re.1`, `Bon 1:1/Fv Spl Rs.5tors.2`,
    `Int Div-Rs.3/- Per Share Purpose Revised`, `Agm/Div-Rs.3.50 Per Share`
