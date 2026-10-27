from datetime import date

from cagr_data.vendors.nse.urls import build_url

LEGACY = (
    "https://nsearchives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/"
    "cm{DD}{MON}{YYYY}bhav.csv.zip"
)
UDIFF = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{YYYY}{MM}{DD}_F_0000.csv.zip"
INDEX = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{DD}{MM}{YYYY}.csv"


def test_legacy_url_pads_day_and_upper_cases_month() -> None:
    assert build_url(LEGACY, date(2010, 1, 4)) == (
        "https://nsearchives.nseindia.com/content/historical/EQUITIES/2010/JAN/cm04JAN2010bhav.csv.zip"
    )


def test_udiff_url() -> None:
    assert build_url(UDIFF, date(2024, 7, 8)).endswith(
        "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    )


def test_index_close_url_is_day_month_year() -> None:
    assert build_url(INDEX, date(2026, 10, 23)).endswith("ind_close_all_23102026.csv")


def test_template_without_placeholders_is_unchanged() -> None:
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    assert build_url(url, date(2026, 10, 26)) == url
