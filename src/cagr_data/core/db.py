"""Database connections and the shared run log."""

from __future__ import annotations

import json
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg
from psycopg.rows import dict_row

from cagr_data.settings import get_settings

REPO = "cagr-data-platform"


def connect(url: str | None = None, *, autocommit: bool = True) -> psycopg.Connection[dict[str, Any]]:
    """Pipeline connections are autocommit; each unit of work opens its own `conn.transaction()`."""
    return psycopg.connect(url or get_settings().database_url, autocommit=autocommit, row_factory=dict_row)


@dataclass
class Run:
    """One row of public.run_log. Steps set rows_written and max_session as they go."""

    run_id: uuid.UUID
    runner: str
    rows_written: int = 0
    max_session: date | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_rows(self, n: int) -> None:
        self.rows_written += n

    def saw_session(self, d: date | None) -> None:
        if d is not None and (self.max_session is None or d > self.max_session):
            self.max_session = d


@contextmanager
def run_log(conn: psycopg.Connection[Any], runner: str, **metadata: Any) -> Iterator[Run]:
    """Record a step in public.run_log; the row is written in its own transaction so it survives a failure.

    The work itself runs in the caller's transaction on `conn`.
    """
    run = Run(run_id=uuid.uuid4(), runner=runner, metadata=dict(metadata))
    with psycopg.connect(conn.info.dsn, password=conn.info.password, autocommit=True) as log:
        log.execute(
            "INSERT INTO public.run_log (run_id, repo, runner, status, started_at, metadata) "
            "VALUES (%s, %s, %s, 'running', now(), %s)",
            (run.run_id, REPO, runner, json.dumps(metadata, default=str)),
        )
        # The run's own rows (raw.ingest_run, ops.dq_result …) reference run_log, so the row must be
        # visible to the caller's transaction before it commits.
        try:
            yield run
        except BaseException as exc:
            log.execute(
                "UPDATE public.run_log SET status = 'failed', ended_at = now(), error_type = %s, error_msg = %s, "
                "rows_written = %s, max_session = %s, metadata = %s WHERE run_id = %s",
                (
                    type(exc).__name__,
                    "".join(traceback.format_exception_only(exc)).strip()[:4000],
                    run.rows_written,
                    run.max_session,
                    json.dumps(run.metadata, default=str),
                    run.run_id,
                ),
            )
            raise
        log.execute(
            "UPDATE public.run_log SET status = 'success', ended_at = now(), rows_written = %s, "
            "max_session = %s, metadata = %s WHERE run_id = %s",
            (run.rows_written, run.max_session, json.dumps(run.metadata, default=str), run.run_id),
        )


def venue_id(conn: psycopg.Connection[Any], code: str) -> int:
    row = conn.execute("SELECT venue_id FROM ref.venue WHERE code = %s", (code,)).fetchone()
    if row is None:
        raise LookupError(f"unknown venue {code}")
    return int(row["venue_id"])


def feed(conn: psycopg.Connection[Any], code: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT f.*, v.code AS vendor_code, ve.code AS venue_code, ve.calendar_id "
        "FROM ref.feed f JOIN ref.vendor v USING (vendor_id) JOIN ref.venue ve USING (venue_id) "
        "WHERE f.code = %s",
        (code,),
    ).fetchone()
    if row is None:
        raise LookupError(f"unknown feed {code}")
    return dict(row)
