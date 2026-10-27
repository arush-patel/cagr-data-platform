"""Database fixtures.

CAGR_TEST_DATABASE_URL points at a server where the test login may create roles and databases
(the CI service, or a local Postgres). The fixtures create the three platform roles if missing,
a fresh database owned by cagr_platform, and apply every migration as cagr_platform, as on
cagr_test.
"""

import os
from collections.abc import Iterator
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from cagr_data.core import migrate

TEST_DB = "cagr_migrate_test"
PLATFORM_PASSWORD = "cagr_platform_test"  # test-only login, never used outside CI and laptops


def _with(url: str, *, user: str | None = None, password: str | None = None, db: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname or "localhost"
    netloc = f"{host}:{parts.port}" if parts.port else host
    if user:
        netloc = f"{user}:{password}@{netloc}" if password else f"{user}@{netloc}"
    elif parts.username:
        cred = f"{parts.username}:{parts.password}" if parts.password else parts.username
        netloc = f"{cred}@{netloc}"
    return urlunsplit((parts.scheme, netloc, "/" + db, parts.query, ""))


@pytest.fixture(scope="session")
def admin_url() -> str:
    url = os.environ.get("CAGR_TEST_DATABASE_URL")
    if not url:
        pytest.skip("CAGR_TEST_DATABASE_URL is not set")
    return url


def create_migrated(admin_url: str, name: str) -> tuple[str, str, list[str]]:
    """Drop and create database name, owned by cagr_platform, with every migration applied.

    Returns its admin URL, the owner's (cagr_platform) URL and the migration ids applied.
    """
    with psycopg.connect(admin_url, autocommit=True) as conn:
        roles = (("cagr_platform", True), ("cagr_writer", False), ("cagr_read", False),
                 ("cagr_reviewer", False))  # fmt: skip
        for role, login in roles:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone()
            if not exists:
                conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
            if login:
                conn.execute(
                    sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(
                        sql.Identifier(role), sql.Literal(PLATFORM_PASSWORD)
                    )
                )
        conn.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name))
        )
        conn.execute(sql.SQL("CREATE DATABASE {} OWNER cagr_platform").format(sql.Identifier(name)))
    with psycopg.connect(_with(admin_url, db=name), autocommit=True) as conn:
        # As on Azure, where azure_pg_admin owns public: the owner role cannot create there.
        conn.execute("ALTER SCHEMA public OWNER TO CURRENT_USER")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
    platform_url = _with(admin_url, user="cagr_platform", password=PLATFORM_PASSWORD, db=name)
    applied = migrate.apply(platform_url)
    return _with(admin_url, db=name), platform_url, applied


@pytest.fixture(scope="session")
def migrated_db(admin_url: str) -> tuple[str, str, list[str]]:
    """The shared test database; tests use it through `db`, in a transaction rolled back."""
    return create_migrated(admin_url, TEST_DB)


@pytest.fixture(scope="session")
def dagster_db(admin_url: str) -> str:
    """A second database for tests that commit (the Dagster assets do); its admin URL."""
    url, _, _ = create_migrated(admin_url, "cagr_dagster_test")
    return url


@pytest.fixture
def db(
    migrated_db: tuple[str, str, list[str]],
) -> Iterator[psycopg.Connection[tuple[object, ...]]]:
    """An admin connection to the migrated database, in a transaction that is rolled back."""
    url, _, _ = migrated_db
    with psycopg.connect(url) as conn:
        yield conn
        conn.rollback()
