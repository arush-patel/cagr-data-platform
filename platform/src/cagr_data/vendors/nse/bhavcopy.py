"""Parse NSE capital-market bhavcopy files (UDiFF and legacy) into one frame.

Both formats come out with the columns of raw.nse_cm_bar (without file_id), so the loader does
not care which one it got. Numbers stay exact (Decimal) and nothing is rounded or filtered: raw
keeps every series and instrument the file has.

Format facts, from the source check (docs/specs/0001-source-check.md):
- UDiFF (from 1 Jan 2024): until 20 Jun 2024 the reserved columns are Rsvd01..Rsvd04 and the
  header ends with a comma; from 21 Jun 2024 they are Rsvd1..Rsvd4.
- Legacy (2010 to 5 Jul 2024): no TOTALTRADES or ISIN columns until 21 Jun 2011. Every line
  ends with a comma. Dates look like 05-JUL-2024, or 4-JAN-2010 in 2010.
- An instrument without an ISIN of its own (the partly-paid E1 series of a rights issue, Feb 2021)
  can carry the placeholder DUMMY. Anything that is not an ISIN is read as missing; the stored
  file keeps what NSE sent.
"""

import io
import zipfile
from datetime import date
from typing import Literal

import polars as pl

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError
from cagr_data.vendors.nse.csvfile import body, cast, header, text

Format = Literal["udiff_v1", "legacy_v1"]

PRICE = pl.Decimal(18, 4)
VALUE = pl.Decimal(22, 2)
ISIN = r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"  # as raw.nse_cm_bar checks it

BAR_SCHEMA: dict[str, pl.DataType] = {
    "trade_date": pl.Date(),
    "symbol": pl.String(),
    "series": pl.String(),
    "isin": pl.String(),
    "instrument_type": pl.String(),
    "open": PRICE,
    "high": PRICE,
    "low": PRICE,
    "close": PRICE,
    "last": PRICE,
    "prev_close": PRICE,
    "volume": pl.Int64(),
    "traded_value_inr": VALUE,
    "trade_count": pl.Int64(),
    "source_format": pl.String(),
}

UDIFF_COLUMNS = (
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb", "SctySrs",
    "XpryDt", "FininstrmActlXpryDt", "StrkPric", "OptnTp", "FinInstrmNm", "OpnPric", "HghPric",
    "LwPric", "ClsPric", "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst",
    "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty",
    "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
)  # fmt: skip
_UDIFF_OLD_RESERVED = {"Rsvd01": "Rsvd1", "Rsvd02": "Rsvd2", "Rsvd03": "Rsvd3", "Rsvd04": "Rsvd4"}

LEGACY_COLUMNS_2010 = (
    "SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "TOTTRDQTY",
    "TOTTRDVAL", "TIMESTAMP",
)  # fmt: skip
LEGACY_COLUMNS = (*LEGACY_COLUMNS_2010, "TOTALTRADES", "ISIN")


def read_zip(content: bytes) -> bytes:
    """The single CSV inside a bhavcopy zip."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = zf.namelist()
            if len(names) != 1:
                raise FileShapeError(f"expected one file in the zip, found {names}")
            return zf.read(names[0])
    except zipfile.BadZipFile as e:
        raise FileShapeError(f"not a zip file: {e}") from e


def _cast(frame: pl.DataFrame) -> pl.DataFrame:
    # Legacy writes a last price of 0 on untraded bond rows where UDiFF leaves it empty.
    frame = cast(frame, BAR_SCHEMA)
    return frame.with_columns(
        last=pl.when(pl.col("last") == 0).then(None).otherwise(pl.col("last"))
    )


def _check_date(frame: pl.DataFrame, expected: date | None) -> None:
    found = sorted(frame["trade_date"].drop_nulls().unique().to_list())
    if expected is not None and found != [expected] and frame.height:
        raise TradeDateMismatchError(expected, found)
    if frame["trade_date"].null_count():
        raise FileShapeError("rows without a trade date")


def _isin(name: str) -> pl.Expr:
    value = text(name)
    return pl.when(value.str.contains(ISIN)).then(value).otherwise(None)


def parse_udiff(csv: bytes, expected_date: date | None = None) -> pl.DataFrame:
    names = [_UDIFF_OLD_RESERVED.get(n, n) for n in header(csv)]
    if tuple(names) != UDIFF_COLUMNS:
        raise FileShapeError(f"unexpected UDiFF header: {names}")
    raw = body(csv, names)
    frame = raw.select(
        trade_date=text("TradDt").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        symbol=text("TckrSymb"),
        series=text("SctySrs").fill_null(""),
        isin=_isin("ISIN"),
        instrument_type=text("FinInstrmTp"),
        open=text("OpnPric"),
        high=text("HghPric"),
        low=text("LwPric"),
        close=text("ClsPric"),
        last=text("LastPric"),
        prev_close=text("PrvsClsgPric"),
        volume=text("TtlTradgVol"),
        traded_value_inr=text("TtlTrfVal"),
        trade_count=text("TtlNbOfTxsExctd"),
        source_format=pl.lit("udiff_v1"),
    )
    frame = _cast(frame)
    _check_date(frame, expected_date)
    return frame


def parse_legacy(csv: bytes, expected_date: date | None = None) -> pl.DataFrame:
    names = header(csv)
    if tuple(names) not in (LEGACY_COLUMNS, LEGACY_COLUMNS_2010):
        raise FileShapeError(f"unexpected legacy header: {names}")
    raw = body(csv, names)
    has_isin = "ISIN" in names
    frame = raw.select(
        trade_date=text("TIMESTAMP").str.strptime(pl.Date, "%d-%b-%Y", strict=False),
        symbol=text("SYMBOL"),
        series=text("SERIES").fill_null(""),
        isin=_isin("ISIN") if has_isin else pl.lit(None, pl.String),
        instrument_type=pl.lit(None, pl.String),
        open=text("OPEN"),
        high=text("HIGH"),
        low=text("LOW"),
        close=text("CLOSE"),
        last=text("LAST"),
        prev_close=text("PREVCLOSE"),
        volume=text("TOTTRDQTY"),
        traded_value_inr=text("TOTTRDVAL"),
        trade_count=text("TOTALTRADES") if has_isin else pl.lit(None, pl.String),
        source_format=pl.lit("legacy_v1"),
    )
    frame = _cast(frame)
    _check_date(frame, expected_date)
    return frame


def parse_bhavcopy(content: bytes, fmt: Format, expected_date: date | None = None) -> pl.DataFrame:
    """Parse a downloaded bhavcopy zip in the given format."""
    csv = read_zip(content)
    if fmt == "udiff_v1":
        return parse_udiff(csv, expected_date)
    if fmt == "legacy_v1":
        return parse_legacy(csv, expected_date)
    raise ValueError(f"not a bhavcopy format: {fmt}")
