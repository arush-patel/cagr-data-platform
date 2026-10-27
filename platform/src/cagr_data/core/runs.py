"""ops.run: one row per pipeline run, so every receipt and check result says which run made it."""

from typing import Literal

from cagr_data.core.db import Connection

RunKind = Literal["nightly", "backfill", "revision_check", "manual"]


def start_run(
    conn: Connection,
    kind: RunKind,
    *,
    code_version: str | None = None,
    dagster_run_id: str | None = None,
) -> int:
    row = conn.execute(
        "INSERT INTO ops.run (kind, code_version, dagster_run_id) VALUES (%s, %s, %s)"
        " RETURNING run_id",
        (kind, code_version, dagster_run_id),
    ).fetchone()
    assert row is not None
    return int(row[0])


def finish_run(
    conn: Connection, run_id: int, *, succeeded: bool, peak_mem_mb: int | None = None
) -> None:
    conn.execute(
        "UPDATE ops.run SET status = %s, finished_at = greatest(now(), started_at),"
        " peak_mem_mb = %s WHERE run_id = %s AND status = 'running'",
        ("succeeded" if succeeded else "failed", peak_mem_mb, run_id),
    )
