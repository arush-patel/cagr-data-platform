"""Parse NSE's daily index closes (ind_close_all_DDMMYYYY.csv) into raw.nse_index_close rows.

The header has been the same since the first file (21 Feb 2012). Index names are kept as sent:
they change over the years (S&P CNX Nifty, then Nifty 50) and mapping them to a stable index is a
later spec. The date is DD-MM-YYYY, or DD/MM/YYYY in some years (from Jun 2014); both are read.
An index repeated with identical values is kept once. Volume is a whole number, written with a
zero fraction (179982752.00) in some files around 2015. Missing values are "-" (a points index has
no open, high, low or valuation ratios). Turnover is in crores, as the file says; the loader does
not convert it.
"""

from datetime import date

import polars as pl

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError
from cagr_data.vendors.nse.csvfile import body, cast, check_dates_parsed, header, text

PRICE = pl.Decimal(18, 4)

INDEX_CLOSE_SCHEMA: dict[str, pl.DataType] = {
    "trade_date": pl.Date(),
    "index_name": pl.String(),
    "open": PRICE,
    "high": PRICE,
    "low": PRICE,
    "close": PRICE,
    "points_change": PRICE,
    "pct_change": pl.Decimal(10, 4),
    "volume": pl.Int64(),
    "turnover_inr": pl.Decimal(22, 2),
    "pe": pl.Decimal(12, 4),
    "pb": pl.Decimal(12, 4),
    "div_yield": pl.Decimal(10, 4),
}

INDEX_CLOSE_COLUMNS = (
    "Index Name", "Index Date", "Open Index Value", "High Index Value", "Low Index Value",
    "Closing Index Value", "Points Change", "Change(%)", "Volume", "Turnover (Rs. Cr.)", "P/E",
    "P/B", "Div Yield",
)  # fmt: skip


def parse_index_close(csv: bytes, expected_date: date | None = None) -> pl.DataFrame:
    """Parse one ind_close_all file. The date inside must equal expected_date when given."""
    names = header(csv)
    if tuple(names) != INDEX_CLOSE_COLUMNS:
        raise FileShapeError(f"unexpected index close header: {names}")
    raw = body(csv, names)
    frame = raw.select(
        # 09-06-2014 in most years, 09/06/2014 in some (from Jun 2014).
        trade_date=text("Index Date")
        .str.replace_all("/", "-")
        .str.strptime(pl.Date, "%d-%m-%Y", strict=False),
        index_name=text("Index Name"),
        open=text("Open Index Value"),
        high=text("High Index Value"),
        low=text("Low Index Value"),
        close=text("Closing Index Value"),
        points_change=text("Points Change"),
        pct_change=text("Change(%)"),
        volume=text("Volume").str.replace(r"\.0+$", ""),  # 179982752.00 in some 2015 files
        turnover_inr=text("Turnover (Rs. Cr.)"),
        pe=text("P/E"),
        pb=text("P/B"),
        div_yield=text("Div Yield"),
    )
    check_dates_parsed(frame, raw, {"trade_date": "Index Date"})
    frame = cast(frame, INDEX_CLOSE_SCHEMA)
    if frame["trade_date"].null_count() or frame["index_name"].null_count():
        raise FileShapeError("rows without an index name or date")
    # An index can appear twice, byte for byte (GSEC10 NSE Index on 15 Sep 2014); keep one.
    # Two different rows for one index are a shape error rather than a guess.
    frame = frame.unique(maintain_order=True)
    if frame["index_name"].is_duplicated().any():
        name = frame.filter(frame["index_name"].is_duplicated())["index_name"][0]
        raise FileShapeError(f"two different rows for index {name!r}")
    found = sorted(frame["trade_date"].unique().to_list())
    if expected_date is not None and frame.height and found != [expected_date]:
        raise TradeDateMismatchError(expected_date, found)
    return frame
