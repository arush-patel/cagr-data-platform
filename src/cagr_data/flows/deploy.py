"""One Prefect deployment per active ref.feed row that has a flow, scheduled from the row itself.

uv run --extra flows python -m cagr_data.flows.deploy        # serve all XNSE deployments
"""

from __future__ import annotations

from prefect import serve

from cagr_data.core import db
from cagr_data.flows import xnse

FLOWS = {
    "xnse_holidays": xnse.calendar_sync,
    "xnse_listing_master": xnse.reference_sync,
    "xnse_corporate_actions": xnse.corporate_actions,
    "xnse_eod_bhavcopy": xnse.eod,
}


def main() -> None:
    with db.connect() as conn:
        feeds = conn.execute(
            "SELECT code, schedule_cron, schedule_tz FROM ref.feed WHERE active AND code = ANY(%s)",
            (list(FLOWS),),
        ).fetchall()
    deployments = [
        FLOWS[f["code"]].to_deployment(name=f["code"], cron=f["schedule_cron"], timezone=f["schedule_tz"])
        for f in feeds
    ]
    serve(*deployments)


if __name__ == "__main__":
    main()
