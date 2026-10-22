"""XNSE flows. Each one only opens a connection and calls the pipeline step it is named after;
schedules come from ref.feed (see deploy.py), so no business logic or timing lives here.
"""

from __future__ import annotations

from datetime import date, timedelta

from prefect import flow, get_run_logger

from cagr_data import pipeline
from cagr_data.core import db


def _today() -> date:
    return date.today()


@flow(name="xnse-calendar-sync", retries=2, retry_delay_seconds=600)
def calendar_sync() -> int:
    with db.connect() as conn:
        return pipeline.calendar_sync(
            conn, pipeline.Fetchers.live(), date(2011, 1, 1), date(_today().year + 1, 12, 31)
        )


@flow(name="xnse-reference-sync", retries=2, retry_delay_seconds=600)
def reference_sync() -> dict[str, object]:
    with db.connect() as conn:
        return pipeline.reference_sync(conn, pipeline.Fetchers.live(), _today())


@flow(name="xnse-corporate-actions", retries=2, retry_delay_seconds=900)
def corporate_actions() -> dict[str, object]:
    t = _today()
    with db.connect() as conn:
        return pipeline.corporate_actions_sync(
            conn, pipeline.Fetchers.live(), t - timedelta(days=7), t + timedelta(days=30)
        )


# The bhavcopy appears in the evening; retry every 15 minutes until 22:30 IST (D1 cadence).
@flow(name="xnse-eod", retries=14, retry_delay_seconds=900)
def eod(session: date | None = None) -> dict[str, object]:
    s = session or _today()
    log = get_run_logger()
    with db.connect() as conn:
        res = pipeline.eod(conn, pipeline.Fetchers.live(), s, s, nightly=True)
    if res.missing:
        raise RuntimeError(f"bhavcopy for {s} not published yet")
    log.info("clean rows %s, checks %s", res.clean_rows, res.checks)
    return res.__dict__
