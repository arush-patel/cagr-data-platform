"""Parse NSE capital-market bhavcopy files (UDiFF and legacy) into one frame.

Both formats come out with the columns of raw.nse_cm_bar (without file_id), so the loader does
not care which one it got. Numbers stay exact (Decimal) and nothing is rounded or filtered: raw
keeps every series and instrument the file has.

Format facts, from the source check (docs/specs/0001-source-check.md):
- UDiFF (from 1 Jan 2024): until 20 Jun 2024 the reserved columns are Rsvd01..Rsvd04 and the
  header ends with a comma; from 21 Jun 2024 they are Rsvd1..Rsvd4.
- Legacy (2010 to 5 Jul 2024): no TOTALTRADES or ISIN columns until 21 Jun 2011. Every line
  ends with a comma. Dates look like 05-JUL-2024, or 4-JAN-2010 in 2010.
"""

import io
import zipfile
from datetime import date
from typing import Literal

import polars as pl

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError

Format = Literal["udiff_v1", "legacy_v1"]

PRICE = pl.Decimal(18, 4)
VALUE = pl.Decimal(22, 2)

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


def _header(csv: bytes) -> list[str]:
    first = csv.split(b"\n", 1)[0].decode("utf-8-sig").rstrip("\r")
    names = [n.strip() for n in first.split(",")]
    if names and names[-1] == "":  # a trailing comma
        names.pop()
    return names


def _body(csv: bytes, names: list[str]) -> pl.DataFrame:
    """Every field as text, named by position; the trailing empty field, if any, is dropped."""
    frame = pl.read_csv(
        io.BytesIO(csv),
        has_header=False,
        skip_rows=1,
        infer_schema=False,
        truncate_ragged_lines=True,
    )
    if frame.width < len(names):
        raise FileShapeError(f"rows have {frame.width} fields, header names {len(names)}")
    extra = frame.columns[len(names) :]
    if extra and frame.select(pl.any_horizontal(pl.col(extra).is_not_null())).to_series().any():
        raise FileShapeError(f"rows have {frame.width} fields, header names {len(names)}")
    return frame.drop(extra).rename(dict(zip(frame.columns, names, strict=False)))


def _text(name: str) -> pl.Expr:
    return pl.col(name).str.strip_chars().replace("", None)


def _cast(frame: pl.DataFrame) -> pl.DataFrame:
    try:
        return frame.select([pl.col(c).cast(t) for c, t in BAR_SCHEMA.items()])
    except pl.exceptions.InvalidOperationError as e:
        raise FileShapeError(f"a value does not fit its column: {e}") from e


def _check_date(frame: pl.DataFrame, expected: date | None) -> None:
    found = sorted(frame["trade_date"].drop_nulls().unique().to_list())
    if expected is not None and found != [expected] and frame.height:
        raise TradeDateMismatchError(expected, found)
    if frame["trade_date"].null_count():
        raise FileShapeError("rows without a trade date")


def parse_udiff(csv: bytes, expected_date: date | None = None) -> pl.DataFrame:
    names = [_UDIFF_OLD_RESERVED.get(n, n) for n in _header(csv)]
    if tuple(names) != UDIFF_COLUMNS:
        raise FileShapeError(f"unexpected UDiFF header: {names}")
    raw = _body(csv, names)
    frame = raw.select(
        trade_date=_text("TradDt").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
        symbol=_text("TckrSymb"),
        series=_text("SctySrs").fill_null(""),
        isin=_text("ISIN"),
        instrument_type=_text("FinInstrmTp"),
        open=_text("OpnPric"),
        high=_text("HghPric"),
        low=_text("LwPric"),
        close=_text("ClsPric"),
        last=_text("LastPric"),
        prev_close=_text("PrvsClsgPric"),
        volume=_text("TtlTradgVol"),
        traded_value_inr=_text("TtlTrfVal"),
        trade_count=_text("TtlNbOfTxsExctd"),
        source_format=pl.lit("udiff_v1"),
    )
    frame = _cast(frame)
    _check_date(frame, expected_date)
    return frame


def parse_legacy(csv: bytes, expected_date: date | None = None) -> pl.DataFrame:
    names = _header(csv)
    if tuple(names) not in (LEGACY_COLUMNS, LEGACY_COLUMNS_2010):
        raise FileShapeError(f"unexpected legacy header: {names}")
    raw = _body(csv, names)
    has_isin = "ISIN" in names
    frame = raw.select(
        trade_date=_text("TIMESTAMP").str.strptime(pl.Date, "%d-%b-%Y", strict=False),
        symbol=_text("SYMBOL"),
        series=_text("SERIES").fill_null(""),
        isin=_text("ISIN") if has_isin else pl.lit(None, pl.String),
        instrument_type=pl.lit(None, pl.String),
        open=_text("OPEN"),
        high=_text("HIGH"),
        low=_text("LOW"),
        close=_text("CLOSE"),
        last=_text("LAST"),
        prev_close=_text("PREVCLOSE"),
        volume=_text("TOTTRDQTY"),
        traded_value_inr=_text("TOTTRDVAL"),
        trade_count=_text("TOTALTRADES") if has_isin else pl.lit(None, pl.String),
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
