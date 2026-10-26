# cagr-data-platform

One repository for the Cagr trading data platform: data pipelines, reference data, features,
the research lab, the Observatory and the `cagr` toolkit. It is being built step by step;
see `docs/specs/` for what each step does.

## What is here so far

- `platform/`: the data platform package `cagr-data` (Python).
- `docs/specs/`: one spec per step. Step one is `0001-nse-daily-market-data.md`.

## Working on it

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12.

```sh
uv sync                    # create .venv with every workspace package
uv run ruff check .        # lint
uv run ruff format .       # format
uv run mypy                # type-check
uv run pytest              # tests; database tests run when CAGR_TEST_DATABASE_URL is set
```

Settings come from `CAGR_*` environment variables or a `.env` file (never committed):
`CAGR_DATABASE_URL` for the platform database and `CAGR_RAW_ROOT` for where original vendor
files are kept.
