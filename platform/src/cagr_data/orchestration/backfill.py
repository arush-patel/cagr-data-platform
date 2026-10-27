"""Backfill the NSE session assets over a date range, in this process (spec 0001, section 7.5).

    uv run python -m cagr_data.orchestration.backfill 2024-06-01 2024-08-31

Runs in the order the calendar needs: the session files day by day, then the holiday lists and
the calendar (which records what the archive proves before 2011), then the file_arrived check
day by day. Every partition is its own Dagster run, recorded in DAGSTER_HOME when it is set, so
they show in `dagster dev`. Reads .env from the current folder. At 1 request a second a year
takes about 12 minutes. Exits 1 if any partition failed, after listing them.

If a partition fails because the database is unreachable (the laptop's tunnel dropped), it waits
for the database to come back and runs that partition again, rather than failing every day left.
"""

import argparse
import os
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import date, timedelta

import dagster as dg
import psycopg
from dotenv import load_dotenv

from cagr_data.orchestration import assets
from cagr_data.orchestration.resources import PlatformDatabase

FILE_ASSETS = (assets.nse_cm_bhavcopy, assets.nse_index_close)
WAIT_POLL_S = 15.0
WAIT_LIMIT_S = 30 * 60.0
RETRIES = 3


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _failed_checks(result: dg.ExecuteInProcessResult) -> list[str]:
    return sorted(e.check_name for e in result.get_asset_check_evaluations() if not e.passed)


def database_reachable(resources: Mapping[str, object]) -> Callable[[], bool]:
    """A probe that opens and closes a connection to the platform database."""
    db = resources["db"]
    assert isinstance(db, PlatformDatabase)

    def probe() -> bool:
        try:
            with db.connect() as conn:
                conn.execute("SELECT 1")
        except psycopg.OperationalError:
            return False
        return True

    return probe


def wait_until(
    ready: Callable[[], bool],
    *,
    poll_s: float = WAIT_POLL_S,
    limit_s: float = WAIT_LIMIT_S,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Poll ready until it is true or limit_s has passed; returns whether it became true."""
    waited = 0.0
    while not ready():
        if waited >= limit_s:
            return False
        sleep(poll_s)
        waited += poll_s
    return True


def backfill(
    start: date,
    end: date,
    resources: Mapping[str, object],
    *,
    instance: dg.DagsterInstance | None = None,
    log: bool = True,
    reachable: Callable[[], bool] | None = None,
    wait: Callable[[Callable[[], bool]], bool] = wait_until,
) -> list[str]:
    """Returns one line per failed partition or step; empty when everything succeeded."""
    tags = {assets.RUN_KIND_TAG: "backfill", "cagr/backfill": f"{start}..{end}"}
    failures: list[str] = []
    reachable = reachable or database_reachable(resources)

    def materialize(
        selection: Sequence[dg.AssetsDefinition], label: str, key: str | None
    ) -> dg.ExecuteInProcessResult:
        for _ in range(RETRIES):
            result = dg.materialize(
                selection, partition_key=key, resources=dict(resources), instance=instance,
                tags=tags, raise_on_error=False,
            )  # fmt: skip
            if result.success or reachable():
                return result
            if log:
                print(f"{label}: database unreachable, waiting for it", flush=True)
            if not wait(reachable):
                return result
        return result

    def run(selection: Sequence[dg.AssetsDefinition], label: str, key: str | None) -> None:
        result = materialize(selection, label, key)
        status = "ok" if result.success else "FAILED"
        failed = _failed_checks(result)
        if not result.success:
            failures.append(f"{label}: {', '.join(failed) or 'step failed'}")
        if log:
            extra = f" (failed checks: {', '.join(failed)})" if failed else ""
            print(f"{label}: {status}{extra}", flush=True)

    for day in _days(start, end):
        run(FILE_ASSETS, f"{day} files", day.isoformat())
    run((assets.nse_holidays,), "holiday lists", None)
    run((assets.xnse_calendar,), "calendar", None)
    for day in _days(start, end):
        run((assets.nse_session_complete,), f"{day} file_arrived", day.isoformat())
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    if args.end < args.start:
        parser.error("end is before start")

    load_dotenv(".env")
    from cagr_data.orchestration.definitions import RESOURCES  # reads the environment

    instance = dg.DagsterInstance.get() if os.environ.get("DAGSTER_HOME") else None
    failures = backfill(args.start, args.end, RESOURCES, instance=instance)
    print(f"\n{len(failures)} failure(s)" + "".join(f"\n  {f}" for f in failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
