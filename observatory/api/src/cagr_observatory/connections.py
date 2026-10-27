"""Opening database connections, with a plain-words error when the database can't be reached."""

from collections.abc import Callable

import psycopg
from fastapi import HTTPException

from cagr.db import Connection


def unreachable(exc: Exception) -> str:
    """Why a connection failed, in words: a missing setting, or the server's first line."""
    if isinstance(exc, psycopg.OperationalError):
        first = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
        return f"The database can't be reached: {first}"
    return str(exc) or type(exc).__name__


def open_or_503(connect: Callable[[], Connection]) -> Connection:
    """connect(), or a 503 that says why, so the page shows the reason instead of a 500."""
    try:
        return connect()
    except (RuntimeError, psycopg.OperationalError) as exc:
        raise HTTPException(503, unreachable(exc)) from exc
