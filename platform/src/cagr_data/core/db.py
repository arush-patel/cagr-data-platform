"""Connections to the platform database (psycopg 3)."""

from typing import Any

import psycopg

from cagr_data.core.settings import Settings

Connection = psycopg.Connection[tuple[Any, ...]]


def connect(url: str | None = None, *, autocommit: bool = False) -> Connection:
    """Connect with url, or with CAGR_DATABASE_URL when url is None."""
    if url is None:
        secret = Settings().database_url
        if secret is None:
            raise RuntimeError("CAGR_DATABASE_URL is not set")
        url = secret.get_secret_value()
    return psycopg.connect(url, autocommit=autocommit)
