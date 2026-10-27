from datetime import date, datetime

import dagster as dg

from cagr_data.orchestration import assets
from cagr_data.orchestration.definitions import defs, nse_session_evening
from cagr_data.quality.checks import Outcome


def test_definitions_load() -> None:
    dg.Definitions.validate_loadable(defs)
    keys = {k.to_user_string() for k in defs.resolve_asset_graph().get_all_asset_keys()}
    assert keys == {
        "nse_cm_bhavcopy", "nse_index_close", "nse_holidays", "xnse_calendar",
        "nse_equity_list", "nse_sme_equity_list", "nse_symbol_change", "nse_session_complete",
        "nse_etf_list", "nse_corporate_actions",
    }  # fmt: skip


def test_partitions_are_every_calendar_day_in_ist() -> None:
    keys = assets.daily.get_partition_keys(
        current_time=datetime(2010, 1, 10, 12, tzinfo=assets.IST)
    )
    assert keys[0] == "2010-01-01"
    assert keys[-1] == "2010-01-10"  # today's partition exists, for the evening schedule
    assert len(keys) == 10


def test_evening_schedule_asks_for_todays_partition() -> None:
    at = datetime(2026, 10, 27, 19, 30, tzinfo=assets.IST)
    request = nse_session_evening(dg.build_schedule_context(scheduled_execution_time=at))
    assert isinstance(request, dg.RunRequest)
    assert request.partition_key == "2026-10-27"
    assert request.run_key == "2026-10-27T1930"


def test_deadline() -> None:
    day = date(2026, 10, 27)
    assert not assets.deadline_passed(day, datetime(2026, 10, 27, 22, 29, tzinfo=assets.IST))
    assert assets.deadline_passed(day, datetime(2026, 10, 27, 22, 30, tzinfo=assets.IST))
    assert assets.deadline_passed(day, datetime(2026, 10, 28, 1, 0, tzinfo=assets.IST))


def test_every_declared_check_gets_a_result() -> None:
    outcomes = [
        Outcome("file_shape", True),
        Outcome("no_duplicates", True, {"feed": 1}),
        Outcome("no_duplicates", False, {"feed": 2}),
    ]
    results = {
        r.check_name: r
        for r in assets._check_results(("file_shape", "no_duplicates", "row_count"), outcomes, "x")
    }
    assert results["file_shape"].passed
    assert not results["no_duplicates"].passed
    assert results["no_duplicates"].severity == dg.AssetCheckSeverity.ERROR
    assert results["row_count"].passed
    assert results["row_count"].metadata["evaluated"].value is False
