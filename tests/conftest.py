from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from cagr_data.core import db, migrate

TEST_URL = os.environ.get("CAGR_TEST_DATABASE_URL", "postgresql://cagr:cagr@localhost:54329/cagr_test")


def _db_available() -> bool:
    try:
        with psycopg.connect(TEST_URL, connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def test_url() -> str:
    if not _db_available():
        pytest.skip("test database not reachable: run `docker compose up -d db`")
    return TEST_URL


@pytest.fixture
def fresh_db(test_url: str) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """An empty database with every migration applied."""
    migrate.reset(test_url)
    migrate.migrate(test_url)
    with db.connect(test_url) as conn:
        yield conn


@pytest.fixture
def nse_files(tmp_path: Path) -> Path:
    from cagr_data import sample as nse_fixtures

    root = tmp_path / "nse"
    nse_fixtures.write_all(root)
    return root
