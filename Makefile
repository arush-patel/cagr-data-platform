.DEFAULT_GOAL := help
.PHONY: help db down migrate reset demo test lint fmt typecheck check psql

help:  ## List targets
	@grep -E '^[a-z]+:.*##' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-10s %s\n", $$1, $$2}'

db:  ## Start Postgres 17 (cagr_dev and cagr_test)
	docker compose up -d --wait db

down:  ## Stop Postgres (data is kept in the pgdata volume)
	docker compose down

migrate:  ## Apply pending migrations to cagr_dev
	uv run cagr migrate

reset:  ## Drop and rebuild the cagr_dev schema
	uv run cagr migrate --reset

demo: reset  ## Load the synthetic NSE files into a fresh cagr_dev
	uv run cagr demo

test:  ## Run the tests (the db ones need `make db`)
	uv run pytest -ra

lint:  ## Ruff lint and format check
	uv run ruff check .
	uv run ruff format --check .

fmt:  ## Format and fix what ruff can
	uv run ruff format .
	uv run ruff check --fix .

typecheck:  ## mypy, strict
	uv run mypy

check: lint typecheck test  ## Everything CI runs

psql:  ## Open psql on cagr_dev
	docker exec -it cagr-data-db psql -U cagr -d cagr_dev
