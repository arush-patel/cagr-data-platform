# Development

**In short:** run the four Python commands below before you push. If you changed the web UI, run
the UI checks too. CI runs both sets on every pull request.

```sh
uv run ruff check .          # lint
uv run ruff format .         # format (this includes Python code blocks in .md files)
uv run mypy                  # type-check, strict
uv run pytest                # tests
```

For the web UI:

```sh
cd observatory/web
npm run typecheck && npm run lint && npm test && npm run build
```

## Tests

| Folder | Needs a database? | Covers |
|---|---|---|
| `platform/tests/unit/` | no | Parsers (on real NSE files), HTTP client, storage, checks, calendar, listing and adjustment rules, share counts, Dagster wiring |
| `platform/tests/db/` | yes | Migrations, loading files, listings, corporate actions, clean bars, industry seed, review decisions |
| `toolkit/tests/` | only `test_cagr_db.py` and `test_cagr_data.py` | Every toolkit module, and its reads against the real schema |
| `lab/tests/` | no | Every shared lab feature passes the look-ahead check |
| `observatory/api/tests/` | `test_observatory_review_db.py` | The API and the review endpoints |
| `observatory/web/src/` (`*.test.tsx`) | no | The UI |

Database tests run only when `CAGR_TEST_DATABASE_URL` is set. That login must be able to create
roles and databases, because the tests build a fresh database and apply every migration.

```sh
CAGR_TEST_DATABASE_URL=postgresql://postgres:postgres@localhost:5432/postgres uv run pytest
```

## CI

`.github/workflows/ci.yml` has two jobs, run on every pull request and every push to `main`:

| Job | Runs |
|---|---|
| `platform` | Lint, format check, mypy and pytest, against a fresh Postgres 17 |
| `observatory-web` | Checks the UI's API types are up to date, then typecheck, lint, tests and build |

Merge only when both are green.

## Conventions

- One spec per step in `docs/specs/`, and one pull request per task.
- **Every pull request that changes code updates `docs/` in the same pull request.**
- A schema change is a new numbered file in `db/migrations/`. Never edit one that has already run.
- Don't name a folder `data/` anywhere: `.gitignore` hides every such folder.
