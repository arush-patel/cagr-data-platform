from datetime import date

from cagr_data.raw.feeds import Feed, stored_name


def _feed(fmt: str, cadence: str) -> Feed:
    return Feed("f", "nse_archives", "d", fmt, "", cadence)


def test_stored_names() -> None:
    day = date(2026, 10, 27)
    assert (
        stored_name(
            _feed("udiff_v1", "session"),
            day,
            "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_20261027_F_0000.csv.zip",
        )
        == "BhavCopy_NSE_CM_0_0_0_20261027_F_0000.csv.zip"
    )
    assert (
        stored_name(
            _feed("equity_l_v1", "daily"),
            day,
            "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv",
        )
        == "EQUITY_L_20261027.csv"
    )
    assert (
        stored_name(
            _feed("holiday_master_v1", "weekly"),
            date(2026, 1, 1),
            "https://www.nseindia.com/api/holiday-master?type=trading&year=2026",
        )
        == "holiday-master_trading_2026.json"
    )
