from datetime import date

import polars as pl
import pytest

from cagr import dataset
from cagr.calendar import Calendar

SESSIONS = [date(2024, 1, d) for d in (1, 2, 3, 4, 5, 8, 9, 10)]
CAL = Calendar(SESSIONS)


def values(key: str, rows: list[tuple[int, date, float]]) -> pl.DataFrame:
    return pl.DataFrame(rows, schema=["listing_id", "date", key], orient="row")


def test_anchors() -> None:
    assert dataset.anchor_offset("T") == 0
    assert dataset.anchor_offset("T-1") == -1
    assert dataset.anchor_offset("T \u2212 2") == -2
    assert dataset.anchor_offset("T+5") == 5
    with pytest.raises(ValueError):
        dataset.anchor_offset("yesterday")


def test_event_rows_anchor_on_sessions() -> None:
    events = pl.DataFrame(
        {
            "listing_id": [1, 2],
            "start_date": [date(2024, 1, 8), date(2024, 1, 6)],
            "cls": ["A", "B"],
        }
    )
    out = dataset.event_rows(events, CAL, at="T-1")
    # Monday 8 Jan: T-1 is Friday 5 Jan; Saturday 6 Jan starts on Friday 5 Jan, so T-1 is 4 Jan
    assert out["date"].to_list() == [date(2024, 1, 5), date(2024, 1, 4)]
    assert out.columns == ["listing_id", "start_date", "cls", "date"]


def test_build_joins_on_the_exact_session() -> None:
    rows = pl.DataFrame({"listing_id": [1, 1, 2], "date": [SESSIONS[1], SESSIONS[2], SESSIONS[1]]})
    rsi = values(
        "rsi_14@1", [(1, SESSIONS[1], 40.0), (1, SESSIONS[2], 60.0), (2, SESSIONS[0], 10.0)]
    )
    golden = values(
        "golden_a@11", [(1, SESSIONS[1], 1.0), (1, SESSIONS[2], 0.0), (2, SESSIONS[1], 1.0)]
    )
    ds = dataset.build(rows, features={"rsi_14@1": rsi}, labels={"golden_a@11": golden})
    assert ds.X()["rsi_14@1"].to_list() == [40.0, 60.0, None]  # listing 2 is not carried forward
    assert ds.y().to_list() == [1.0, 0.0, 1.0]
    assert (
        ds.recipe_hash
        == dataset.build(
            rows, features={"rsi_14@1": rsi}, labels={"golden_a@11": golden}
        ).recipe_hash
    )


def test_build_refuses_leaks_and_ambiguity() -> None:
    rows = pl.DataFrame({"listing_id": [1], "date": [SESSIONS[0]]})
    f = values("fwd@1", [(1, SESSIONS[0], 1.0)])
    with pytest.raises(dataset.LeakError):
        dataset.build(rows, features={"fwd@1": f}, leaky=["fwd@1"])
    with pytest.raises(dataset.LeakError):
        dataset.build(rows, features={"fwd@1": f}, labels={"fwd@1": f})
    dup = values("x@1", [(1, SESSIONS[0], 1.0), (1, SESSIONS[0], 2.0)])
    with pytest.raises(ValueError, match="more than one"):
        dataset.build(rows, features={"x@1": dup})


def test_asof_takes_the_latest_earlier_value() -> None:
    rows = pl.DataFrame({"listing_id": [1, 1], "date": [date(2024, 1, 10), date(2024, 3, 1)]})
    shares = values("shares", [(1, date(2024, 1, 1), 100.0), (1, date(2024, 2, 1), 120.0)])
    assert dataset.asof(rows, shares, "shares")["shares"].to_list() == [100.0, 120.0]
    assert dataset.asof(rows, shares, "shares", max_days=20)["shares"].to_list() == [100.0, None]
