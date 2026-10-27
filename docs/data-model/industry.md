# Industry

**In short:** each listing can have one of Arush's 52 industries (`cagr_industry`, called `group_3`
in the old `stockdb`). The industries and their Nifty sector indices are in `ref.industry`. The
starting assignments come from a one-off export of the old database.

Code: `platform/src/cagr_data/reference/industry.py`. Plan: [spec 0006](../specs/0006-industry-classification.md).

## Tables

| Table | Holds |
|---|---|
| `ref.industry` | The 52 industries, each with its Nifty sector index (for example, Private Bank is in Nifty Private Bank) |
| `ref.listing_industry` | Each listing's current industry |
| `ref.industry_change` | Every assignment ever made or proposed, with who decided it |
| `ref.listing_tier` | NSE index tier periods (Nifty 50, Next 50, ...). **Empty for now:** nothing fills it yet |

A listing's industry applies to its whole history. If it changes, the old assignment stays in
`ref.industry_change`.

To read industry and tier for a date:

```sql
SELECT * FROM ref.listing_classification('2026-10-26');
```

## Loading the seed

The seed is `db/seeds/group3_2026-10-27.csv`, a `symbol,group_3` export from `stockdb`.

```sh
uv run python -m cagr_data.reference.industry db/seeds/group3_2026-10-27.csv
```

- Each symbol is matched to the listing that held it on the export date. A stock that has since
  been delisted or renamed is matched to the last listing that held it.
- A value that isn't one of the 52 industries (an old, coarser group, or a typo) isn't loaded. The
  listing stays unclassified.
- Symbols with no listing are reported, not guessed.
- A listing that already has an industry is left alone, so running it again adds nothing.

Seed rows are applied directly (proposed by `seed`), because they were curated by hand in the old
database.
