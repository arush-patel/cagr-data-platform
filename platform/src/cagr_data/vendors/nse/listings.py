"""Parse NSE's listing snapshots: EQUITY_L.csv, SME_EQUITY_L.csv and symbolchange.csv.

These files have no date inside; the snapshot date is the day we fetched them. Raw keeps every
row as sent. Turning them into listings with valid-from and valid-to dates is spec 0002.

Format facts, from the source check:
- EQUITY_L: six header names start with a space; dates are DD-MON-YYYY.
- SME_EQUITY_L: header names use underscores, every line ends with a comma, dates have a
  two-digit year (22-Oct-26) and there is no market lot column.
- symbolchange: company names start with a space; dates are DD-MON-YYYY.
"""

from datetime import date
from typing import Literal

import polars as pl

from cagr_data.vendors.errors import FileShapeError
from cagr_data.vendors.nse.csvfile import body, cast, check_dates_parsed, header, parse_date, text

Board = Literal["main", "sme"]

EQUITY_LIST_SCHEMA: dict[str, pl.DataType] = {
    "snapshot_date": pl.Date(),
    "board": pl.String(),
    "symbol": pl.String(),
    "name": pl.String(),
    "series": pl.String(),
    "listing_date": pl.Date(),
    "paid_up_value": pl.Decimal(18, 4),
    "market_lot": pl.Int32(),
    "isin": pl.String(),
    "face_value": pl.Decimal(18, 4),
}

SYMBOL_CHANGE_SCHEMA: dict[str, pl.DataType] = {
    "snapshot_date": pl.Date(),
    "company_name": pl.String(),
    "old_symbol": pl.String(),
    "new_symbol": pl.String(),
    "change_date": pl.Date(),
}

# Header names after stripping spaces and reading "_" as a space, so both boards share names.
EQUITY_LIST_COLUMNS: dict[Board, tuple[str, ...]] = {
    "main": (
        "SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING", "PAID UP VALUE", "MARKET LOT",
        "ISIN NUMBER", "FACE VALUE",
    ),
    "sme": (
        "SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING", "PAID UP VALUE", "ISIN NUMBER",
        "FACE VALUE",
    ),
}  # fmt: skip
_LISTING_DATE_FORMAT: dict[Board, str] = {"main": "%d-%b-%Y", "sme": "%d-%b-%y"}

SYMBOL_CHANGE_COLUMNS = ("NAME OF THE COMPANY", "OLD SYMBOL", "NEW SYMBOL", "EFFECTIVE DATE")


def _require(frame: pl.DataFrame, columns: list[str], what: str) -> None:
    missing = frame.select(pl.any_horizontal(pl.col(columns).is_null())).to_series()
    if missing.any():
        raise FileShapeError(f"{what} rows without {', '.join(columns)}: {int(missing.sum())}")


def parse_equity_list(csv: bytes, board: Board, snapshot_date: date) -> pl.DataFrame:
    """Parse EQUITY_L.csv (board main) or SME_EQUITY_L.csv (board sme)."""
    names = [n.replace("_", " ") for n in header(csv)]
    if tuple(names) != EQUITY_LIST_COLUMNS[board]:
        raise FileShapeError(f"unexpected {board} equity list header: {header(csv)}")
    raw = body(csv, names)
    frame = raw.select(
        snapshot_date=pl.lit(snapshot_date, pl.Date),
        board=pl.lit(board),
        symbol=text("SYMBOL"),
        name=text("NAME OF COMPANY"),
        series=text("SERIES"),
        listing_date=parse_date("DATE OF LISTING", _LISTING_DATE_FORMAT[board]),
        paid_up_value=text("PAID UP VALUE"),
        market_lot=text("MARKET LOT") if board == "main" else pl.lit(None, pl.String),
        isin=text("ISIN NUMBER"),
        face_value=text("FACE VALUE"),
    )
    check_dates_parsed(frame, raw, {"listing_date": "DATE OF LISTING"})
    frame = cast(frame, EQUITY_LIST_SCHEMA)
    _require(frame, ["symbol"], "equity list")
    return frame


def parse_symbol_change(csv: bytes, snapshot_date: date) -> pl.DataFrame:
    """Parse symbolchange.csv; each snapshot holds the full history of changes."""
    names = header(csv)
    if tuple(names) != SYMBOL_CHANGE_COLUMNS:
        raise FileShapeError(f"unexpected symbol change header: {names}")
    raw = body(csv, names)
    frame = raw.select(
        snapshot_date=pl.lit(snapshot_date, pl.Date),
        company_name=text("NAME OF THE COMPANY"),
        old_symbol=text("OLD SYMBOL"),
        new_symbol=text("NEW SYMBOL"),
        change_date=parse_date("EFFECTIVE DATE", "%d-%b-%Y"),
    )
    check_dates_parsed(frame, raw, {"change_date": "EFFECTIVE DATE"})
    frame = cast(frame, SYMBOL_CHANGE_SCHEMA)
    _require(frame, ["old_symbol", "new_symbol", "change_date"], "symbol change")
    return frame


ETF_LIST_SCHEMA: dict[str, pl.DataType] = {
    "snapshot_date": pl.Date(),
    "symbol": pl.String(),
    "underlying": pl.String(),
    "security_name": pl.String(),
    "listing_date": pl.Date(),
    "market_lot": pl.Int32(),
    "isin": pl.String(),
    "face_value": pl.Decimal(18, 4),
    "etf_underlying": pl.String(),
    "underlying_key": pl.String(),
}

ETF_LIST_COLUMNS = (
    "Symbol", "Underlying Asset", "SecurityName", "DateofListing", "MarketLot", "ISINNumber",
    "FaceValue", "ETF Underlying", "Underlying Key",
)  # fmt: skip


def parse_etf_list(csv: bytes, snapshot_date: date) -> pl.DataFrame:
    """Parse eq_etfseclist.csv, NSE's list of ETFs (all with INF ISINs; none in EQUITY_L).
    Listing dates have a two-digit year (08-Jan-02)."""
    names = header(csv)
    if tuple(names) != ETF_LIST_COLUMNS:
        raise FileShapeError(f"unexpected ETF list header: {names}")
    raw = body(csv, names)
    frame = raw.select(
        snapshot_date=pl.lit(snapshot_date, pl.Date),
        symbol=text("Symbol"),
        underlying=text("Underlying Asset"),
        security_name=text("SecurityName"),
        listing_date=parse_date("DateofListing", "%d-%b-%y"),
        market_lot=text("MarketLot"),
        isin=text("ISINNumber"),
        face_value=text("FaceValue"),
        etf_underlying=text("ETF Underlying"),
        underlying_key=text("Underlying Key"),
    )
    check_dates_parsed(frame, raw, {"listing_date": "DateofListing"})
    frame = cast(frame, ETF_LIST_SCHEMA)
    _require(frame, ["symbol", "isin"], "ETF list")
    return frame
