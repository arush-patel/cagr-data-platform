"""Settings and read-only access to the platform database.

The toolkit only reads. Connections it opens are read-only sessions, whatever the login, and
the login it expects is a read role (cagr_read). Writing belongs to the platform's pipelines.
"""

from datetime import date
from typing import Any

import polars as pl
import psycopg
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from cagr.calendar import Calendar

Connection = psycopg.Connection[tuple[Any, ...]]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CAGR_", env_file=".env", extra="ignore")

    read_database_url: SecretStr | None = None
    """A read-only login (cagr_read) to the platform's database."""


def connect_read(url: str | None = None) -> Connection:
    """A read-only connection, with url or CAGR_READ_DATABASE_URL when url is None."""
    if url is None:
        secret = Settings().read_database_url
        if secret is None:
            raise RuntimeError("CAGR_READ_DATABASE_URL is not set")
        url = secret.get_secret_value()
    conn = psycopg.connect(url, autocommit=True)
    conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    return conn


def read_frame(conn: Connection, query: str, params: Any = None) -> pl.DataFrame:
    """Run a query and return its rows as a Polars frame (empty, with its columns, if none)."""
    with conn.cursor() as cur:
        cur.execute(query, params)
        columns = [c.name for c in cur.description or []]
        rows = cur.fetchall()
    if not rows:
        return pl.DataFrame({c: [] for c in columns})
    return pl.DataFrame(rows, schema=columns, orient="row")


def load_calendar(conn: Connection, venue: str = "XNSE") -> Calendar:
    """The venue's sessions from ref.calendar_session."""
    rows = conn.execute(
        "SELECT date FROM ref.calendar_session WHERE venue_id = %s AND is_session ORDER BY date",
        (venue,),
    ).fetchall()
    sessions: list[date] = [r[0] for r in rows]
    if not sessions:
        raise ValueError(f"ref.calendar_session has no sessions for {venue}")
    return Calendar(sessions, venue=venue)
