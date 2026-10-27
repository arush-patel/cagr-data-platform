"""The corporate-actions subject parser on real NSE subjects (spec 0002, 7.2)."""

import json
from datetime import date
from decimal import Decimal

import pytest

from cagr_data.vendors.errors import FileShapeError
from cagr_data.vendors.nse.corporate_actions import ParsedAction, parse_response, parse_subject

D = Decimal


def one(subject: str, face_value: str | None = "10") -> ParsedAction:
    actions = parse_subject(subject, D(face_value) if face_value else None)
    assert len(actions) == 1, actions
    return actions[0]


@pytest.mark.parametrize(
    ("subject", "old", "new"),
    [
        ("Face Value Split (Sub-Division) - From Rs10/- Per Share To Re 1/- Per Share", 10, 1),
        ("Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 1/- Per Share", 2, 1),
        ("Fv Split Rs.10 To Re.1", 10, 1),
        ("Fv Split Rs.10/- To Rs.2/-", 10, 2),
    ],
)
def test_splits(subject: str, old: int, new: int) -> None:
    a = one(subject)
    assert (a.kind, a.ratio_old, a.ratio_new, a.parsed) == ("split", D(old), D(new), True)


def test_consolidation() -> None:
    a = one("Consolidation Of Shares From Re 1/- Per Share To Rs 10/- Per Share")
    assert (a.kind, a.ratio_old, a.ratio_new) == ("consolidation", D(1), D(10))


@pytest.mark.parametrize(("subject", "a", "b"), [("Bonus 1:1", 1, 1), ("Bonus 3:1", 3, 1),
                                                 ("Bon 1:2", 1, 2)])  # fmt: skip
def test_bonuses(subject: str, a: int, b: int) -> None:
    got = one(subject)
    assert (got.kind, got.ratio_new, got.ratio_old) == ("bonus", D(a), D(b))


def test_two_actions_in_one_subject() -> None:
    actions = parse_subject("Bon 1:1/Fv Spl Rs.5tors.2", D(5))
    assert [(a.kind, a.ratio_new, a.ratio_old) for a in actions] == [
        ("bonus", D(1), D(1)),
        ("split", D(2), D(5)),
    ]


def test_rights_at_a_premium_add_the_face_value() -> None:
    a = one("Rights 6:179 @ Premium Rs 1810/-", face_value="2")
    assert (a.kind, a.ratio_new, a.ratio_old, a.issue_price) == ("rights", D(6), D(179), D(1812))
    assert one("Rights 1:2 @ Premium Re 1/-", face_value="1").issue_price == D(2)


def test_rights_without_a_price_are_unparsed() -> None:
    a = one("Rights 1:5")
    assert a.kind == "rights" and not a.parsed


@pytest.mark.parametrize(
    ("subject", "cash"),
    [
        ("Dividend - Rs 10 Per Share", "10"),
        ("Interim Dividend - Re 0.01 Per Share", "0.01"),
        ("Annual General Meeting/Dividend - Rs  2.25 Per Share", "2.25"),
        ("Int Div-Rs.3/- Per Share Purpose Revised", "3"),
        ("Agm/Div-Rs.3.50 Per Share", "3.50"),
        ("Special Dividend - Rs 15 Per Share", "15"),
        ("Agm/Div-50%", "5.0000"),
    ],
)
def test_dividends(subject: str, cash: str) -> None:
    dividends = [a for a in parse_subject(subject, D(10)) if a.kind == "dividend"]
    assert [a.cash_per_share for a in dividends] == [D(cash)]


def test_an_amount_that_lost_its_decimal_point_is_unparsed() -> None:
    # GENESYS 2019: "Rs.0125" (Rs 0.125 on a Rs 5 face value) is not read as Rs 125.
    actions = parse_subject("Annual General Meeting/Dividend - Rs.0125 Per Share", D(5))
    [a] = [a for a in actions if a.kind == "dividend"]
    assert not a.parsed and a.cash_per_share is None
    [nine] = parse_subject("Interim Dividend - Rs 09 Per Share", D(10))
    assert nine.cash_per_share == D(9)


def test_two_dividends_in_one_subject() -> None:
    actions = parse_subject(
        "Interim Dividend - Rs 2 Per Share / Special Dividend - Rs 5 Per Share", D(1)
    )
    assert [a.cash_per_share for a in actions] == [D(2), D(5)]


@pytest.mark.parametrize(
    ("subject", "kind"),
    [
        ("Demerger", "demerger"),
        ("Buy Back", "buyback"),
        ("Capital Reduction", "other"),
        ("Interest Payment", "other"),
        ("Extra Ordinary General Meeting", "other"),
        ("Distribution - Rs 2.82 Per Unit Comprising Of Return Of Capital - Rs 2.82 Per Unit",
         "other"),
    ],
)  # fmt: skip
def test_actions_that_never_change_a_ratio(subject: str, kind: str) -> None:
    assert parse_subject(subject, D(10))[0].kind == kind


