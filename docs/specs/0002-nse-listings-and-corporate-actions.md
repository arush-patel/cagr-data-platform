# Spec 0002: NSE listings and corporate actions

| | |
|---|---|
| Status | Agreed. Arush answered the open questions on 27 Oct 2026 (section 11). |
| Date | 2026-10-27 |
| Owner | Arush |
| Component | `platform` (`cagr_data.reference`), version 0.2.0 |
| Database | `cagr_test` |
| Depends on | Spec 0001: the raw bhavcopy, index closes, listing masters and symbol changes, plus the XNSE calendar |
| Next | Spec 0003: clean daily bars, adjusted and unadjusted, read by the toolkit |

## 1. Goal

Every row NSE sends belongs to a **listing** that has one number for its whole life. A ticker
change, an ISIN change or a move between EQ and BE does not change that number. Every split,
bonus, rights issue and demerger is recorded with the factor that makes old prices comparable
with new ones.

Once this step is done we can answer these questions for any date since 2010:

- Which listings existed on NSE that day, and what was each one's symbol and ISIN?
- Which listing does a bhavcopy row (symbol, series, ISIN) belong to?
- What corporate actions did a listing have, on which ex-dates, and by what factor did each one
  change its price?
- Which of these facts a person still has to confirm, and which were decided automatically, on
  what evidence.

Nothing here changes a price. Spec 0003 applies the factors to build clean bars.

## 2. Scope

### In scope

| What | Source | Why now |
|---|---|---|
| Listing identity: one `listing_id` per security on NSE, with dated symbols and ISINs | Bhavcopy (every session since 2010), `symbolchange.csv`, `EQUITY_L`, `SME_EQUITY_L` and NSE's ETF list (new feed) | Every later layer is keyed on it (D4) |
| Index identity: one `listing_id` per NSE index, with dated names | `ind_close_all`, plus a hand-kept list of renames | Benchmarks and the industry indices need index bars too |
| Corporate actions, parsed into kind and ratio | NSE corporate-actions API (new feed) | D3 |
| Price adjustment factors per listing and ex-date | The parsed action's ratio, checked against the jump in the close and the ISIN change on the ex-date | Spec 0003 needs them to adjust bars |
| A review queue for what can't be decided automatically | This spec | D4 and D16: only clear-cut cases apply on their own |
| The read interface the toolkit uses for listings | This spec, section 9 | Toolkit slice T2 |

### Out of scope (later specs)

- Clean and adjusted bars, weekly and monthly bars (spec 0003).
- Shares outstanding and market cap (D5), and the universes built on them.
- Industry classification and its approval workflow (D14 to D17), and Nifty index membership.
- The review page itself. It is the first page of the new Observatory and is built in its own
  thread on top of this spec's tables (section 7.4). Teams cards come later.
- BSE and other venues. A second venue adds an instrument table above listings (section 6.1).
- Total-return series. Ordinary dividends are recorded but don't adjust prices; extraordinary
  dividends do (Q3).

## 3. Decisions this spec relies on

- D3: corporate actions come from NSE's corporate-actions API, parsed into kind and ratio, with
  a review queue.
- D4: listings and symbol changes come from the masters, `symbolchange.csv` and the ISIN on every
  bhavcopy. Changes are recorded in `ref.listing_change`, and only clear-cut cases apply on their
  own.
- D11: checks are rows in `ops.dq_check`, and critical checks fail the run.
- D16: whoever proposes a change can't also approve it.
- From spec 0001: raw is append-only and every raw row points at its file. This spec never edits
  raw. It reads raw and writes `ref`.
- Project rule: no code or data is taken from the old repos. The old design artifacts are used
  only as a reference.

## 4. What a listing is

**A listing is one security trading on one venue.** On NSE that means one company's equity, one
ETF, one REIT or InvIT, or one index. It is not a symbol, and it is not an ISIN.

- **A symbol is a dated attribute.** When NSE renames `ZOMATO` to `ETERNAL`, the listing keeps
  its number and gets a new symbol from the change date.
