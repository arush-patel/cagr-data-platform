"""Records check outcomes in ops.dq_result, with each check's severity and params from
ops.dq_check.

Results are written in the caller's transaction. Commit before raising on a critical failure,
so a failed run still says why (spec 0001, section 6.1).
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg.types.json import Jsonb

from cagr_data.core.db import Connection
from cagr_data.quality.checks import Outcome


@dataclass(frozen=True)
class Check:
    check_id: str
    severity: str
    params: dict[str, Any]
    feed_id: str | None


class CriticalCheckError(Exception):
    def __init__(self, failed: Sequence[Outcome]) -> None:
        super().__init__("critical checks failed: " + ", ".join(o.check_id for o in failed))
        self.failed = list(failed)


def load_checks(conn: Connection, layer: str = "raw") -> dict[str, Check]:
    """Active checks of a layer, by id."""
    rows = conn.execute(
        "SELECT check_id, severity, params, feed_id FROM ops.dq_check WHERE layer = %s AND active",
        (layer,),
    ).fetchall()
    return {r[0]: Check(*r) for r in rows}


def record(
    conn: Connection,
    checks: dict[str, Check],
    *,
    run_id: int,
    day: date | None,
    outcomes: Iterable[Outcome],
) -> list[Outcome]:
    """Write one ops.dq_result row per outcome of an active check. Returns the critical
    failures."""
    critical: list[Outcome] = []
    with conn.cursor() as cur:
        for o in outcomes:
            check = checks.get(o.check_id)
            if check is None:
                continue  # inactive
            cur.execute(
                "INSERT INTO ops.dq_result (run_id, check_id, partition_date, passed, observed,"
                " detail) VALUES (%s, %s, %s, %s, %s, %s)",
                (run_id, o.check_id, day, o.passed, Jsonb(o.observed), o.detail),
            )
            if not o.passed and check.severity == "critical":
                critical.append(o)
    return critical


def raise_on_critical(failed: Sequence[Outcome]) -> None:
    if failed:
        raise CriticalCheckError(failed)
