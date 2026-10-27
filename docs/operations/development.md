# Development and CI

## Workspace

The root `pyproject.toml` is a uv workspace (not itself a package) with two members, `platform/`
and `toolkit/`. One `uv.lock` pins every dependency for both. Shared tool settings live in the root:

| Tool | Setting |
|---|---|
| ruff | line length 100, target py312, rules E, F, W, I, UP, B, SIM, RUF; first-party `cagr_data`, `cagr` |
| mypy | `strict = true` over packages `cagr_data` and `cagr` |
| pytest | test paths `platform/tests`, `toolkit/tests`; marker `db` for tests that need Postgres |

Dev dependencies: `dagster-webserver`, `hypothesis`, `mypy`, `pytest`, `ruff`.

## Commands

```sh
uv sync                    # install everything into .venv
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # type-check
uv run pytest              # tests
```

## Tests

| Suite | Folder | Needs a database | Covers |
|---|---|---|---|
| Platform unit | `platform/tests/unit/` | no | parsers (against real NSE files in `fixtures/nse/`), URLs, HTTP client, storage, checks, calendar rules, feeds, orchestration wiring, backfill |
| Platform database | `platform/tests/db/` | yes | migrations, connections, ingest end to end, calendar storage, Dagster assets against Postgres |
| Toolkit | `toolkit/tests/` | `test_cagr_db.py` only | indicators, metrics, calendar, splits, features and look-ahead, layer rules |

The fixtures in `platform/tests/fixtures/nse/` are real NSE files covering each format variant:
legacy bhavcopy from 2010 and 2024, UDiFF from January and July 2024, `ind_close_all`, a holiday
list, both equity lists and the symbol-change file.

Database tests run only when `CAGR_TEST_DATABASE_URL` is set. That login must be able to create
roles and databases: the fixtures create the three platform roles if missing and a fresh
`cagr_migrate_test` database owned by `cagr_platform`, then apply every migration as
`cagr_platform`, the same way as on `cagr_test`.

```sh
CAGR_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
```

## CI

`.github/workflows/ci.yml` runs on every pull request and every push to `main`:

```mermaid
flowchart LR
    pr["pull request /<br/>push to main"] --> co["checkout"]
    co --> uv["setup uv,<br/>uv sync --locked"]
    uv --> lint["ruff check"]
    lint --> fmt["ruff format --check"]
    fmt --> mypy["mypy (strict)"]
    mypy --> test["pytest -ra<br/>with postgres:17 service"]
```

The Postgres 17 service container gives the database tests a real server
(`CAGR_TEST_DATABASE_URL=postgresql://cagr:cagr@localhost:5432/cagr_ci`), so migrations and
ingest are tested on every PR.

## Conventions

- One step per spec in `docs/specs/`; short branches; a pull request into `main` per task, with CI
  green before merge.
- Every database object comes from a new numbered migration in `db/migrations/`. Applied migrations
  are never edited; a fix is a new migration (as 0006 adjusts a check seeded by 0004).
- A new raw partition or table revokes the writer's default UPDATE, DELETE and TRUNCATE, as 0003
  does.
- Update `docs/` in the same pull request as the code it describes.