- **An ISIN is a dated attribute.** It changes on some corporate actions, for example a
  face-value split or a partly paid share becoming fully paid. The listing stays the same.
- **The series is a property of each day's bar, not of the listing.** A stock that moves from EQ
  to BE (trade-for-trade) and back is one listing throughout. Spec 0003 picks the bar for each
  day.
- **`listing_id` is assigned once and never reused or renumbered.** If two listings later turn
  out to be the same security, one is merged into the other (section 7.4). The merged one keeps
  its number and points at the survivor.

Which bhavcopy series make a listing (Q2):

| Series | What it is | Listing? |
|---|---|---|
| EQ, BE, BZ | Main board equity: normal, trade-for-trade, and trade-for-trade for non-compliant companies | Yes, `asset_class = equity`, `board = main` |
| SM, ST, SZ | SME board (Emerge) | Yes, `asset_class = equity`, `board = sme` |
| EQ rows of ETFs | Exchange-traded funds and fund units trade in EQ. In Oct 2026 they were 355 of 2,750 EQ symbols, all missing from `EQUITY_L` | Yes, `asset_class = etf`: an ISIN starting `INF`, and NSE's ETF list (its URL is confirmed in task 3) |
| RR, IV | REITs and InvITs | Yes, `asset_class = reit` or `invit` |
| T0 | Same-day settlement window for stocks that also trade in EQ | No. Its rows belong to the EQ listing but never make its bar (0003) |
| Bonds, G-secs, T-bills, SGBs, debt series (N*, Y*, Z*, GS, GB, TB and so on) | Debt | No |
| Rights entitlements (E1, E2 and so on) | Short-lived rights | No. Recorded as part of the rights issue |

In 2024 to 2026 the bhavcopy has 144 series, but a symbol is never in two of EQ, BE, BZ, SM, ST and
SZ on the same day, so each listing has at most one row per session ([source check](0002-source-check.md)).

## 5. How listings are built

### 5.1 One session at a time, in date order

The listing state on day *d* is built from the state on the session before *d*, plus *d*'s
evidence. The historical build runs the same step for every session from 4 Jan 2010, so history
and the nightly run share one code path.

Each session's step reads:

- the session's bhavcopy rows in listing series (from `raw.nse_cm_bar_current`);
- symbol changes whose change date falls within five sessions of *d* (from the latest
  `symbolchange.csv` snapshot, which holds the full history);
- manual decisions recorded in `ref.listing_change` (section 7.4).

### 5.2 The rules, in order

For each row (symbol *s*, ISIN *i*) on session *d*:

1. **Known ISIN.** If *i* belongs to a listing *L*, the row is *L*'s. If *L*'s current symbol is
   not *s*, that's a symbol change on *L* from *d*. It applies automatically, because the ISIN
   proves the identity. If `symbolchange.csv` doesn't confirm it, a warning is raised.
2. **Known symbol, new ISIN.** If *s* is *L*'s current symbol, *L* traded within the last 20
   sessions, and *L*'s old ISIN is not in today's file, that's an ISIN change on *L* from *d*. It
   applies automatically. This happens about 70 times a year, almost always on a face-value
   split, and the old ISIN never comes back. If *L* had been silent for longer, a new listing is
   created instead, with a review item asking whether to merge (section 7.4).
3. **Symbol change without an ISIN** (before 22 Jun 2011). If *s* is the new symbol of a
   `symbolchange.csv` entry dated within five sessions of *d*, and the old symbol belongs to a
   listing that traded within the last 20 sessions, that's a symbol change on that listing. It
   applies automatically.
4. **Anything else is a new listing**, created automatically from *d* with symbol *s* and ISIN
   *i*. A new listing is a fact in the file, so it needs no approval.

Every change the rules make is written to `ref.listing_change` with the evidence for it: the
file, the rule number, and the matching `symbolchange.csv` row.

### 5.3 Before ISINs (Jan 2010 to 21 Jun 2011)

Files before 22 Jun 2011 have no ISIN column, so those rows match on symbol (rules 3 and 4). On
22 Jun 2011, the first file with ISINs, each existing listing takes the ISIN of its symbol's row.
That ISIN is dated from the listing's first session, because it identifies the same security.
Listings that stopped trading before 22 Jun 2011 keep no ISIN.

