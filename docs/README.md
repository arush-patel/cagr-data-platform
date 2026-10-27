# Cagr data platform documentation

This folder documents what is **built and merged on `main`**. Plans and designs for future steps
live in [`specs/`](specs/); anything described here exists in code today. Where a spec and the code
differ, these pages follow the code.

## What exists today

| Area | Package / folder | Status | Docs |
|---|---|---|---|
| NSE daily market data (raw layer) | `platform/` (`cagr_data`) | Built (spec 0001) | [Pipeline](pipeline/nse-daily.md), [Data quality](pipeline/data-quality.md) |
| Database schemas `ops`, `ref`, `raw` | `db/migrations/` 0001 to 0006 | Built | [Database](data/database.md) |
| XNSE trading calendar | `cagr_data.reference.calendar` | Built | [Trading calendar](data/calendar.md) |
| Shared library `cagr` (first slice) | `toolkit/` (`cagr`) | Built (spec 0004, slice T1) | [Toolkit](toolkit/README.md) |
| CI | `.github/workflows/ci.yml` | Built | [Development](operations/development.md) |

Not built yet, so not documented here: listings and corporate actions (spec 0002), clean bars
(spec 0003), features materialised in a lake, the lab, the Observatory, deployment to the VM.

## Pages

1. [Architecture overview](architecture/overview.md): the monorepo, its packages, how they depend on
   each other, and where things run.
2. Data
   - [Data sources](data/sources.md): every NSE feed, its URL, format and coverage.
   - [Database](data/database.md): schemas, tables, views, partitions, roles and migrations.
   - [Trading calendar](data/calendar.md): how sessions are derived and numbered.
3. Pipeline
   - [NSE daily pipeline](pipeline/nse-daily.md): Dagster assets, schedules, fetch, ingest,
     receipts, revisions, storage and backfill.
   - [Data quality](pipeline/data-quality.md): the twelve raw-layer checks.
4. [Toolkit (`cagr`)](toolkit/README.md): indicators, metrics, calendar, splits, features and
   read access.
5. Operations
   - [Running the platform](operations/running.md): settings, migrations, Dagster, backfill.
   - [Development and CI](operations/development.md): workspace, tests, lint, type checks.

Diagrams are [Mermaid](https://mermaid.js.org/) and render on GitHub.

## Keeping these pages true

Update the page in the same pull request as the code it describes. A page that describes a plan
belongs in `specs/`, not here.
