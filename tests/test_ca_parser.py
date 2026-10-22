from decimal import Decimal

import pytest

from cagr_data.reference.ca_parser import parse_subject


@pytest.mark.parametrize(
    ("subject", "kind", "factor"),
    [
        ("Bonus 1:1", "bonus", 0.5),
        ("Bonus 1:4", "bonus", 0.8),
        ("Bonus 2:5", "bonus", 5 / 7),
        ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share", "split", 0.2),
        ("Face Value Split (Sub-Division) - From Re 1/- Per Share To Re 0.50/- Per Share", "split", 0.5),
        ("Consolidation Of Shares From Rs 1/- Per Share To Rs 10/- Per Share", "consolidation", 10.0),
    ],
)
def test_price_effect(subject: str, kind: str, factor: float) -> None:
    [a] = parse_subject(subject)
    assert a.kind == kind
    assert a.parse_status == "parsed"
    assert a.price_factor == pytest.approx(factor)


def test_preference_bonus_has_no_price_effect() -> None:
    [a] = parse_subject("Bonus Ncrps 4:1")
    assert (a.kind, a.parse_status, a.price_factor) == ("other", "parsed", 1.0)


def test_dividends_in_one_subject_are_summed() -> None:
    [a] = parse_subject("Final Dividend - Rs 8 Per Share/Special Dividend - Rs 2 Per Share")
    assert (a.kind, a.cash_amount) == ("dividend", 10.0)


def test_meeting_text_is_ignored_but_its_dividend_kept() -> None:
    assert parse_subject("Annual General Meeting") == []
    [a] = parse_subject("Annual General Meeting/Dividend - Rs 3.50 Per Share")
    assert a.cash_amount == 3.5


@pytest.mark.parametrize(
    "subject",
    ["Rights 1:10 @ Premium Rs 90/-", "Demerger", "Scheme Of Arrangement", "Dividend - 50%", "Bonus"],
)
def test_unclear_text_goes_to_review(subject: str) -> None:
    [a] = parse_subject(subject)
    assert a.parse_status == "needs_review"


def test_ratios_are_exact() -> None:
    [a] = parse_subject("Bonus 2:5")
    assert (a.ratio_new, a.ratio_old) == (Decimal(7), Decimal(5))
