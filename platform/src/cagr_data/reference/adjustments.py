"""Price adjustment factors (spec 0002, section 7.3): match each listing's corporate actions and
price jumps to ref.price_adjustment rows.

    uv run python -m cagr_data.reference.adjustments 2010-01-01 2026-10-26

An event is a listing's traded session that either follows the ex-date of a price-changing action
or jumps (the close against the previous traded close is below 0.75 or above 1.33). The factor
comes from the parsed actions; the jump confirms it. What the rules can't decide is left pending
for review. A listing and ex-date that already has a row, whatever its status, is never proposed
again, so a rerun adds nothing and a rejected item stays rejected. Reads closes from clean.bar_1d,
so it runs after the daily bars and before the adjustment steps.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings
from cagr_data.quality.checks import SAMPLE, Outcome

JUMP_BELOW, JUMP_ABOVE = 0.75, 1.33
CONFIRM_LOW, CONFIRM_HIGH = 0.8, 1.25  # observed / expected for an automatic split or bonus
EXTRAORDINARY = 0.02  # dividends of at least 2% of the previous close adjust prices (Q3)
CLEAN_TOLERANCE = 0.05
# Bonus b/(a+b), split F2/F1 and consolidation ratios that a jump with no action is matched to.
CLEAN_RATIOS = (1 / 2, 2 / 3, 1 / 3, 1 / 4, 3 / 4, 4 / 5, 1 / 5, 2 / 5, 1 / 10, 2.0, 5.0, 10.0,
                100.0)  # fmt: skip
# NSE's announcements before this are incomplete, so a clean-ratio jump with an ISIN change is
# applied without a person and marked (Q5). Later ones wait for review.
HISTORY_BEFORE = date(2026, 10, 1)
STRUCTURAL = frozenset({"split", "bonus", "consolidation"})
LOOKBACK = timedelta(days=60)  # for the previous traded close at the start of a range


@dataclass(frozen=True)
class Action:
    action_id: int
    kind: str
    ratio_new: Decimal | None
    ratio_old: Decimal | None
    cash_per_share: Decimal | None
    issue_price: Decimal | None
    parsed: bool


@dataclass(frozen=True)
class Event:
    listing_id: int
    ex_date: date
    close_before: float
    close_on: float
    isin_changed: bool
    actions: Sequence[Action] = ()

    @property
    def observed(self) -> float:
        return self.close_on / self.close_before

    @property
    def jump(self) -> bool:
        return not JUMP_BELOW <= self.observed <= JUMP_ABOVE


@dataclass(frozen=True)
class Proposal:
    status: str  # applied or pending
    factor: float | None
    expected: float | None
    action_id: int | None
    note: str


def _structural(a: Action) -> float | None:
    if a.ratio_new is None or a.ratio_old is None:
        return None
    if a.kind == "bonus":  # a new for every b held
        return float(a.ratio_old / (a.ratio_new + a.ratio_old))
    return float(a.ratio_new / a.ratio_old)  # split or consolidation: new face value / old


def _rights(a: Action, close: float) -> float | None:
    if a.ratio_new is None or a.ratio_old is None or a.issue_price is None:
        return None
    new, held, price = float(a.ratio_new), float(a.ratio_old), float(a.issue_price)
    if price >= close:
        return 1.0  # out of the money: no one takes it up at a loss, so no adjustment
    return (held * close + new * price) / (new + held) / close


def nearest_clean(observed: float) -> float | None:
    best = min(CLEAN_RATIOS, key=lambda r: abs(observed / r - 1))
    return best if abs(observed / best - 1) <= CLEAN_TOLERANCE else None


def decide(event: Event, history_before: date = HISTORY_BEFORE) -> Proposal | None:
    """What the rules make of one event, or None when nothing changed the price basis."""
    priced = [a for a in event.actions if a.kind in STRUCTURAL | {"rights", "demerger"}]
    dividends = [a for a in event.actions if a.kind == "dividend"]
    first = priced[0].action_id if priced else None

    if any(a.kind == "demerger" for a in priced):
        return Proposal("pending", None, None, first, "demerger: confirm the factor from prices")
    if any(not a.parsed for a in priced + dividends):
        return Proposal("pending", None, None, first, "an action on this date is unparsed")

    expected = 1.0
    for a in priced:
        f = _structural(a) if a.kind in STRUCTURAL else _rights(a, event.close_before)
        if f is None:
            return Proposal("pending", None, None, a.action_id, f"{a.kind} without a ratio")
        expected *= f
    cash = sum(float(a.cash_per_share or 0) for a in dividends)
    if cash and cash / event.close_before >= EXTRAORDINARY:
        expected *= (event.close_before - cash) / event.close_before
        first = first or max(dividends, key=lambda a: a.cash_per_share or 0).action_id

    if any(a.kind in STRUCTURAL for a in priced):
        if CONFIRM_LOW <= event.observed / expected <= CONFIRM_HIGH:
            return Proposal("applied", expected, expected, first, "confirmed by the close")
        return Proposal("pending", None, expected, first, "the close disagrees with the action")
    if expected != 1.0 and not event.jump:
        kind = "rights" if priced else "extraordinary dividend"
        return Proposal("applied", expected, expected, first, f"{kind} from the parsed amount")
    if not event.jump:
        return None

    clean = nearest_clean(event.observed)
    if clean is not None and event.isin_changed and event.ex_date < history_before:
        return Proposal("applied", clean, None, None,
                        "clean-ratio jump with an ISIN change and no action, applied from"
                        " history (Q5)")  # fmt: skip
    why = "near a clean ratio" if clean is not None else "no action explains it"
    return Proposal("pending", None, expected if expected != 1.0 else None, first,
                    f"price jump, {why}")  # fmt: skip


@dataclass
class AdjustmentsResult:
    events: int = 0
    applied: int = 0
    pending: int = 0
    by_note: dict[str, int] = field(default_factory=dict)


def _events(conn: Connection, start: date, end: date) -> list[Event]:
    """Events from start to end on listings that don't have a row for the ex-date yet."""
    rows = conn.execute(
        """
        WITH b AS (
            SELECT b.listing_id, b.date, b.close::double precision AS close,
                   lag(b.close::double precision) OVER w AS prev,
                   lag(b.date) OVER w AS prev_date
              FROM clean.bar_1d b JOIN ref.listing l USING (listing_id)
             WHERE l.asset_class <> 'index' AND b.date BETWEEN %(from)s AND %(end)s
            WINDOW w AS (PARTITION BY b.listing_id ORDER BY b.date)),
        acts AS (
            SELECT b.listing_id, b.date, max(c.ex_date) AS ex_date,
                   jsonb_agg(jsonb_build_array(c.action_id, c.kind, c.ratio_new, c.ratio_old,
                             c.cash_per_share, c.issue_price, c.parse_status = 'parsed')
                             ORDER BY c.seq, c.action_id) AS actions
              FROM b JOIN ref.corporate_action c
                ON c.listing_id = b.listing_id AND c.ex_date > b.prev_date
               AND c.ex_date <= b.date
               AND c.kind IN ('split', 'bonus', 'consolidation', 'rights', 'demerger',
                              'dividend')
             WHERE b.date BETWEEN %(start)s AND %(end)s
             GROUP BY 1, 2)
        SELECT b.listing_id, coalesce(a.ex_date, b.date), b.prev, b.close,
               EXISTS (SELECT 1 FROM ref.listing_isin i
                        WHERE i.listing_id = b.listing_id AND i.valid_from > b.prev_date
                          AND i.valid_from <= b.date
                          AND i.valid_from > (SELECT min(valid_from) FROM ref.listing_isin j
                                               WHERE j.listing_id = b.listing_id)),
               coalesce(a.actions, '[]'::jsonb)
          FROM b LEFT JOIN acts a USING (listing_id, date)
         WHERE b.date BETWEEN %(start)s AND %(end)s AND b.prev > 0
           AND (a.actions IS NOT NULL
                OR b.close / b.prev NOT BETWEEN %(below)s AND %(above)s)
           AND NOT EXISTS (SELECT 1 FROM ref.price_adjustment p
                            WHERE p.listing_id = b.listing_id
                              AND p.ex_date = coalesce(a.ex_date, b.date))
         ORDER BY 2, 1
        """,
        {"from": start - LOOKBACK, "start": start, "end": end, "below": JUMP_BELOW,
         "above": JUMP_ABOVE},
    ).fetchall()  # fmt: skip
    # Actions with ex-dates between the same two bars are one event on the latest ex-date.
    return [
        Event(listing_id, ex_date, prev, close, isin_changed, tuple(_action(a) for a in actions))
        for listing_id, ex_date, prev, close, isin_changed, actions in rows
    ]