def test_an_unknown_subject_is_unparsed_not_guessed() -> None:
    [a] = parse_subject("Something New Entirely")
    assert (a.kind, a.parsed) == ("other", False)


def test_parse_response() -> None:
    body = json.dumps(
        [
            {"symbol": "RELIANCE", "comp": "Reliance Industries Limited", "series": "EQ",
             "isin": "INE002A01018", "faceVal": "10", "subject": "Bonus 1:1",
             "exDate": "28-Oct-2024", "recDate": "28-Oct-2024", "bcStartDate": "-",
             "bcEndDate": "-", "ndStartDate": "-", "ndEndDate": "-", "ind": "-",
             "caBroadcastDate": None},
        ]
    ).encode()  # fmt: skip
    frame = parse_response(body, "main")
    row = frame.row(0, named=True)
    assert row["ex_date"] == date(2024, 10, 28)
    assert row["bc_start_date"] is None
    assert row["face_value"] == D(10)
    assert row["board"] == "main"


def test_parse_response_rejects_other_shapes() -> None:
    with pytest.raises(FileShapeError):
        parse_response(b'{"error": "x"}', "main")
    with pytest.raises(FileShapeError):
        parse_response(b"<html>", "main")


# Subjects as NSE published them in 2010 and 2024, typos included.
@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("Fv Spl-Rs10tore1/Div-1.50", [("split", "10", "1"), ("dividend", "1.50")]),
        ("Bon 1:1/Fv Spl Rs.5tore.1", [("bonus", "1", "1"), ("split", "5", "1")]),
        ("Bonus 1: 2", [("bonus", "1", "2")]),
        ("Sch Of Agmt- Bonus Deb1:1", [("other",)]),
        ("Rights 2:3@Prem Rs.345/-", [("rights", "2", "3", "355")]),
        ("Rights 3:10 @Prem Rs.91.5", [("rights", "3", "10", "101.5")]),
        ("Final Dividend Rs.4.50 Per Share And Special Centenary Dividend Rs.5.50 Per Share",
         [("dividend", "4.50"), ("dividend", "5.50")]),
        ("Annual General Meeting / Final Dividend Rs 0.60 And Special Dividend Rs 1.40 Per Share.",
         [("other",), ("dividend", "0.60"), ("dividend", "1.40")]),
        ("Int Div-Re.0.50 Per Sharepurpose Revised", [("dividend", "0.50")]),
        ("Final Div-Rs.7/- Pr Share", [("dividend", "7")]),
        ("Fin Div-Rs.2.70 Per Share", [("dividend", "2.70")]),
        ("Dividend-Re.1/- Per Share", [("dividend", "1")]),
        ("Interim Dividend - Rs 9 Per Share Special Dividend - Rs 18 Per Share",
         [("dividend", "9"), ("dividend", "18")]),
        ("Interim Dividend - Rs 45 Per Share & Special Dividend - Rs 40 Per Share",
         [("dividend", "45"), ("dividend", "40")]),
        ("Interim Dividend - Rs 100 Per Share/ Special Dividend - Rs 60 Per Share",
         [("dividend", "100"), ("dividend", "60")]),
        ("Annual General Meeting / Dividend Rs.2/- Per Share / Scheme Of Arrangement",
         [("other",), ("dividend", "2"), ("demerger",)]),
        ("Distribution - Rs 5.1 Per Unit Consisting Dividend Rs 2 Per Unit/ Interest Rs 2.2782 "
         "Per Unit/ Return Of Capital Rs 0.8218 Per Unit", [("other",)]),
        ("Extra- Ordinary General Meeting", [("other",)]),
        ("Annual General Meetings", [("other",)]),
        ("Annual Book Closure", [("other",)]),
        ("Scheme Of Demerger", [("demerger",)]),
        ("Buyback", [("buyback",)]),
    ],
)  # fmt: skip
def test_real_subjects(subject: str, expected: list[tuple[str, ...]]) -> None:
    got = []
    for a in parse_subject(subject, D(10)):
        if a.kind in ("split", "consolidation"):
            got.append((a.kind, str(a.ratio_old), str(a.ratio_new)))
        elif a.kind == "bonus":
            got.append((a.kind, str(a.ratio_new), str(a.ratio_old)))
        elif a.kind == "rights":
            got.append((a.kind, str(a.ratio_new), str(a.ratio_old), str(a.issue_price)))
        elif a.kind == "dividend":
            got.append((a.kind, str(a.cash_per_share)))
        else:
            got.append((a.kind,))
    assert got == expected
    assert all(a.parsed for a in parse_subject(subject, D(10)))


def test_rights_with_warrants_are_kept_but_unparsed() -> None:
    [a] = [a for a in parse_subject("Rights-Eq3:11 & 1wrnt:1eq", D(10)) if a.kind == "rights"]
    assert (a.ratio_new, a.ratio_old, a.parsed) == (D(3), D(11), False)
