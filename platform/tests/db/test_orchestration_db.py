"""The Dagster assets end to end, against their own database (they commit) and a fake NSE."""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import dagster as dg
import httpx
import psycopg
import pytest

from cagr_data.orchestration import assets
from cagr_data.orchestration.backfill import backfill
from cagr_data.orchestration.resources import NseClient, PlatformDatabase, RawStorage
from cagr_data.vendors.http import VendorClient

pytestmark = pytest.mark.db

FIXTURES = Path(__file__).parents[1] / "fixtures" / "nse"
SERVED = {
    "/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip": (
        "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    ),
    "/content/indices/ind_close_all_23102026.csv": "ind_close_all_23102026.csv",
}
REQUESTS: list[str] = []


def _nse(request: httpx.Request) -> httpx.Response:
    REQUESTS.append(request.url.path)
    if request.url.path == "/api/holiday-master":
        if request.url.params.get("year") == "2026":
            return httpx.Response(
                200, content=(FIXTURES / "holiday-master_trading_2026.json").read_bytes()
            )
        return httpx.Response(200, content=b"{}")
    name = SERVED.get(request.url.path)
    if name is None:
        return httpx.Response(404, text="<html>not found</html>")
    return httpx.Response(200, content=(FIXTURES / name).read_bytes())


class FakeNse(NseClient):
    def client(self) -> VendorClient:
        return VendorClient(1000, transport=httpx.MockTransport(_nse), attempts=1)


@pytest.fixture
def resources(dagster_db: str, tmp_path: Path) -> Iterator[dict[str, object]]:
    REQUESTS.clear()
    yield {
        "db": PlatformDatabase(url=dagster_db),
        "raw": RawStorage(root=str(tmp_path / "raw")),
        "nse": FakeNse(),
    }
    with psycopg.connect(dagster_db, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE ops.dq_result, raw.nse_cm_bar, raw.nse_index_close, raw.nse_holiday,"
            " raw.nse_equity_list, raw.nse_symbol_change, ref.calendar_session,"
            " ref.calendar_exception, raw.vendor_file, raw.fetch_attempt, ops.run CASCADE"
        )


def _checks(result: dg.ExecuteInProcessResult) -> dict[str, bool]:
    return {e.check_name: e.passed for e in result.get_asset_check_evaluations()}


def test_bhavcopy_partition_then_rerun(resources: dict[str, object], dagster_db: str) -> None:
    result = dg.materialize([assets.nse_cm_bhavcopy], partition_key="2024-07-08",
                            resources=resources)  # fmt: skip
    assert result.success
    checks = _checks(result)
    assert all(checks.values()) and len(checks) == len(assets.BAR_CHECKS)
    assert REQUESTS == ["/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"]

    again = dg.materialize([assets.nse_cm_bhavcopy], partition_key="2024-07-08",
                           resources=resources)  # fmt: skip
    assert again.success
    assert len(REQUESTS) == 1  # already received: NSE is not asked again
    with psycopg.connect(dagster_db) as conn:
        runs = conn.execute("SELECT kind, status FROM ops.run ORDER BY run_id").fetchall()
        files = conn.execute("SELECT count(*) FROM raw.vendor_file").fetchone()
    assert runs == [("nightly", "succeeded"), ("nightly", "succeeded")]
    assert files == (1,)


def test_a_weekend_404_is_fine(resources: dict[str, object], dagster_db: str) -> None:
    result = dg.materialize([assets.nse_cm_bhavcopy, assets.nse_index_close],
                            partition_key="2026-10-24", resources=resources)  # fmt: skip
    assert result.success
    with psycopg.connect(dagster_db) as conn:
        attempts = conn.execute(
            "SELECT feed_id, http_status FROM raw.fetch_attempt ORDER BY feed_id"
        ).fetchall()
    assert attempts == [("nse_cm_bhavcopy_udiff", 404), ("nse_index_close", 404)]


def test_missing_index_file_fails_the_session(
    resources: dict[str, object], dagster_db: str
) -> None:
    assert dg.materialize([assets.nse_cm_bhavcopy], partition_key="2024-07-08",
                          resources=resources).success  # fmt: skip
    assert dg.materialize([assets.xnse_calendar], resources=resources).success
    result = dg.materialize(
        [assets.nse_session_complete], partition_key="2024-07-08", resources=resources,
        raise_on_error=False,
    )  # fmt: skip
    assert not result.success
    assert _checks(result) == {"file_arrived": False}
    with psycopg.connect(dagster_db) as conn:
        recorded = conn.execute(
            "SELECT passed, observed->'missing' FROM ops.dq_result WHERE check_id = 'file_arrived'"
        ).fetchall()
    assert recorded == [(False, ["nse_index_close"])]


def test_index_file_with_wrong_date_is_critical(
    resources: dict[str, object], dagster_db: str
) -> None:
    SERVED["/content/indices/ind_close_all_22102026.csv"] = "ind_close_all_23102026.csv"
    try:
        result = dg.materialize([assets.nse_index_close], partition_key="2026-10-22",
                                resources=resources, raise_on_error=False)  # fmt: skip
    finally:
        del SERVED["/content/indices/ind_close_all_22102026.csv"]
    assert not result.success
    assert _checks(result)["trade_date_matches"] is False
    with psycopg.connect(dagster_db) as conn:
        assert conn.execute("SELECT count(*) FROM raw.vendor_file").fetchone() == (0,)
        status = conn.execute("SELECT status FROM ops.run").fetchall()
    assert status == [("failed",)]


def test_archive_proof_for_2010(resources: dict[str, object], dagster_db: str) -> None:
    with psycopg.connect(dagster_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO raw.fetch_attempt (feed_id, partition_date, url, http_status) VALUES"
            " ('nse_cm_bhavcopy_legacy', '2010-01-05', 'u', 404)"
        )
        for i, day in enumerate((date(2010, 1, 4), date(2010, 1, 6))):
            conn.execute(
                "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes,"
                " storage_uri, row_count) VALUES ('nse_cm_bhavcopy_legacy', %s, 'u', %s, 1,"
                " 'file:///x', 1)",
                (day, f"{i:064x}"),
            )
    assert dg.materialize([assets.xnse_calendar], resources=resources).success
    with psycopg.connect(dagster_db) as conn:
        day = conn.execute(
            "SELECT is_session, reason FROM ref.calendar_session WHERE date = '2010-01-05'"
        ).fetchone()
    assert day == (False, "holiday")


def test_holiday_lists_from_2011(resources: dict[str, object], dagster_db: str) -> None:
    assert dg.materialize([assets.nse_holidays], resources=resources).success
    years = range(2011, assets.today_ist().year + 2)
    assert len(REQUESTS) == len(years)
    assert dg.materialize([assets.nse_holidays], resources=resources).success
    assert len(REQUESTS) == len(years) + 2  # past years are not fetched again
    with psycopg.connect(dagster_db) as conn:
        files = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE row_count > 0) FROM raw.vendor_file"
            " WHERE feed_id = 'nse_holidays'"
        ).fetchone()
        holidays = conn.execute(
            "SELECT count(*) FROM ref.calendar_exception WHERE kind = 'holiday'"
        ).fetchone()
    assert files == (len(years), 1)
    assert holidays == (20,)


def test_backfill_a_short_range(resources: dict[str, object], dagster_db: str) -> None:
    failures = backfill(date(2024, 7, 6), date(2024, 7, 8), resources, log=False)
    # Sat 6 and Sun 7 Jul: 404s, not sessions. Mon 8 Jul: the bhavcopy arrives, the index
    # file does not (the fake NSE has none), so only file_arrived fails.
    assert failures == ["2024-07-08 file_arrived: file_arrived"]
    with psycopg.connect(dagster_db) as conn:
        kinds = conn.execute("SELECT DISTINCT kind FROM ops.run").fetchall()
        sessions = conn.execute(
            "SELECT date FROM ref.calendar_session WHERE is_session"
            " AND date BETWEEN '2024-07-06' AND '2024-07-08'"
        ).fetchall()
    assert set(kinds) <= {("backfill",), ("manual",)}
    assert sessions == [(date(2024, 7, 8),)]
