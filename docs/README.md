# Documentation

These pages describe what is **built and merged on `main`**. Plans for future steps live in
[`specs/`](specs/). If a spec and the code disagree, these pages follow the code.

## What the platform does today

Every evening it downloads NSE's daily market files, keeps the original files, loads every row into
Postgres exactly as NSE sent it, checks the data, and keeps a trading calendar. A shared Python
library, `cagr`, has indicators, performance metrics and other tools that every part of the
platform will use.

## Pages

| Page | Read it to learn |
|---|---|
| [Overview](architecture/overview.md) | How the repository is laid out and how the parts fit together |
| [Data sources](data-model/sources.md) | Which NSE files we download and what is in them |
| [Database](data-model/database.md) | What each schema and table holds |
| [Trading calendar](data-model/calendar.md) | How we know which days NSE traded |
| [NSE daily pipeline](pipeline/nse-daily.md) | What runs each night, and how a file gets from NSE into the database |
| [Data quality](pipeline/data-quality.md) | The checks run on every file |
| [Toolkit](toolkit/README.md) | The `cagr` library's functions |
| [Running the platform](operations/running.md) | Settings, migrations, the pipeline and backfills |
| [Development](operations/development.md) | Tests, lint, CI and conventions |

## Not built yet

These are not documented here: listings and corporate actions (spec 0002), adjusted bars
(spec 0003), the feature lake, the lab, the Observatory, and deployment to the VM.

## Writing these pages

Readability comes first: someone new should get a page at a glance. Start with a short summary,
use plain words and tables, and add a diagram only when it explains something a table can't.
Update a page in the same pull request as the code it describes.
