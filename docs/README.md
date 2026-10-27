# Documentation

These pages describe what is **built and merged on `main`**. Plans for future steps live in
[`specs/`](specs/). If a spec and the code disagree, these pages follow the code.

## What the platform does today

1. Every evening it downloads NSE's daily files and loads every row into Postgres exactly as NSE
   sent it (the `raw` layer).
2. It turns those rows into one record per security, called a **listing**, which follows the
   security through symbol and ISIN changes. It also records corporate actions and the price
   adjustments they cause (the `ref` layer).
3. It builds clean daily, weekly and monthly bars per listing, adjusted and unadjusted, and a
   daily market cap (the `clean` layer).
4. The **Observatory** web app lets a person decide what the rules couldn't, such as a possible
   merge or a price jump with no announced cause.

A shared Python library, `cagr`, has the indicators, metrics and tools every part uses.

## Pages

| Page | Read it to learn |
|---|---|
| [Overview](architecture/overview.md) | How the repository is laid out and how the parts fit together |
| [Data sources](data-model/sources.md) | Which NSE files we download and what is in them |
| [Database](data-model/database.md) | What each schema and table holds |
| [Trading calendar](data-model/calendar.md) | How we know which days NSE traded |
| [Listings and corporate actions](data-model/listings.md) | How a stock is followed through symbol and ISIN changes, and how price adjustments are decided |
| [Clean bars and market cap](data-model/clean-bars.md) | The bars to use for research, and shares outstanding |
| [Industry](data-model/industry.md) | Arush's 52 industries and how listings get one |
| [NSE daily pipeline](pipeline/nse-daily.md) | What runs each night, how a file gets into the database, and the build commands |
| [Data quality](pipeline/data-quality.md) | The checks on files, listings and bars |
| [Toolkit](toolkit/README.md) | The `cagr` library's functions, and the first shared features |
| [Observatory](observatory/README.md) | The web app: the review page, how to run it |
| [Running the platform](operations/running.md) | Settings, migrations, the pipeline, builds and backfills |
| [Development](operations/development.md) | Tests, lint, CI and conventions |

## Not built yet

These are not documented here: NSE index tiers (the table exists but nothing fills it), industry
review on the Observatory, the 52 industry indices, the feature lake, the lab's notebooks, and deployment to
the VM.

## Writing these pages

Every pull request that changes code updates these pages in the same pull request.

Readability comes first: someone new should get a page at a glance. Start with a short summary,
use plain words and tables, and add a diagram only when it explains something a table can't.
