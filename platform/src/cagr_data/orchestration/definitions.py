"""The Dagster code location: `dagster dev -m cagr_data.orchestration.definitions`.

Environment: CAGR_PIPELINE_DATABASE_URL (the pipeline's login, cagr_writer; CAGR_DATABASE_URL is
the owner's, for migrations only) and CAGR_RAW_ROOT (the folder for original files). Times are
IST (spec 0001, section 5).
"""

import dagster as dg

from cagr_data.orchestration import assets
from cagr_data.orchestration.resources import NseClient, PlatformDatabase, RawStorage

session_job = dg.define_asset_job(
    "nse_session",
    selection=[assets.nse_cm_bhavcopy, assets.nse_index_close, assets.nse_session_complete],
    partitions_def=assets.daily,
)
calendar_job = dg.define_asset_job(
    "xnse_calendar_weekly", selection=[assets.nse_holidays, assets.xnse_calendar]
)
snapshot_job = dg.define_asset_job(
    "nse_snapshots",
    selection=[
        assets.nse_equity_list,
        assets.nse_sme_equity_list,
        assets.nse_symbol_change,
        assets.nse_etf_list,
        assets.nse_corporate_actions,
    ],
)


@dg.schedule(
    cron_schedule="0,30 19-22 * * *",
    job=session_job,
    execution_timezone="Asia/Kolkata",
)
def nse_session_evening(context: dg.ScheduleEvaluationContext) -> dg.RunRequest | dg.SkipReason:
    """19:00 to 22:30 IST every 30 minutes, for today's partition. A file already received is
    not fetched again, so later runs only fetch what is still missing; the 22:30 run decides
    file_arrived."""
    now = context.scheduled_execution_time.astimezone(assets.IST)
    if (now.hour, now.minute) > (22, 30):
        return dg.SkipReason("after the 22:30 deadline")
    key = now.date().isoformat()
    return dg.RunRequest(run_key=f"{key}T{now:%H%M}", partition_key=key)


calendar_schedule = dg.ScheduleDefinition(
    job=calendar_job, cron_schedule="0 7 * * 1", execution_timezone="Asia/Kolkata"
)
snapshot_schedule = dg.ScheduleDefinition(
    job=snapshot_job, cron_schedule="30 7 * * *", execution_timezone="Asia/Kolkata"
)

RESOURCES = {
    "db": PlatformDatabase(url=dg.EnvVar("CAGR_PIPELINE_DATABASE_URL")),
    "raw": RawStorage(root=dg.EnvVar("CAGR_RAW_ROOT")),
    "nse": NseClient(),
}

defs = dg.Definitions(
    assets=[
        assets.nse_cm_bhavcopy,
        assets.nse_index_close,
        assets.nse_holidays,
        assets.xnse_calendar,
        assets.nse_equity_list,
        assets.nse_sme_equity_list,
        assets.nse_symbol_change,
        assets.nse_etf_list,
        assets.nse_corporate_actions,
        assets.nse_session_complete,
    ],
    jobs=[session_job, calendar_job, snapshot_job],
    schedules=[nse_session_evening, calendar_schedule, snapshot_schedule],
    resources=RESOURCES,
)