### 5.4 When a listing stops trading

A listing that disappears from the bhavcopy may be suspended or delisted, and history can't tell
the two apart (the masters exist only from 27 Oct 2026). Illiquid SME and trade-for-trade
stocks also go weeks without a trade: 306 symbols had gaps of more than five sessions in 2024 to
2026. So a listing records its `first_session` and `last_session`, the first and last days it had
a row, and it has no bar on days it didn't trade. The status `inactive` means it is in none of the
current masters (equity, SME, ETF) and hasn't traded for 20 sessions. If it trades again, it
becomes `active` again.

### 5.5 Indices

`ind_close_all` names each index, and names change over the years (`S&P CNX Nifty`, then
`CNX Nifty`, then `Nifty 50`). There is no ISIN to prove identity, so renames come from a
hand-kept seed list in a migration. A new index name that isn't on the list becomes a new
listing, with a review item asking whether it's a rename.

## 6. Schema

All tables are in `ref`, written by `cagr_writer` and readable by `cagr_read`. One new raw
table holds the corporate-actions feed. Dated rows use `valid_from` (inclusive) and `valid_to`
(exclusive, null while current), and "as of *d*" means `valid_from <= d AND (valid_to IS NULL OR
valid_to > d)`. That's the rule the toolkit already assumes.

### 6.1 Listings

**`ref.listing` is the stock master: the one list of every security we track**, with one row for
each, whether it still trades or not. The view `ref.listing_current` puts each listing next to
its current symbol and ISIN, so "which ISINs are we tracking?" is one query.

