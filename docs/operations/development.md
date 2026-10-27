# Development

**In short:** run the four commands below before you push. CI runs the same four on every pull
request.

```sh
uv run ruff check .          # lint
uv run ruff format .         # format (this includes Python code blocks in .md files)
uv run mypy                  # type-check, strict
uv run pytest                # tests
```

## Tests

| Folder | Needs a database? | Covers |
|---|---|---|
| `platform/tests/unit/` | no | Parsers (on real NSE files), HTTP client, storage, checks, calendar, Dagster wiring |
| `platform/tests/db/` | yes | Migrations and loading files end to end |
| `toolkit/tests/` | only `test_cagr_db.py` | Every toolkit module |

Database tests run only when `CAGR_TEST_DATABASE_URL` is set. That login must be able to create
roles and databases, because the tests build a fresh database and apply every migration.

```sh
CAGR_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
```

## CI

`.github/workflows/ci.yml` runs lint, format check, mypy and pytest on every pull request and every
push to `main`, against a fresh Postgres 17. Merge only when it is green.

## Conventions

- One spec per step in `docs/specs/`, and one pull request per task.
- A schema change is a new numbered file in `db/migrations/`. Never edit one that has already run.
- Update `docs/` in the same pull request as the code it describes.
- Don't name a folder `data/` anywhere: `.gitignore` hides every such folder.
