"""The PR zip's mcap, Ix and ffix files (spec 0007, section 2), from samples of real files."""

from datetime import date
from decimal import Decimal as D
from pathlib import Path

import pytest

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError
from cagr_data.vendors.nse.pr import parse_issue_cap, parse_mcap
from cagr_data.vendors.nse.urls import build_url


def _zip(nse_fixtures: Path, name: str) -> bytes:
    return (nse_fixtures / "pr" / name).read_bytes()


def test_mcap_strips_padding_and_drops_the_totals(nse_fixtures: Path) -> None:
    frame = parse_mcap(_zip(nse_fixtures, "PR010224.zip"), date(2024, 2, 1))
    assert frame.height == 6  # the Listed, Permitted and Total rows are gone
    first = frame.row(0, named=True)
    assert first == {
        "trade_date": date(2024, 2, 1), "symbol": "20MICRONS", "series": "EQ",
        "security_name": "20 MICRONS LTD", "category": "Listed",
        "last_trade_date": date(2024, 2, 1), "face_value": D("5.0000"),
        "issue_size": 35286502, "close": D("166.0500"), "market_cap_inr": D("5859323657.10"),
    }  # fmt: skip
    stale = frame.filter(frame["symbol"] == "3RDROCK").row(0, named=True)
    assert (stale["series"], stale["last_trade_date"]) == ("IT", date(2023, 9, 13))


def test_mcap_member_name_case_changed(nse_fixtures: Path) -> None:
    frame = parse_mcap(_zip(nse_fixtures, "PR231026.zip"), date(2026, 10, 23))
    assert frame["symbol"].to_list() == ["20MICRONS", "3BBLACKBIO", "ACTIVEINFR"]
    assert frame["category"].to_list() == ["Listed", "Permitted", "Listed"]


def test_mcap_wrong_date_or_missing_file(nse_fixtures: Path) -> None:
    with pytest.raises(TradeDateMismatchError):
        parse_mcap(_zip(nse_fixtures, "PR010224.zip"), date(2024, 2, 2))
    with pytest.raises(FileShapeError):
        parse_mcap(_zip(nse_fixtures, "PR150615.zip"), date(2015, 6, 15))


def test_issue_cap_reads_ix_and_ffix_without_section_lines(nse_fixtures: Path) -> None:
    frame = parse_issue_cap(_zip(nse_fixtures, "PR040110.zip"), date(2010, 1, 4))
    assert frame.height == 8
    assert frame.filter(frame["source"] == "ix")["index_name"].unique().sort().to_list() == [
        "BANK Nifty", "CNX 500",
    ]  # fmt: skip
    nuvo = frame.filter(frame["symbol"] == "ABIRLANUVO").row(0, named=True)
    assert (nuvo["source"], nuvo["index_name"], nuvo["issue_cap"]) == ("ffix", "JR. NIFTY",
                                                                       103006768)  # fmt: skip
    assert nuvo["investible_factor"] == D("0.584835")
    assert set(frame["trade_date"].to_list()) == {date(2010, 1, 4)}


def test_issue_cap_with_only_ffix_or_neither(nse_fixtures: Path) -> None:
    only_ffix = parse_issue_cap(_zip(nse_fixtures, "PR020113.zip"), date(2013, 1, 2))
    assert only_ffix["source"].to_list() == ["ffix"]
    assert parse_issue_cap(_zip(nse_fixtures, "PR150615.zip"), date(2015, 6, 15)).height == 0


def test_two_digit_year_in_urls() -> None:
    template = "https://x/pr/PR{DD}{MM}{YY}.zip"
    assert build_url(template, date(2010, 1, 4)) == "https://x/pr/PR040110.zip"
