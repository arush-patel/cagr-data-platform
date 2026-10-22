from datetime import date

import pytest

from cagr_data import sample as nse_fixtures
from cagr_data.vendors import nse_archives


def test_bhavcopy_urls_switch_format_on_8_jul_2024() -> None:
    assert nse_archives.bhavcopy_file(date(2024, 7, 5)).url.endswith(
        "/EQUITIES/2024/JUL/cm05JUL2024bhav.csv.zip"
    )
    assert nse_archives.bhavcopy_file(date(2024, 7, 8)).url.endswith(
        "/content/cm/BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
    )


def test_udiff_parse_and_equity_filter() -> None:
    d = date(2026, 10, 1)
    content = nse_fixtures.udiff_zip(d, nse_fixtures.bhavcopy_rows(d, 0, {}))
    bhav = nse_archives.parse_bhavcopy(content, "udiff", expected_date=d)
    assert len(bhav) == 6
    eq = nse_archives.equity_rows(bhav, ("EQ", "BE"))
    assert set(eq["series"]) == {"EQ"}
    assert len(eq) == 5
    rel = eq[eq["symbol"] == "RELIANCE"].iloc[0]
    assert rel["isin"] == "INE002A01018"
    assert rel["volume"] == 100_000


def test_wrong_trade_date_is_refused() -> None:
    d = date(2026, 10, 1)
    content = nse_fixtures.udiff_zip(d, nse_fixtures.bhavcopy_rows(d, 0, {}))
    with pytest.raises(nse_archives.FileShapeError):
        nse_archives.parse_bhavcopy(content, "udiff", expected_date=date(2026, 10, 5))


def test_missing_column_is_refused() -> None:
    with pytest.raises(nse_archives.FileShapeError):
        nse_archives.parse_bhavcopy(b"TradDt,ISIN\n2026-10-01,INE002A01018\n", "udiff")


def test_legacy_parse_without_isin() -> None:
    csv = (
        b"SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,\n"
        b"RELIANCE,EQ,1000,1010,990,1005,1004,998,12345,12345678.9,04-JAN-2010,321,\n"
    )
    bhav = nse_archives.parse_bhavcopy(csv, "legacy", expected_date=date(2010, 1, 4))
    assert bhav.iloc[0]["isin"] == ""
    assert bhav.iloc[0]["close"] == 1005


def test_series_preference_picks_eq_over_be() -> None:
    import pandas as pd

    bhav = pd.DataFrame(
        {"isin": ["INE1", "INE1"], "symbol": ["A", "A"], "series": ["BE", "EQ"], "close": [1.0, 2.0]}
    )
    eq = nse_archives.equity_rows(bhav, ("EQ", "BE"))
    assert eq["series"].tolist() == ["EQ"]


def test_listing_master_and_symbol_changes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    nse_fixtures.write_all(tmp_path)
    masters = tmp_path / "nse_archives" / "masters" / f"{nse_fixtures.END:%Y%m%d}"
    m = nse_archives.parse_listing_master((masters / "EQUITY_L.csv").read_bytes())
    assert len(m) == 5 and m.iloc[0]["listed_on"] == date(1995, 11, 29)
    sc = nse_archives.parse_symbol_changes((masters / "symbolchange.csv").read_bytes())
    assert sc.iloc[0]["new_symbol"] == "XYZNEW" and sc.iloc[0]["changed_on"] == date(2026, 10, 14)