| Table | One row per | Main columns |
|---|---|---|
| `ref.listing` | listing | `listing_id` (integer identity), `venue_id` (`XNSE`), `asset_class` (`equity`, `etf`, `reit`, `invit`, `index`), `board` (`main`, `sme`, null for indices), `name` (latest from the masters), `listed_on` (the master's listing date when known), `first_session`, `last_session`, `status` (`active`, `inactive`, `merged`), `merged_into` |
| `ref.listing_symbol` | symbol period of a listing | `listing_id`, `symbol`, `valid_from`, `valid_to`, `change_id` |
| `ref.listing_isin` | ISIN period of a listing | `listing_id`, `isin`, `valid_from`, `valid_to`, `change_id` |
| `ref.listing_change` | change, applied or waiting | `change_id`, `kind` (`new_listing`, `symbol_change`, `isin_change`, `merge`, `index_rename`), `listing_id`, `effective_date`, `payload` (jsonb: old and new values), `evidence` (jsonb: file ids, rule, matching rows), `status` (`applied`, `pending`, `rejected`), `auto` (true when the rules applied it), `proposed_by` (`rules`, or the person's name), `decided_by`, `decided_at`, `note` |

Rules the database enforces:

- **No overlaps.** A listing has one symbol and one ISIN at a time. This uses
  `EXCLUDE USING gist` on `int4range(listing_id, listing_id, '[]')` and the date range. Both are
  built-in range types, so no extension is needed.
- **No symbol or ISIN on two listings on the same day.** A text column can't go in a gist
  exclusion without `btree_gist`, which we can't enable, so a critical `ref`-layer check does
  this after each session's step (section 8).
- **Someone else decides.** `decided_by <> proposed_by` (D16). Rows the rules propose carry
  `proposed_by = 'rules'`, so any person can decide them.

The `evidence` of a change has a fixed shape, so the review page can show it without working it
out again:

| Key | Present for | Value |
|---|---|---|
| `rule` | every change | `"5.2.1"` to `"5.2.4"`, `"5.3"`, `"5.5"` or `"manual"` |
| `session` | every change | the session date the rule ran on |
| `file_ids` | every change | the `raw.vendor_file` ids it read |
| `row` | rule changes | the bhavcopy row: `symbol`, `series`, `isin`, `close`, `volume` |
| `previous` | symbol and ISIN changes | the listing's last row before the change: `date`, `symbol`, `isin`, `close` |
| `symbolchange` | rule 5.2.3 | the `symbolchange.csv` row: `old_symbol`, `new_symbol`, `date` |
| `candidates` | review items about a possible merge or rename | the `listing_id`s it could belong to |

`ref.listing_change` is the audit trail: the symbol and ISIN tables can always be rebuilt from
its applied rows.

A second venue (BSE) will add `ref.instrument` above listings, so one ISIN can have a listing on
each venue. NSE alone doesn't need it.

### 6.2 Corporate actions

| Table | One row per | Main columns |
|---|---|---|
| `raw.nse_corporate_action` | row of an API response | `file_id`, `symbol`, `series`, `isin`, `company`, `subject`, `ex_date`, `record_date`, `face_value`, plus the other fields the response carries, as text |
| `ref.corporate_action` | parsed action | `action_id`, `listing_id`, `ex_date`, `kind` (`split`, `bonus`, `consolidation`, `rights`, `demerger`, `dividend`, `buyback`, `other`), `ratio_new`, `ratio_old`, `cash_per_share`, `issue_price` (rights), `subject`, `parse_status` (`parsed`, `unparsed`), `source_file_id` |
| `ref.price_adjustment` | ex-date that changed a listing's price basis | `adjustment_id` (rising identity), `listing_id`, `ex_date`, `factor` (multiplies prices before `ex_date`), `observed_factor`, `expected_factor`, `close_before` (the previous traded close), `close_on` (the ex-date's close), `action_id` (null when nothing explains it), `status` (`applied`, `pending`, `rejected`), `auto`, `proposed_by`, `decided_by`, `decided_at`, `applied_at`, `note` |

`factor` is what spec 0003 multiplies into every price before the ex-date. Volumes are divided by
it.

A dataset records the adjustment state it used as one number: the highest `adjustment_id` among
applied rows, together with the time it read them. A row's factor never changes after it is
applied. A correction rejects the row and applies a new one with a higher id.

## 7. Corporate actions and adjustment factors

### 7.1 The feed

A new feed, `nse_corporate_actions`, calls NSE's corporate-actions API for a window of ex-dates:

- **Nightly:** ex-dates from today minus 7 days to today plus 30 days (D3). Actions are announced
  before they take effect, so each one is seen several times before its ex-date. A changed
  response is a new revision, as in spec 0001.
- **History:** one call per calendar year from 2010. A full year comes back in one response (2,693
  rows for 2024), and the window filters on ex-date.

The API needs no cookie, answers with a JSON array, and writes empty values as `"-"`. Ratios and
amounts appear only in the free-text `subject`.

It follows spec 0001's rules for raw: politeness, receipts, revisions, and the original response
kept in storage.

### 7.2 Parsing the subject

NSE describes each action in free text, such as `Bonus 1:1`,
`Face Value Split (Sub-Division) - From Rs10/- Per Share To Re 1/- Per Share`,
`Rights 6:179 @ Premium Rs 1810/-` and `Dividend - Rs 10 Per Share`. Wording from around 2010
is abbreviated (`Fv Split Rs.10 To Re.1`, `Bon 1:1/Fv Spl Rs.5tors.2`) and gets its own
patterns. One subject can hold two actions, which become two rows. A parser with one pattern per kind
turns the subject into a kind, a ratio, a cash amount and, for rights, an issue price. A subject
that no pattern matches is stored as `unparsed`, with nothing guessed. Each pattern is tested on
real subjects collected by the probe.

### 7.3 Where the factor comes from

**The factor comes from the parsed action. The price data confirms it.** NSE doesn't adjust
anything for us: its `prev_close` on an ex-date is the unadjusted last close (RELIANCE's on
28 Oct 2024, the day of its 1:1 bonus, was 2655.70 against a close of 1334.35). So the only
evidence in the bhavcopy is the jump in the close itself, plus the ISIN change that comes with
most splits.

The factor multiplies every price before the ex-date:

| Kind | Factor |
|---|---|
| Split from face value *F₁* to *F₂* | *F₂ / F₁* (Rs 10 to Re 1: 0.1) |
| Bonus *a*:*b* (*a* new for every *b* held) | *b / (a + b)* (1:1: 0.5; 3:1: 0.25) |
| Consolidation | *F₂ / F₁* (above 1) |
| Rights *a*:*b* at issue price *R* (face value plus premium) | *TERP / C*, where *C* is the close before the ex-date and *TERP = (b·C + a·R) / (a + b)* |
| Demerger, capital reduction | From prices: the ex-date's first price divided by *C*. Always pending, so a person confirms |
| Extraordinary dividend: cash *D* on one ex-date with *D / C* ≥ 2% | *(C − D) / C* (Q3). Every dividend on the same ex-date is summed first |
| Ordinary dividend (*D / C* below 2%), buyback, AGM, interest | No adjustment (Q3). Recorded only |

Each session, for each listing that traded, the step checks the evidence:

- `observed = close on d / close on the listing's previous traded session`
- A day is a **jump** when `observed` is below 0.75 or above 1.33. That is beyond any price band
  that applies to the stock, so a normal day can't produce it.

| Case | Result |
|---|---|
| Split, bonus or consolidation on its ex-date, and `observed / factor` between 0.8 and 1.25 | Applied automatically |
| Rights issue on its ex-date | Applied automatically with the TERP factor. A rights factor is close to 1, so no jump is expected |
| Extraordinary dividend on its ex-date | Applied automatically. A 2% to 10% drop is inside normal moves, so the price can't confirm it; the parsed amount is the evidence |
| Split, bonus or consolidation on its ex-date, but the price disagrees | Pending |
| A jump near a clean ratio (1/2, 2/3, 1/5, 1/10 and so on) with an ISIN change, and no action | Pending. In history before NSE's announcements are complete, applied automatically and marked (Q5) |
| Any other jump without an action | Pending (often a re-listing or a series move, and usually rejected) |

In 2024 to 2026, 110 EQ jumps landed on a clean ratio (1/2 29 times, 2/3 29, 1/10 29, 1/5 15).
The probe also found a ×100 consolidation (KAUSHALYA, 6 Feb 2024) with no announcement, which is
why a clean-ratio jump with an ISIN change still raises a review item.

### 7.4 Review

What the rules can't decide waits in `ref.listing_change` or `ref.price_adjustment` with
`status = pending`. Arush reviews them after the nightly run (Q6) on the Observatory's review
page, which is the first page built in the new Observatory. The same actions also exist as a
command, for scripts and for use before the page is ready:

```
uv run python -m cagr_data.reference.review list
uv run python -m cagr_data.reference.review approve 812 --as arush
uv run python -m cagr_data.reference.review reject 813 --as arush --note "not a split"
uv run python -m cagr_data.reference.review merge 4021 --into 377 --as arush
```

- Approving or rejecting sets `decided_by` and `decided_at`. Someone who proposed a change by
  hand can't approve it themselves.
- A merge moves the merged listing's symbol and ISIN periods to the survivor, marks the merged
  listing `merged`, and is itself a change row, so it can be undone.
- A pending adjustment holds back only its own listing. Spec 0003 builds that listing's adjusted
  bars only up to the session before the pending ex-date, and marks them, so one unclear action
  never blocks the night for everyone else.
- A pending item older than three sessions is a warning every night until someone decides it
  (D3).

## 8. Checks (`ref` layer)

| Check | Severity | Passes when |
|---|---|---|
| `listing_unique_symbol` | critical | no symbol belongs to two listings on the same day |
| `listing_unique_isin` | critical | no ISIN belongs to two listings on the same day |
| `listing_rows_mapped` | critical | every bhavcopy row in a listing series maps to exactly one listing |
| `listing_isin_present` | warn | every active equity listing has an ISIN from 22 Jun 2011 |
| `symbol_change_confirmed` | warn | each symbol change applied by rule 1 has a matching `symbolchange.csv` row |
| `master_coverage` | warn | every symbol in the latest `EQUITY_L`, `SME_EQUITY_L` and ETF list has a listing, and every listing that traded today is in one of them |
| `corporate_action_parsed` | warn | every action with an ex-date in the next 3 sessions is parsed |
| `adjustment_explained` | warn | every adjustment event on this session is applied or pending, never ignored |
| `review_waiting` | warn | no pending listing change or adjustment is older than 3 sessions |

## 9. Read interface (for the toolkit and spec 0003)

The toolkit reads only what this section lists, as `cagr_read`. Anything else may change.

| Object | What it gives |
|---|---|
| `ref.listing` | one row per listing, with the columns in section 6.1 |
| `ref.listing_current` | the stock master as of today: `listing_id`, `symbol`, `isin`, `name`, `asset_class`, `board`, `status`, `first_session`, `last_session`, one row per listing |
| `ref.listing_symbol`, `ref.listing_isin` | dated symbols and ISINs |
| `ref.listings_as_of(d date)` | a function returning `listing_id`, `symbol`, `isin`, `name`, `asset_class`, `board` for every listing that had traded by *d* and wasn't merged |
| `ref.listing_by_symbol(symbol text, d date)` | the `listing_id` that had that symbol on *d*, or null |
| `ref.price_adjustment` (applied rows) | factors by listing and ex-date, for readers who want to undo or redo adjustment themselves. `max(adjustment_id)` over applied rows identifies the adjustment state |

- Every row is keyed on the integer `listing_id`. Symbols and ISINs are attributes, never keys.
- Point in time: as of *d*, as in section 6.
- The calendar stays `ref.calendar_session`.

## 10. Plan

Each task is one PR into main.

| # | Task | Output |
|---|---|---|
| 0 | Probe: series in our data, ISIN and symbol changes, price jumps, the corporate-actions API (run from the laptop) | Done 27 Oct 2026: [`0002-source-check.md`](0002-source-check.md) |
| 1 | Migration: `ref.listing*` tables, `ref.listing_current`, constraints, grants, the index rename seed, the ref checks | Tables in `cagr_test` |
| 2 | Listing builder (rules 5.2 to 5.5), run over all history from `raw` | Every bhavcopy row since 2010 maps to a listing; the ref checks pass |
| 3 | Corporate-actions feed, the ETF list feed, and the subject parser | `raw.nse_corporate_action` filled since 2010; parse rate reported |
| 4 | Adjustment factors and matching (7.3) | `ref.price_adjustment` for all history; pending list reviewed with Arush |
| 5 | Review command, Dagster assets after the nightly files, read functions and grants | Nightly run keeps listings and adjustments current |

Acceptance in `cagr_test`:

1. Every bhavcopy row in a listing series since 2010 maps to exactly one listing.
2. Known cases come out right, such as RELIANCE's 1:1 bonus (ex-date 28 Oct 2024), a
   face-value split, a rename (ZOMATO to ETERNAL), an ISIN change and a demerger.
3. Every adjustment event since 2010 is applied or decided, and the pending list is empty or
   agreed with Arush.
4. Rerunning the builder over history changes nothing.

## 11. Open questions

| # | Question | Recommendation |
|---|---|---|
| Q1 | Is a listing the security (surviving symbol and ISIN changes) rather than the symbol? | **Agreed.** The listing follows the security as its symbol continues under a new name |
| Q2 | Which instruments get listings? | **Agreed.** Main-board and SME equities, ETFs, REITs, InvITs and indices. No debt, no T0, no rights entitlements |
| Q3 | Should dividends adjust prices? | **Decided:** ordinary dividends no, extraordinary dividends yes. A dividend of at least 2% of the previous close is adjusted, as Zerodha's Kite does. Splits, bonuses, consolidations, rights and demergers are all adjusted |
| Q4 | Should rights issues be adjusted with the theoretical ex-rights price (TERP)? | **Agreed.** TERP |
| Q5 | For history, should an unexplained jump that's a clean ratio (1/2, 1/5 and so on) apply without a person? | **Agreed.** Applied automatically and marked |
| Q6 | Who reviews pending items, and how often? | **Decided:** Arush, after the nightly run, on a review page in the Observatory. The Observatory rebuild starts now, with this page first |
