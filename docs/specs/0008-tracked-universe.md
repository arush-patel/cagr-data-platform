# Spec 0008: the tracked universe

| | |
|---|---|
| Status | Agreed 29 Oct 2026 (Arush chose EQ main board plus liquid indices) |
| Date | 2026-10-29 |
| Owner | Arush |
| Component | `platform` (`cagr_data.reference`) |
| Database | `cagr_test` |
| Depends on | Spec 0002 (listings, the review queue), spec 0007 (market cap, the ₹1,500 crore floor) |

## 1. Goal

The platform follows only what institutions can trade: main-board stocks in the EQ series, and
the indices that matter. The review queue holds only items about those, so Arush can clear it.

## 2. What we found (29 Oct 2026)

- 4,043 price adjustments were pending. Only 483 involve a stock that ever reached ₹1,500
  crore, and only 312 of those are on an EQ-series day. BE, BZ, SM, ST and RR made up most of
  the rest.
- EQ carries more than 99% of stock turnover (₹186.0M crore since 2010, against ₹625k crore for
  BE and ₹198k crore for SM).
- Of 235 index listings, about 30 have no turnover: inactive ones, G-Sec, leverage and inverse,
  USD, dividend points and arbitrage. Many of the others are thematic or strategy variants.
- The 129 pending `index_rename` items are NSE's CNX to Nifty renaming on four days
  (2013-02-08, 2015-11-09, 2016-04-01 and 2016-07-07).

## 3. Decisions

1. **Tracked equities** are main-board equity listings that traded in the EQ series on some day.
2. **Tracked indices** are a hand-kept list in the repository
   (`platform/src/cagr_data/reference/tracked_indices.txt`), changed by pull request: broad
   market and size, India VIX, and the sector indices, including every sector index in
   `ref.industry`.
3. **Raw data stays complete.** Every file NSE publishes is still fetched and loaded. It costs
   little and can't be fetched again later.
4. **Review items outside the scope are set aside, never deleted.** A pending price adjustment
   stays pending only if its listing is a tracked equity, its event bar is in the EQ series, and
   the listing reached ₹1,500 crore on some day. Other items become `out_of_scope`, and come back
   if the scope widens.
6. **Moves that are real trading are set aside too** (decided 30 Oct 2026, after Arush checked
   POLICYBZR, TVSHLTD, KSE and MBECL on charts). Each carries a `scope_reason`:
   `real_move` (no action and no ISIN change, unless the drop is near 1/n), `small_demerger`
   (under 5%), `unparsed_dividend` (fell 5% or less) and `illiquid` (median value traded under
   ₹5 crore a day, unless a split, bonus or consolidation is announced). Possible merges are set
   aside as `illiquid` when both sides traded under ₹1 crore a day.
5. **Index renames with one candidate are approved in bulk**, by a person, with one command.

## 4. Schema (migration 0015)

| Object | What |
|---|---|
| `ref.tracked_listing` | `listing_id`, `reason` (`eq_main` or `index`), `built_at`; rebuilt whole |
| `ref.tracked_current` (view) | The tracked listings with their current symbol, ISIN, name and status |
| `ref.price_adjustment.status` | Adds `out_of_scope`, which needs `decided_by` and has no `applied_at` |
| `scope_reason` on `ref.price_adjustment` and `ref.listing_change` (migration 0017) | Why an item is `out_of_scope`; set exactly when the status is |
| `ref.listing_change.status` (migration 0017) | Adds `out_of_scope` |

## 5. Plan

| # | Task | Output |
|---|---|---|
| 1 | Migration 0015, `cagr_data.reference.universe`, `review approve-renames`, docs | This PR |
| 2 | On `cagr_test`: approve the renames, then build the universe and scope the queue | About 312 adjustments pending |
| 3 | Scope the clean builds and checks to the tracked set | Faster builds, checks only on what we trade |
| 4 | Migration 0017 and the scope reasons (rule 6) | About 60 adjustments and no merges pending |

## 6. Questions

| # | Question | Answer |
|---|---|---|
| Q1 | Which series? | **Decided:** EQ only. BE (trade-to-trade) days fall out of scope |
| Q2 | Which indices? | The list in `tracked_indices.txt`; Arush edits it by pull request |
