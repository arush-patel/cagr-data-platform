import io
import zipfile
from datetime import date
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError
from cagr_data.vendors.nse.bhavcopy import BAR_SCHEMA, parse_bhavcopy, parse_legacy, parse_udiff

UDIFF_OLD = "BhavCopy_NSE_CM_0_0_0_20240102_F_0000.csv.zip"
UDIFF = "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
LEGACY = "cm05JUL2024bhav.csv.zip"
LEGACY_2010 = "cm04JAN2010bhav.csv.zip"


def _parse(fixtures: Path, name: str, fmt: str, day: date) -> pl.DataFrame:
    return parse_bhavcopy((fixtures / name).read_bytes(), fmt, day)  # type: ignore[arg-type]


def _reliance(frame: pl.DataFrame) -> dict[str, object]:
    rows = frame.filter((pl.col("symbol") == "RELIANCE") & (pl.col("series") == "EQ")).to_dicts()
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize(
    ("name", "fmt", "day"),
    [
        (UDIFF_OLD, "udiff_v1", date(2024, 1, 2)),
        (UDIFF, "udiff_v1", date(2024, 7, 8)),
        (LEGACY, "legacy_v1", date(2024, 7, 5)),
        (LEGACY_2010, "legacy_v1", date(2010, 1, 4)),
    ],
)
def test_every_format_gives_the_same_schema(
    nse_fixtures: Path, name: str, fmt: str, day: date
) -> None:
    frame = _parse(nse_fixtures, name, fmt, day)
    assert frame.schema == pl.Schema(BAR_SCHEMA)
    assert frame.height > 10
    assert frame["trade_date"].unique().to_list() == [day]
    assert frame["source_format"].unique().to_list() == [fmt]
    assert frame.select(pl.struct("symbol", "series").is_duplicated().any()).item() is False


def test_udiff_current_header(nse_fixtures: Path) -> None:
    row = _reliance(_parse(nse_fixtures, UDIFF, "udiff_v1", date(2024, 7, 8)))
    assert row["isin"] == "INE002A01018"
    assert row["instrument_type"] == "STK"
    assert row["close"] == Decimal("3201.8000")
    assert row["prev_close"] == Decimal("3177.2500")
    assert row["volume"] == 4750403
    assert row["trade_count"] == 240527


def test_udiff_old_header_with_trailing_comma(nse_fixtures: Path) -> None:
    frame = _parse(nse_fixtures, UDIFF_OLD, "udiff_v1", date(2024, 1, 2))
    assert _reliance(frame)["close"] == Decimal("2611.7000")
    assert set(frame["series"]) >= {"EQ", "BE", "SM", "ST"}


def test_legacy_with_isin(nse_fixtures: Path) -> None:
    row = _reliance(_parse(nse_fixtures, LEGACY, "legacy_v1", date(2024, 7, 5)))
    assert row["isin"] == "INE002A01018"
    assert row["instrument_type"] is None
    assert row["trade_count"] == 261494


def test_legacy_2010_has_no_isin_or_trade_count_and_unpadded_dates(nse_fixtures: Path) -> None:
    frame = _parse(nse_fixtures, LEGACY_2010, "legacy_v1", date(2010, 1, 4))
    assert frame["isin"].null_count() == frame.height
    assert frame["trade_count"].null_count() == frame.height
    assert _reliance(frame)["close"] == Decimal("1075.5000")


def test_wrong_date_is_refused(nse_fixtures: Path) -> None:
    with pytest.raises(TradeDateMismatchError) as e:
        _parse(nse_fixtures, UDIFF, "udiff_v1", date(2024, 7, 9))
    assert e.value.found == [date(2024, 7, 8)]


def _zip(csv: str, name: str = "x.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, csv)
    return buf.getvalue()


def test_unknown_header_is_a_shape_error() -> None:
    with pytest.raises(FileShapeError):
        parse_legacy(b"SYMBOL,SERIES,OPEN,CLOSE\nA,EQ,1,2\n")
    with pytest.raises(FileShapeError):
        parse_udiff(b"TradDt,TckrSymb\n2024-07-08,A\n")


def test_value_that_is_not_a_number_is_a_shape_error() -> None:
    header = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    row = "A,EQ,1,2,1,abc,2,1,10,20,05-JUL-2024,"
    with pytest.raises(FileShapeError):
        parse_legacy(f"{header}\n{row}\n".encode())


def test_rows_wider_than_the_header_are_a_shape_error() -> None:
    header = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    row = "A,EQ,1,2,1,2,2,1,10,20,05-JUL-2024,surprise,"
    with pytest.raises(FileShapeError):
        parse_legacy(f"{header}\n{row}\n".encode())


def test_zip_must_hold_one_csv() -> None:
    with pytest.raises(FileShapeError):
        parse_bhavcopy(b"not a zip", "legacy_v1")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.csv", "x")
        zf.writestr("b.csv", "y")
    with pytest.raises(FileShapeError):
        parse_bhavcopy(buf.getvalue(), "legacy_v1")


def test_crlf_line_endings() -> None:
    header = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    row = "A,EQ,1,2,1,2,2,1,10,20,05-JUL-2024,"
    frame = parse_bhavcopy(_zip(f"{header}\r\n{row}\r\n"), "legacy_v1", date(2024, 7, 5))
    assert frame["close"].to_list() == [Decimal("2.0000")]


def test_a_last_price_of_zero_is_missing() -> None:
    # Legacy writes 0 on untraded bond rows; UDiFF leaves the field empty.
    header = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
    rows = ["A,NT,100,100,100,100,0,100,1,100,05-JUL-2024,", "B,EQ,1,2,1,2,2,1,10,20,05-JUL-2024,"]
    frame = parse_legacy("\n".join([header, *rows, ""]).encode())
    assert frame["last"].to_list() == [None, Decimal("2.0000")]


def test_a_placeholder_isin_is_missing() -> None:
    # 16 Feb 2021: the partly-paid rights shares (series E1) carry DUMMY for their ISIN.
    header = ("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
              "TOTALTRADES,ISIN,")  # fmt: skip
    rows = ["ABFRLPP1,E1,135.1,136,133.1,135.5,133.1,139.4,20362,2749956.3,16-FEB-2021,29,DUMMY,",
            "ABFRL,EQ,1,2,1,2,2,1,10,20,16-FEB-2021,5,INE647O01011,"]  # fmt: skip
    frame = parse_legacy("\n".join([header, *rows, ""]).encode(), date(2021, 2, 16))
    assert frame["isin"].to_list() == [None, "INE647O01011"]


def test_a_two_digit_year_without_a_trailing_comma() -> None:
    # 13 Jul 2020: TIMESTAMP is 13-Jul-20 and the lines have no trailing comma.
    header = ("SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,"
              "TOTALTRADES,ISIN")  # fmt: skip
    row = "20MICRONS,EQ,32.85,33.85,31.85,33.45,33.85,32.3,187303,6187285.7,13-Jul-20,1382,"
    row += "INE144J01027"
    frame = parse_legacy(f"{header}\n{row}\n".encode(), date(2020, 7, 13))
    assert frame["trade_date"].to_list() == [date(2020, 7, 13)]
    assert frame["trade_count"].to_list() == [1382]
