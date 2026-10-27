"""Parse the members of NSE's daily PR zip (PR{DDMMYY}.zip) that carry shares outstanding.

- mcap{DDMMYYYY}.csv, from 1 Feb 2024: every security's issue size and market cap. Fields are
  padded with spaces, dates read `01 FEB 2024`, and the last rows are totals (Listed,
  Permitted, Total) with a blank series, which are dropped.
- Ix{DDMMYY}.csv and ffix{DDMMYY}.csv, 2010 to 2013: ISSUE_CAP of each index constituent, one
  row per index it belongs to. Each index's block starts with a section line (` , , ,<name>`)
  and a separator line, which are dropped. Ix is missing from some zips (2 Jan 2013 has only
  ffix). Neither file carries a date, so rows get the partition date.

Member names changed case over the years (MCAP01022024.csv, mcap23102026.csv), so they are
matched without regard to case.
"""

import io
import re
import zipfile
from datetime import date

import polars as pl

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError
from cagr_data.vendors.nse.csvfile import body, cast, check_dates_parsed, header, parse_date, text

PRICE = pl.Decimal(18, 4)
CAP = pl.Decimal(24, 2)

MCAP_COLUMNS = (
    "Trade Date", "Symbol", "Series", "Security Name", "Category", "Last Trade Date",
    "Face Value(Rs.)", "Issue Size", "Close Price/Paid up value(Rs.)", "Market Cap(Rs.)",
)  # fmt: skip
MCAP_SCHEMA: dict[str, pl.DataType] = {
    "trade_date": pl.Date(),
    "symbol": pl.String(),
    "series": pl.String(),
    "security_name": pl.String(),
    "category": pl.String(),
    "last_trade_date": pl.Date(),
    "face_value": PRICE,
    "issue_size": pl.Int64(),
    "close": PRICE,
    "market_cap_inr": CAP,
}

IX_COLUMNS = ("INDEX_FLG", "SYMBOL", "SERIES", "SECURITY", "ISSUE_CAP", "CLOSE_PRIC", "MKT_CAP",
              "WEIGHTAGE")  # fmt: skip
FFIX_COLUMNS = ("INDEX_FLG", "SYMBOL", "SERIES", "SECURITY", "ISSUE_CAP", "INVESTIBLE_FACTOR",
                "CLOSE_PRIC", "FF_MKT_CAP", "WEIGHTAGE")  # fmt: skip
ISSUE_CAP_SCHEMA: dict[str, pl.DataType] = {
    "trade_date": pl.Date(),
    "source": pl.String(),
    "index_name": pl.String(),
    "symbol": pl.String(),
    "series": pl.String(),
    "security_name": pl.String(),
    "issue_cap": pl.Int64(),
    "investible_factor": pl.Decimal(10, 6),
    "close": PRICE,
    "market_cap_inr": CAP,
    "weightage": pl.Decimal(10, 4),
}


def _members(content: bytes) -> dict[str, bytes]:
    """The zip's members by lower-case name."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return {name.lower(): zf.read(name) for name in zf.namelist()}
    except zipfile.BadZipFile as e:
        raise FileShapeError(f"not a zip file: {e}") from e


def _member(members: dict[str, bytes], pattern: str) -> bytes | None:
    found = [v for k, v in members.items() if re.fullmatch(pattern, k)]
    if len(found) > 1:
        raise FileShapeError(f"more than one member matches {pattern}")
    return found[0] if found else None


def parse_mcap(content: bytes, expected_date: date | None = None) -> pl.DataFrame:
    """The mcap rows of one PR zip. A zip without an mcap file is an error: the feed only
    covers dates that have one."""
    csv = _member(_members(content), r"mcap\d{8}\.csv")
    if csv is None:
        raise FileShapeError("no mcap file in the zip")
    names = header(csv)
    if tuple(names) != MCAP_COLUMNS:
        raise FileShapeError(f"unexpected mcap header: {names}")
    raw = body(csv, names).filter(text("Series").is_not_null())
    frame = raw.select(
        trade_date=parse_date("Trade Date", "%d %b %Y"),
        symbol=text("Symbol"),
        series=text("Series"),
        security_name=text("Security Name"),
        category=text("Category"),
        last_trade_date=parse_date("Last Trade Date", "%d %b %Y"),
        face_value=text("Face Value(Rs.)"),
        issue_size=text("Issue Size"),
        close=text("Close Price/Paid up value(Rs.)"),
        market_cap_inr=text("Market Cap(Rs.)"),
    )
    check_dates_parsed(frame, raw, {"trade_date": "Trade Date",
                                    "last_trade_date": "Last Trade Date"})  # fmt: skip
    frame = cast(frame, MCAP_SCHEMA)
    if frame["symbol"].null_count():
        raise FileShapeError("an mcap row has no symbol")
    if expected_date is not None:
        days = sorted(set(frame["trade_date"].unique().to_list()))
        if days != [expected_date]:
            raise TradeDateMismatchError(expected_date, days)
    return frame


def _issue_cap_rows(csv: bytes, source: str, columns: tuple[str, ...], day: date) -> pl.DataFrame:
    names = header(csv)
    if tuple(names) != columns:
        raise FileShapeError(f"unexpected {source} header: {names}")
    # Section and separator lines have fewer fields than the header; drop them before reading.
    lines = csv.split(b"\n")
    kept = [lines[0]] + [ln for ln in lines[1:] if ln.strip() and not ln.startswith(b" ,")]
    raw = body(b"\n".join(kept) + b"\n", names)
    frame = raw.select(
        trade_date=pl.lit(day, pl.Date),
        source=pl.lit(source),
        index_name=text("INDEX_FLG"),
        symbol=text("SYMBOL"),
        series=text("SERIES"),
        security_name=text("SECURITY"),
        issue_cap=text("ISSUE_CAP"),
        investible_factor=text("INVESTIBLE_FACTOR") if source == "ffix" else pl.lit(None),
        close=text("CLOSE_PRIC"),
        market_cap_inr=text("FF_MKT_CAP" if source == "ffix" else "MKT_CAP"),
        weightage=text("WEIGHTAGE"),
    )
    frame = cast(frame, ISSUE_CAP_SCHEMA)
    keys = pl.any_horizontal(pl.col("index_name", "symbol", "series").is_null())
    if frame.select(keys).to_series().any():
        raise FileShapeError(f"a {source} row has no index, symbol or series")
    return frame


def parse_issue_cap(content: bytes, expected_date: date | None = None) -> pl.DataFrame:
    """The Ix and ffix rows of one PR zip, possibly none. The partition date is required: the
    files themselves carry no date."""
    if expected_date is None:
        raise ValueError("the Ix and ffix files carry no date; pass the partition date")
    members = _members(content)
    frames = [
        _issue_cap_rows(csv, source, columns, expected_date)
        for source, pattern, columns in (
            ("ix", r"ix\d{6}\.csv", IX_COLUMNS),
            ("ffix", r"ffix\d{6}\.csv", FFIX_COLUMNS),
        )
        if (csv := _member(members, pattern)) is not None
    ]
    if not frames:
        return pl.DataFrame(schema=ISSUE_CAP_SCHEMA)
    return pl.concat(frames)
