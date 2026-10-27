# Listings and corporate actions

**In short:** a **listing** is one security, followed for its whole life even when its symbol or
ISIN changes. Everything downstream (bars, market cap, industry) is keyed on `listing_id`, never on
the symbol. Corporate actions such as splits and bonuses become **price adjustments**. When the
rules can't decide something, it waits for a person on the review page.

Code: `platform/src/cagr_data/reference/` (`listings.py`, `listing_build.py`,
`corporate_actions.py`, `adjustments.py`, `review.py`).

## What becomes a listing

| Bhavcopy series | Listing |
|---|---|
| EQ, BE, BZ | Main-board equity, or an ETF when the ISIN starts with `INF` |
| SM, ST, SZ | SME equity |
| RR | REIT |
| IV | InvIT |
| Any other (T0, bonds, rights entitlements, ...) | None |

Each index in the index-close file is also a listing, with asset class `index`. Old index names
are joined to the current one through `ref.index_alias` (`S&P CNX Nifty` and `CNX Nifty` are
both `Nifty 50`).

## How a row is matched to a listing

The builder goes through history one trading day at a time. The first rule that fits wins:

1. **Known ISIN:** the row belongs to that ISIN's listing. A different symbol means a symbol change.
2. **Known symbol with a new ISIN:** if the listing traded in the last 20 sessions and its old ISIN
   isn't in today's file, it's an ISIN change. If the listing had been silent for longer, a new
   listing is made and a review item asks whether to merge the two.
3. **No ISIN (before 22 Jun 2011):** if `symbolchange.csv` has a change to this symbol within 5
   sessions, from a symbol that traded in the last 20 sessions, it's a symbol change.
4. **Anything else** is a new listing.

A listing silent for more than 20 sessions whose symbol is in none of NSE's current lists is
marked `inactive`.

## Where listings live

| Table | One row per |
|---|---|
| `ref.listing` | Security: asset class, board, name, first and last session, status (`active`, `inactive`, `merged`) |
| `ref.listing_symbol` | Symbol a listing had, with `valid_from` and `valid_to` |
| `ref.listing_isin` | ISIN a listing had, with `valid_from` and `valid_to` |
| `ref.listing_change` | Change the builder made or proposed, with its evidence and status |

Dates run from `valid_from` (included) to `valid_to` (not included). An empty `valid_to` means
the symbol or ISIN is still current. A listing has one symbol and one ISIN at a time.

To look things up, use these rather than the tables:

| Use | Gives |
|---|---|
| `ref.listing_current` | Every listing with its current symbol and ISIN |
| `ref.listings_as_of(date)` | Every listing that existed on a date, with that day's symbol and ISIN |
| `ref.listing_by_symbol(symbol, date)` | The `listing_id` that held a symbol on a date |

```sql
SELECT ref.listing_by_symbol('INFY', '2015-06-01');
```

## Corporate actions

NSE's corporate-actions responses are parsed into `ref.corporate_action`, one row per action.
One announcement can hold two actions: "Bonus 1:1 / Face Value Split" gives two rows.

| Kind | Numbers parsed from the subject |
|---|---|
| `split`, `consolidation` | Old and new face value |
| `bonus` | a new shares for every b held |
| `rights` | a new for every b held, and the issue price |
| `dividend` | Rupees per share (a percentage is turned into rupees using the face value) |
| `demerger`, `buyback`, `other` | None |

A subject that no pattern recognises is stored as `unparsed`, with nothing guessed. Each action is
matched to a listing by ISIN, or else by its symbol on the ex-date.

## Price adjustments

A price adjustment says how to scale a listing's prices before an ex-date so the history is
comparable. Prices before the ex-date are multiplied by the **factor**. For a split from
Rs 10 to Rs 2 face value, the factor is 0.2.

The builder looks at every day that follows an ex-date of a price-changing action, and every day
whose close jumps against the previous close (below 0.75 times or above 1.33 times).

| Situation | Result |
|---|---|
| Split, bonus or consolidation, and the close moved as expected (within 0.8 to 1.25 of the expected factor) | Applied automatically |
| Split, bonus or consolidation, and the close disagrees | Waits for review |
| Rights issue, or a dividend of at least 2% of the previous close, without a price jump | Applied automatically, with the factor from the parsed amounts |
| Demerger, or an action the parser couldn't read | Waits for review |
| A jump with no action to explain it | Waits for review |
| A jump with no action before 1 Oct 2026, near a clean ratio (1:2, 1:5, ...) with an ISIN change | Applied automatically and marked, because NSE's old announcements are incomplete |

These are stored in `ref.price_adjustment`. A listing and ex-date that already has a row is never
proposed again, so a rejected item stays rejected. An applied factor is never edited: a correction
rejects the row and adds a new one.

## Review

Anything left `pending` in `ref.listing_change` or `ref.price_adjustment` is decided by a person,
on the Observatory's [review page](../observatory/README.md) or from the command line:

```sh
uv run python -m cagr_data.reference.review list
uv run python -m cagr_data.reference.review approve price_adjustment 813 --as arush
uv run python -m cagr_data.reference.review reject listing_change 812 --as arush --note "different company"
```

- Whoever proposed an item can't decide it.
- Approving a possible merge moves the newer listing's symbols and ISINs to the older one, and marks
  the newer one `merged`.
- The `review_waiting` check warns when an item has waited more than 3 sessions.
