"""Parse NSE's holiday-master API response into raw.nse_holiday rows.

The response is an object keyed by segment (CBM, CD, CM, FO, ...), each a list of
{tradingDate: "DD-Mon-YYYY", weekDay, description, morning_session, evening_session}. Raw keeps
every segment; the calendar reads CM. For 2010 the API answers 200 with {}: that means "no list",
not "no holidays", so the result is an empty frame and the caller must not read it as a year
without holidays.
"""

import json
from datetime import date, datetime
from typing import Any

import polars as pl

from cagr_data.vendors.errors import FileShapeError

HOLIDAY_SCHEMA: dict[str, pl.DataType] = {
    "segment": pl.String(),
    "date": pl.Date(),
    "description": pl.String(),
    "weekday": pl.String(),
}


def _entry(segment: str, item: Any) -> dict[str, object]:
    if not isinstance(item, dict) or not isinstance(item.get("tradingDate"), str):
        raise FileShapeError(f"{segment}: entry without a tradingDate: {item!r}")
    try:
        day = datetime.strptime(item["tradingDate"].strip(), "%d-%b-%Y").date()
    except ValueError as e:
        bad = item["tradingDate"]
        raise FileShapeError(f"{segment}: tradingDate is not a date: {bad!r}") from e
    weekday = item.get("weekDay")
    if isinstance(weekday, str) and weekday.strip() and weekday.strip() != day.strftime("%A"):
        raise FileShapeError(f"{segment}: {day} is a {day:%A}, the file says {weekday}")
    description = item.get("description")
    return {
        "segment": segment,
        "date": day,
        "description": description.strip() if isinstance(description, str) else None,
        "weekday": weekday.strip() if isinstance(weekday, str) else None,
    }


def parse_holidays(content: bytes, year: int | None = None) -> pl.DataFrame:
    """Parse one holiday-master response. With year, every date must fall in that year."""
    try:
        doc = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise FileShapeError(f"not JSON: {e}") from e
    if not isinstance(doc, dict):
        raise FileShapeError(f"expected an object keyed by segment, got {type(doc).__name__}")
    rows: list[dict[str, object]] = []
    for segment, items in doc.items():
        if not isinstance(items, list):
            raise FileShapeError(f"{segment}: expected a list, got {type(items).__name__}")
        rows.extend(_entry(segment, item) for item in items)
    frame = pl.DataFrame(rows, schema=HOLIDAY_SCHEMA, orient="row")
    if year is not None:
        outside = frame.filter(pl.col("date").dt.year() != year)
        if outside.height:
            raise FileShapeError(f"dates outside {year}: {outside['date'].to_list()}")
    if frame.select(pl.struct("segment", "date").is_duplicated().any()).item():
        raise FileShapeError("a segment lists the same date twice")
    return frame.sort("segment", "date")


def segment_dates(frame: pl.DataFrame, segment: str = "CM") -> list[date]:
    """The holiday dates of one segment."""
    return frame.filter(pl.col("segment") == segment)["date"].to_list()