def _action(a: list[Any]) -> Action:
    def dec(v: Any) -> Decimal | None:
        return None if v is None else Decimal(str(v))

    return Action(int(a[0]), a[1], dec(a[2]), dec(a[3]), dec(a[4]), dec(a[5]), bool(a[6]))


def build_adjustments(
    conn: Connection, start: date, end: date, history_before: date = HISTORY_BEFORE
) -> AdjustmentsResult:
    """Propose an adjustment for every new event from start to end, in the caller's
    transaction."""
    result = AdjustmentsResult()
    inserts: list[tuple[Any, ...]] = []
    for event in _events(conn, start, end):
        result.events += 1
        p = decide(event, history_before)
        if p is None:
            continue
        applied = p.status == "applied"
        result.applied += applied
        result.pending += not applied
        result.by_note[p.note] = result.by_note.get(p.note, 0) + 1
        inserts.append((
            event.listing_id, event.ex_date, p.factor, event.observed, p.expected,
            event.close_before, event.close_on, p.action_id, p.status, applied,
            "now" if applied else None, p.note,
        ))  # fmt: skip
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ref.price_adjustment (listing_id, ex_date, factor, observed_factor,
                   expected_factor, close_before, close_on, action_id, status, auto,
                   proposed_by, applied_at, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'rules', %s, %s)
            """,
            inserts,
        )
    return result


def adjustment_explained(conn: Connection, start: date, end: date) -> Outcome:
    """Every jump from start to end has an adjustment row, applied, pending or decided (check
    adjustment_explained)."""
    rows = conn.execute(
        """
        WITH b AS (
            SELECT b.listing_id, b.date, b.close,
                   lag(b.close) OVER w AS prev, lag(b.date) OVER w AS prev_date
              FROM clean.bar_1d b JOIN ref.listing l USING (listing_id)
             WHERE l.asset_class <> 'index' AND b.date BETWEEN %s AND %s
            WINDOW w AS (PARTITION BY b.listing_id ORDER BY b.date))
        SELECT b.date, b.listing_id FROM b
         WHERE b.date BETWEEN %s AND %s AND b.prev > 0
           AND b.close / b.prev NOT BETWEEN %s AND %s
           AND NOT EXISTS (SELECT 1 FROM ref.price_adjustment p
                            WHERE p.listing_id = b.listing_id AND p.ex_date > b.prev_date
                              AND p.ex_date <= b.date)
         ORDER BY 1, 2
        """,
        (start - LOOKBACK, end, start, end, JUMP_BELOW, JUMP_ABOVE),
    ).fetchall()
    if not rows:
        return Outcome("adjustment_explained", True)
    sample = ["/".join(str(v) for v in r) for r in rows[:SAMPLE]]
    return Outcome("adjustment_explained", False, {"count": len(rows), "sample": sample},
                   f"{len(rows)} jumps with no adjustment")  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("start", type=date.fromisoformat)
    parser.add_argument("end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        result = build_adjustments(conn, args.start, args.end)
        conn.commit()
    print(f"{result.events} events: {result.applied} applied, {result.pending} pending")
    for note, n in sorted(result.by_note.items(), key=lambda kv: -kv[1]):
        print(f"  {n:6d}  {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
