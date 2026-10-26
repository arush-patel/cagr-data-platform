import os

import psycopg
import pytest

pytestmark = pytest.mark.db


@pytest.fixture
def database_url() -> str:
    url = os.environ.get("CAGR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CAGR_TEST_DATABASE_URL is not set")
    return url


def test_postgres_17(database_url: str) -> None:
    with psycopg.connect(database_url) as conn:
        row = conn.execute("SHOW server_version_num").fetchone()
    assert row is not None
    assert int(row[0]) >= 170000
