from datetime import date
from fractions import Fraction
from typing import Any

from cagr_observatory.review import reasons


def adj(**kw: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "action_kind": None, "subject": None, "ratio_new": None, "ratio_old": None,
        "factor": None, "expected_factor": None, "observed_factor": None,
        "isin_before": "INE000A01011", "isin_on": "INE000A01011",
    }  # fmt: skip
    return row | kw


def test_nearest_clean_ratio() -> None:
    assert reasons.nearest_clean_ratio(0.502) == Fraction(1, 2)
    assert reasons.nearest_clean_ratio(99.6) is None  # a hundredfold isn't on the list
    assert reasons.nearest_clean_ratio(10.2) == Fraction(10)
    assert reasons.nearest_clean_ratio(0.8) is None


def test_bonus_whose_price_disagrees() -> None:
    row = adj(action_kind="bonus", subject="Bonus 1:1", ratio_new=1, ratio_old=1,
              expected_factor=0.5, observed_factor=0.62)  # fmt: skip
    assert reasons.adjustment_title(row) == "Bonus 1:1"
    assert reasons.adjustment_reason(row) == (
        "NSE announced “Bonus 1:1”. That predicts a factor of 0.5, but the close moved"
        f" {reasons.X}0.62, 24% apart, so the rules stopped."
    )


def test_unexplained_jump_with_isin_change() -> None:
    row = adj(observed_factor=0.1003, isin_on="INE000A01029")
    assert reasons.adjustment_title(row) == f"Unexplained jump {reasons.X}0.1003"
    text = reasons.adjustment_reason(row)
    assert "close to 1/10" in text and "The ISIN changed" in text


def test_demerger_is_always_confirmed() -> None:
    row = adj(action_kind="demerger", subject="Demerger", factor=0.83, observed_factor=0.83)
    assert "always confirmed by a person" in reasons.adjustment_reason(row)


def test_possible_merge() -> None:
    row = {
        "change_kind": "new_listing", "listing_id": 9, "symbol": "ABC", "board": "main",
        "date": date(2024, 2, 6), "proposed_by": "rules", "payload": {},
        "evidence": {"candidates": [3]},
    }  # fmt: skip
    assert reasons.change_title(row) == "Possible merge"
    assert reasons.change_reason(row, ["ABCOLD"]) == (
        "ABC appeared on 06 Feb 2024 as a new listing. It may be the same security as ABCOLD."
        " Merge it if so; reject this item to keep it separate."
    )
