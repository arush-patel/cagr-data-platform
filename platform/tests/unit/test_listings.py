from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cagr_data.vendors.errors import FileShapeError
from cagr_data.vendors.nse.listings import (
    EQUITY_LIST_SCHEMA,
    SYMBOL_CHANGE_SCHEMA,
    parse_equity_list,
    parse_symbol_change,
)

SNAPSHOT = date(2026, 10, 27)


def test_main_board(nse_fixtures: Path) -> None:
    frame = parse_equity_list((nse_fixtures / "EQUITY_L.csv").read_bytes(), "main", SNAPSHOT)
    assert frame.schema == EQUITY_LIST_SCHEMA
    assert frame.height == 17
    assert frame.filter(symbol="RELIANCE").to_dicts() == [
        {
            "snapshot_date": SNAPSHOT,
            "board": "main",
            "symbol": "RELIANCE",
            "name": "Reliance Industries Limited",
            "series": "EQ",
            "listing_date": date(1995, 11, 29),
            "paid_up_value": Decimal("10.0000"),
            "market_lot": 1,
            "isin": "INE002A01018",
            "face_value": Decimal("10.0000"),
        }
    ]
    assert set(frame["series"]) == {"EQ", "BE"}


def test_sme_board_has_two_digit_years_and_no_lot(nse_fixtures: Path) -> None:
    frame = parse_equity_list((nse_fixtures / "SME_EQUITY_L.csv").read_bytes(), "sme", SNAPSHOT)
    assert frame.schema == EQUITY_LIST_SCHEMA
    assert frame.height == 16
    ravita = frame.filter(symbol="RAVITA").to_dicts()[0]
    assert ravita["listing_date"] == date(2026, 10, 22)
    assert ravita["series"] == "ST"
    assert ravita["market_lot"] is None
    assert ravita["board"] == "sme"
    assert frame["listing_date"].null_count() == 0
    assert set(frame["series"]) == {"SM", "ST"}


def test_board_decides_the_expected_header(nse_fixtures: Path) -> None:
    with pytest.raises(FileShapeError, match="sme equity list header"):
        parse_equity_list((nse_fixtures / "EQUITY_L.csv").read_bytes(), "sme", SNAPSHOT)


def test_bad_listing_date_raises(nse_fixtures: Path) -> None:
    csv = (nse_fixtures / "EQUITY_L.csv").read_bytes().replace(b"29-NOV-1995", b"29/11/1995")
    with pytest.raises(FileShapeError, match="not a date"):
        parse_equity_list(csv, "main", SNAPSHOT)


def test_symbol_changes(nse_fixtures: Path) -> None:
    frame = parse_symbol_change((nse_fixtures / "symbolchange.csv").read_bytes(), SNAPSHOT)
    assert frame.schema == SYMBOL_CHANGE_SCHEMA
    assert frame.height == 18
    assert frame.filter(new_symbol="360ONE").to_dicts() == [
        {
            "snapshot_date": SNAPSHOT,
            "company_name": "360 ONE WAM LIMITED",
            "old_symbol": "IIFLWAM",
            "new_symbol": "360ONE",
            "change_date": date(2023, 1, 23),
        }
    ]
    assert not frame["company_name"].str.starts_with(" ").any()


def test_symbol_change_needs_both_symbols(nse_fixtures: Path) -> None:
    csv = (nse_fixtures / "symbolchange.csv").read_bytes().replace(b",IIFLWAM,", b",,", 1)
    with pytest.raises(FileShapeError, match="old_symbol"):
        parse_symbol_change(csv, SNAPSHOT)
