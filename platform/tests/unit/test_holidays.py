import json
from datetime import date
from pathlib import Path

import pytest

from cagr_data.vendors.errors import FileShapeError
from cagr_data.vendors.nse.holidays import HOLIDAY_SCHEMA, parse_holidays, segment_dates

FILE = "holiday-master_trading_2026.json"


def _doc(entries: list[dict[str, object]]) -> bytes:
    return json.dumps({"CM": entries}).encode()


def test_keeps_every_segment(nse_fixtures: Path) -> None:
    frame = parse_holidays((nse_fixtures / FILE).read_bytes(), 2026)
    assert frame.schema == HOLIDAY_SCHEMA
    assert frame.height == 239
    assert frame["segment"].n_unique() == 12


def test_cm_list_includes_weekend_holidays(nse_fixtures: Path) -> None:
    frame = parse_holidays((nse_fixtures / FILE).read_bytes(), 2026)
    cm = segment_dates(frame)
    assert len(cm) == 20
    assert date(2026, 11, 8) in cm  # Diwali, a Sunday
    assert date(2026, 10, 2) in cm
    assert date(2026, 2, 1) not in cm  # Budget day, a Sunday session
    diwali = frame.filter(segment="CM", date=date(2026, 11, 8)).to_dicts()[0]
    assert diwali["weekday"] == "Sunday"
    assert diwali["description"] == "Diwali Laxmi Pujan*"


def test_empty_object_is_no_list() -> None:
    frame = parse_holidays(b"{}", 2010)
    assert frame.height == 0
    assert frame.schema == HOLIDAY_SCHEMA


@pytest.mark.parametrize(
    ("content", "match"),
    [
        (b"<html>", "not JSON"),
        (b"[]", "object"),
        (json.dumps({"CM": {}}).encode(), "list"),
        (_doc([{"weekDay": "Monday"}]), "tradingDate"),
        (_doc([{"tradingDate": "2026-01-26"}]), "not a date"),
        (_doc([{"tradingDate": "26-Jan-2026", "weekDay": "Tuesday"}]), "Monday"),
        (_doc([{"tradingDate": "25-Dec-2025"}]), "outside 2026"),
        (_doc([{"tradingDate": "26-Jan-2026"}, {"tradingDate": "26-Jan-2026"}]), "twice"),
    ],
)
def test_bad_responses_raise(content: bytes, match: str) -> None:
    with pytest.raises(FileShapeError, match=match):
        parse_holidays(content, 2026)
