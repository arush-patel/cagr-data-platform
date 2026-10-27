"""The rules that turn an event into an adjustment (spec 0002, section 7.3)."""

from datetime import date
from decimal import Decimal as D

import pytest

from cagr_data.reference.adjustments import Action, Event, decide, nearest_clean

DAY = date(2024, 10, 28)


def act(kind: str, new: str | None = None, old: str | None = None, cash: str | None = None,
        price: str | None = None, parsed: bool = True, action_id: int = 1) -> Action:  # fmt: skip
    return Action(action_id, kind, D(new) if new else None, D(old) if old else None,
                  D(cash) if cash else None, D(price) if price else None, parsed)  # fmt: skip


def event(before: float, on: float, *actions: Action, isin_changed: bool = False,
          day: date = DAY) -> Event:  # fmt: skip
    return Event(7, day, before, on, isin_changed, actions)


def test_bonus_confirmed_by_the_close_is_applied() -> None:
    # RELIANCE's 1:1 bonus: 2655.70 to 1334.35.
    p = decide(event(2655.70, 1334.35, act("bonus", "1", "1")))
    assert p is not None and (p.status, p.factor, p.action_id) == ("applied", 0.5, 1)


@pytest.mark.parametrize(
    ("action", "factor"),
    [
        (act("split", "1", "10"), 0.1),
        (act("split", "2", "5"), 0.4),
        (act("bonus", "1", "2"), 2 / 3),
        (act("bonus", "3", "1"), 0.25),
    ],
)
def test_structural_factors(action: Action, factor: float) -> None:
    p = decide(event(100, 100 * factor * 1.03, action))
    assert p is not None and p.status == "applied" and p.factor == pytest.approx(factor)


def test_consolidation_raises_the_old_prices() -> None:
    p = decide(event(5, 51, act("consolidation", "10", "1")))
    assert p is not None and (p.status, p.factor) == ("applied", 10.0)


def test_split_the_close_disagrees_with_is_pending() -> None:
    p = decide(event(100, 98, act("split", "1", "10")))
    assert p is not None and (p.status, p.factor, p.expected) == ("pending", None, 0.1)


def test_two_actions_on_one_date_compound() -> None:
    p = decide(event(100, 10, act("bonus", "1", "1"), act("split", "1", "5", action_id=2)))
    assert p is not None and p.status == "applied" and p.factor == pytest.approx(0.1)


def test_rights_use_terp() -> None:
    # 1 new for every 4 held at 60 against a close of 100: TERP = (4·100 + 60) / 5 = 92.
    p = decide(event(100, 93, act("rights", "1", "4", price="60")))
    assert p is not None and p.status == "applied" and p.factor == pytest.approx(0.92)


def test_rights_out_of_the_money_change_nothing() -> None:
    assert decide(event(100, 99, act("rights", "1", "4", price="120"))) is None


def test_dividends_adjust_only_from_two_percent() -> None:
    assert decide(event(100, 99, act("dividend", cash="1.5"))) is None
    p = decide(event(100, 97, act("dividend", cash="1.5"), act("dividend", cash="1", action_id=2)))
    assert p is not None and p.status == "applied" and p.factor == pytest.approx(0.975)


def test_a_dividend_at_or_above_the_close_waits_for_a_person() -> None:
    # A misread amount (Rs 125 on a close of 81.95) must not become a negative factor.
    p = decide(event(81.95, 79.95, act("dividend", cash="125")))
    assert p is not None and (p.status, p.factor, p.expected) == ("pending", None, None)


def test_demerger_and_unparsed_actions_wait_for_a_person() -> None:
    p = decide(event(100, 70, act("demerger")))
    assert p is not None and (p.status, p.factor) == ("pending", None)
    p = decide(event(100, 50, act("bonus", parsed=False)))
    assert p is not None and p.status == "pending"


def test_a_jump_with_no_action() -> None:
    # A clean ratio with an ISIN change, in history: applied and marked.
    p = decide(event(100, 51, isin_changed=True, day=date(2015, 3, 2)))
    assert p is not None and (p.status, p.factor, p.action_id) == ("applied", 0.5, None)
    assert "Q5" in p.note
    # The same today waits for review.
    p = decide(event(100, 51, isin_changed=True, day=date(2026, 10, 27)))
    assert p is not None and p.status == "pending"
    # Without an ISIN change, or off a clean ratio, it waits too.
    assert decide(event(100, 51, day=date(2015, 3, 2))).status == "pending"  # type: ignore[union-attr]
    p = decide(event(100, 60, isin_changed=True, day=date(2015, 3, 2)))
    assert p is not None and p.status == "pending"


def test_an_ordinary_day_is_nothing() -> None:
    assert decide(event(100, 104)) is None


def test_nearest_clean() -> None:
    assert nearest_clean(0.51) == 0.5
    assert nearest_clean(0.098) == 0.1
    assert nearest_clean(101) == 100.0
    assert nearest_clean(0.6) is None
