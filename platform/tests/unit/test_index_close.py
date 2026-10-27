from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError
from cagr_data.vendors.nse.index_close import INDEX_CLOSE_SCHEMA, parse_index_close

FILE = "ind_close_all_23102026.csv"
DAY = date(2026, 10, 23)


def _csv(fixtures: Path) -> bytes:
    return (fixtures / FILE).read_bytes()


def _row(csv: bytes, name: str) -> dict[str, object]:
    frame = parse_index_close(csv, DAY)
    rows = frame.filter(index_name=name).to_dicts()
    assert len(rows) == 1
    return rows[0]


def test_parses_every_index_with_exact_values(nse_fixtures: Path) -> None:
    frame = parse_index_close(_csv(nse_fixtures), DAY)
    assert frame.schema == INDEX_CLOSE_SCHEMA
    assert frame.height == 17
    assert _row(_csv(nse_fixtures), "Nifty 50") == {
        "trade_date": DAY,
        "index_name": "Nifty 50",
        "open": Decimal("22742.2000"),
        "high": Decimal("22787.2500"),
        "low": Decimal("22372.0000"),
        "close": Decimal("22600.0000"),
        "points_change": Decimal("-87.6500"),
        "pct_change": Decimal("-0.3900"),
        "volume": 364190826,
        "turnover_inr": Decimal("29859.76"),
        "pe": Decimal("19.0500"),
        "pb": Decimal("2.7800"),
        "div_yield": Decimal("1.5200"),
    }


def test_dash_is_missing(nse_fixtures: Path) -> None:
    row = _row(_csv(nse_fixtures), "Nifty50 Dividend Points")
    assert row["close"] == Decimal("204.2200")
    assert row["points_change"] == Decimal("0")
    assert row["open"] is None and row["pe"] is None and row["volume"] is None


def test_crlf_gives_the_same_frame(nse_fixtures: Path) -> None:
    csv = _csv(nse_fixtures)
    crlf = csv.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    assert parse_index_close(crlf, DAY).equals(parse_index_close(csv, DAY))


def test_other_date_is_a_mismatch(nse_fixtures: Path) -> None:
    with pytest.raises(TradeDateMismatchError) as e:
        parse_index_close(_csv(nse_fixtures), date(2026, 10, 22))
    assert e.value.found == [DAY]


def test_header_must_match(nse_fixtures: Path) -> None:
    csv = _csv(nse_fixtures).replace(b"Div Yield", b"Dividend Yield", 1)
    with pytest.raises(FileShapeError, match="header"):
        parse_index_close(csv, DAY)


@pytest.mark.parametrize(
    ("old", "new"),
    [(b"Nifty 50,23-10-2026", b"Nifty 50,2026-10-23"), (b"22787.25", b"22787.2x")],
)
def test_bad_values_raise(nse_fixtures: Path, old: bytes, new: bytes) -> None:
    with pytest.raises(FileShapeError):
        parse_index_close(_csv(nse_fixtures).replace(old, new, 1), DAY)


def test_slashed_dates_are_read(nse_fixtures: Path) -> None:
    # From 9 Jun 2014 some files write the date as 09/06/2014.
    csv = (
        (nse_fixtures / "ind_close_all_23102026.csv")
        .read_bytes()
        .replace(b"23-10-2026", b"23/10/2026")
    )
    frame = parse_index_close(csv, date(2026, 10, 23))
    assert frame["trade_date"].unique().to_list() == [date(2026, 10, 23)]


def test_an_exact_duplicate_row_is_kept_once(nse_fixtures: Path) -> None:
    # 15 Sep 2014 lists GSEC10 NSE Index twice, byte for byte.
    csv = (nse_fixtures / "ind_close_all_23102026.csv").read_bytes()
    lines = csv.splitlines(keepends=True)
    once = parse_index_close(csv, DAY)
    twice = parse_index_close(b"".join([*lines[:2], lines[1], *lines[2:]]), DAY)
    assert twice.equals(once)
    changed = lines[1][:-3] + b"99" + lines[1][-1:]  # the same index, another div yield
    with pytest.raises(FileShapeError):
        parse_index_close(b"".join([*lines[:2], changed, *lines[2:]]), DAY)
