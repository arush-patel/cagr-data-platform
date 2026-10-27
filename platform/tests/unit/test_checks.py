from datetime import date
from decimal import Decimal

import polars as pl

from cagr_data.quality import checks as qc
from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError

D = Decimal


def _bars(*rows: tuple[str, str, str | None, int, str, str, str, str]) -> pl.DataFrame:
    return pl.DataFrame(
        [
            {
                "symbol": s, "series": se, "isin": i, "volume": v,
                "open": D(o), "high": D(h), "low": D(lo), "close": D(c),
            }
            for s, se, i, v, o, h, lo, c in rows
        ]
    )  # fmt: skip


GOOD = ("RELIANCE", "EQ", "INE002A01018", 10, "100", "110", "95", "105")


def test_parse_outcomes() -> None:
    assert [o.passed for o in qc.parse_outcomes(None)] == [True, True]
    shape = qc.parse_outcomes(FileShapeError("bad header"))
    assert [(o.check_id, o.passed) for o in shape] == [("file_shape", False)]
    mismatch = qc.parse_outcomes(TradeDateMismatchError(date(2024, 7, 9), [date(2024, 7, 8)]))
    assert [(o.check_id, o.passed) for o in mismatch] == [
        ("file_shape", True),
        ("trade_date_matches", False),
    ]
    assert mismatch[1].observed == {"expected": "2024-07-09", "found": ["2024-07-08"]}


def test_no_duplicates() -> None:
    frame = _bars(GOOD, GOOD, ("RELIANCE", "BE", None, 1, "1", "1", "1", "1"))
    outcome = qc.no_duplicates(frame, ("symbol", "series"))
    assert not outcome.passed
    assert outcome.observed["duplicated"] == 1
    assert outcome.observed["sample"] == ["RELIANCE/EQ"]
    assert qc.no_duplicates(frame.head(1), ("symbol", "series")).passed


def test_prices_positive_only_where_traded() -> None:
    zero_untraded = ("A", "EQ", None, 0, "0", "0", "0", "0")
    zero_traded = ("B", "EQ", None, 5, "0", "10", "0", "10")
    assert qc.prices_positive(_bars(GOOD, zero_untraded)).passed
    outcome = qc.prices_positive(_bars(GOOD, zero_traded))
    assert not outcome.passed and outcome.observed["sample"] == ["B/EQ"]


def test_ohlc_order() -> None:
    assert qc.ohlc_order(_bars(GOOD)).passed
    outcome = qc.ohlc_order(_bars(GOOD, ("X", "EQ", None, 1, "100", "99", "95", "98")))
    assert not outcome.passed and outcome.observed["rows"] == 1


def test_isin_present_from_its_start_date() -> None:
    params = {"from": "2011-06-22", "series": ["EQ"]}
    frame = _bars(
        ("A", "EQ", None, 1, "1", "1", "1", "1"), ("B", "BE", None, 1, "1", "1", "1", "1")
    )
    assert qc.isin_present(frame, date(2010, 1, 4), params).passed
    outcome = qc.isin_present(frame, date(2024, 7, 5), params)
    assert not outcome.passed and outcome.observed["sample"] == ["A/EQ"]


def test_benchmark_present() -> None:
    params = {"index_names": ["Nifty 50", "S&P CNX Nifty"]}
    assert qc.benchmark_present(pl.DataFrame({"index_name": ["S&P CNX Nifty"]}), params).passed
    assert not qc.benchmark_present(pl.DataFrame({"index_name": ["Nifty Bank"]}), params).passed


def test_row_count_band() -> None:
    params = {"max_pct_diff": 10, "min_abs_diff": 50}
    previous = [3000] * 19 + [10]
    assert qc.row_count(3300, previous, params).passed
    assert not qc.row_count(3301, previous, params).passed
    assert qc.row_count(240, [200], params).passed  # the floor of 50 rows
    assert not qc.row_count(251, [200], params).passed
    assert qc.row_count(17, [], params).observed["skipped"] == "no previous sessions"


def test_vendor_revision() -> None:
    assert qc.vendor_revision(1, None).passed
    assert not qc.vendor_revision(2, 7).passed


def test_arrival() -> None:
    weekend = (6, 7)
    friday, saturday = date(2026, 10, 23), date(2026, 10, 24)
    assert [o.passed for o in qc.arrival(friday, weekend, False)] == [True, True]
    assert [o.passed for o in qc.arrival(saturday, weekend, False)] == [True, False]
    assert [o.passed for o in qc.arrival(date(2026, 11, 8), weekend, True)] == [False, True]


def test_file_arrived() -> None:
    assert qc.file_arrived({"a": True, "b": True}).passed
    outcome = qc.file_arrived({"a": True, "b": False})
    assert not outcome.passed and outcome.observed == {"missing": ["b"]}
