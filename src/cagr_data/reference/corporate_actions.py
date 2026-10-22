"""Corporate actions (D3): load, confirm against the exchange's own previous close, apply.

load     NSE's corporate-actions feed → clean.corporate_action (proposed, or needs_review)
confirm  a proposed action with a price effect is confirmed when the ex-date price moved by its factor:
         NSE does not adjust the previous close in the bhavcopy (checked on the real 2024 files, both
         formats), so the evidence is the ex-date open or close against our last close. Actions with
         no price effect confirm at once
detect   a previous-close break with no action on file becomes a detected action for review. Because
         NSE's previous close is unadjusted, a break means a data problem (a wrong mapping, a missed
         or revised file), not a split; an unannounced split shows up as a return outlier instead
apply    clean.apply_corporate_action rescales history once and records the dirty range
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
import psycopg

from cagr_data.reference.ca_parser import parse_subject

TOLERANCE = 0.01
# On the ex-date the price must sit nearer the factor than 1 (geometric midpoint), and the move left
# after the factor must fit a normal day: wider than the 20% band, since splits and bonuses trade freely.
MAX_ADJUSTED_MOVE = 0.30


@dataclass
class LoadResult:
    inserted: int = 0
    unchanged: int = 0
    needs_review: int = 0
    unknown_isin: list[str] = field(default_factory=list)


def load(
    conn: psycopg.Connection[Any], venue_id: int, actions: pd.DataFrame, source: str = "exchange"
) -> LoadResult:
    """Insert newly announced actions. An action already on file (listing, ex-date, kind) is left alone."""
    res = LoadResult()
    isin_map = {
        r["value"]: r["listing_id"]
        for r in conn.execute(
            "SELECT ii.value, l.listing_id FROM ref.instrument_identifier ii JOIN ref.listing l USING (instrument_id) "
            "WHERE ii.id_type = 'isin' AND l.venue_id = %s",
            (venue_id,),
        )
    }
    symbol_map = {
        r["ticker"]: r["listing_id"]
        for r in conn.execute(
            "SELECT ticker, listing_id FROM ref.listing_symbol WHERE venue_id = %s AND valid_to IS NULL",
            (venue_id,),
        )
    }
    for a in actions.itertuples(index=False):
        if a.ex_date is None or pd.isna(a.ex_date):
            continue
        lid = isin_map.get(a.isin) if a.isin else None
        if lid is None:
            lid = symbol_map.get(a.symbol)
        if lid is None:
            res.unknown_isin.append(a.isin or a.symbol)
            continue
        for p in parse_subject(a.subject):
            cur = conn.execute(
                "INSERT INTO clean.corporate_action (listing_id, ex_date, kind, ratio_new, ratio_old, price_factor, "
                "cash_amount, cash_currency, record_date, source, subject_raw, parse_status, status) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'proposed') "
                "ON CONFLICT (listing_id, ex_date, kind) DO NOTHING",
                (
                    lid, a.ex_date, p.kind, p.ratio_new, p.ratio_old, p.price_factor,
                    p.cash_amount, "INR" if p.cash_amount is not None else None,
                    None if pd.isna(a.record_date) else a.record_date, source, a.subject, p.parse_status,
                ),
            )  # fmt: skip
            if cur.rowcount:
                res.inserted += 1
                res.needs_review += p.parse_status == "needs_review"
            else:
                res.unchanged += 1
    return res


_EVIDENCE_SQL = """
WITH primary_vendor AS (
    SELECT f.vendor_id FROM ref.feed f
    WHERE f.venue_id = %(venue)s AND f.dataset = 'eod_bar' AND f.asset_class = 'equity' AND f.priority = 1
),
latest AS (
    SELECT DISTINCT ON (b.listing_id, b.session_date) b.listing_id, b.session_date, b.open, b.close
    FROM raw.vendor_bar b JOIN primary_vendor pv USING (vendor_id)
    WHERE b.listing_id = ANY(%(listings)s)
    ORDER BY b.listing_id, b.session_date, b.revision DESC
)
SELECT cur.listing_id, cur.session_date, cur.open, cur.close,
       prev.close AS our_prev_close, prev.session_date AS prev_session
FROM latest cur
JOIN LATERAL (
    SELECT p.close, p.session_date FROM latest p
    WHERE p.listing_id = cur.listing_id AND p.session_date < cur.session_date AND p.close IS NOT NULL
    ORDER BY p.session_date DESC LIMIT 1
) prev ON true
WHERE (cur.listing_id, cur.session_date) IN (SELECT * FROM unnest(%(listings)s::int[], %(dates)s::date[]))
"""


def move_matches_factor(ex_price: float, prev_close: float, factor: float) -> bool:
    """Did the price on the ex-date move by `factor` from the previous close, rather than not at all?"""
    if ex_price <= 0 or prev_close <= 0 or factor <= 0 or factor == 1:
        return False
    left = math.log(ex_price / prev_close) - math.log(factor)
    return abs(left) < abs(math.log(factor)) / 2 and abs(left) <= math.log(1 + MAX_ADJUSTED_MOVE)


def confirm(conn: psycopg.Connection[Any], venue_id: int) -> dict[str, int]:
    """Confirm proposed, parsed actions whose ex-date price moved by their factor."""
    out = {"confirmed": 0, "waiting": 0, "mismatch": 0}
    conn.execute(
        "UPDATE clean.corporate_action ca SET status = 'confirmed' FROM ref.listing l "
        "WHERE l.listing_id = ca.listing_id AND l.venue_id = %s AND ca.status = 'proposed' "
        "AND ca.parse_status IN ('parsed','manual') AND ca.price_factor = 1",
        (venue_id,),
    )
    pending = conn.execute(
        "SELECT ca.action_id, ca.listing_id, ca.ex_date, ca.price_factor FROM clean.corporate_action ca "
        "JOIN ref.listing l USING (listing_id) WHERE l.venue_id = %s AND ca.status = 'proposed' "
        "AND ca.parse_status IN ('parsed','manual') AND ca.price_factor <> 1",
        (venue_id,),
    ).fetchall()
    if not pending:
        return out
    ev = {
        (r["listing_id"], r["session_date"]): r
        for r in conn.execute(
            _EVIDENCE_SQL,
            {
                "venue": venue_id,
                "listings": [p["listing_id"] for p in pending],
                "dates": [p["ex_date"] for p in pending],
            },
        )
    }
    # A split and a bonus on one ex-date (VERTOZ, AARTECH in 2024) move the price by their product.
    groups: dict[tuple[int, date], list[dict[str, Any]]] = defaultdict(list)
    for p in pending:
        groups[(p["listing_id"], p["ex_date"])].append(p)
    for key, acts in groups.items():
        e = ev.get(key)
        if e is None or not e["our_prev_close"] or not (e["open"] or e["close"]):
            out["waiting"] += len(acts)  # ex-date not traded yet
            continue
        factor = math.prod(a["price_factor"] for a in acts)
        if any(move_matches_factor(px, e["our_prev_close"], factor) for px in (e["open"], e["close"]) if px):
            conn.execute(
                "UPDATE clean.corporate_action SET status = 'confirmed' WHERE action_id = ANY(%s)",
                ([a["action_id"] for a in acts],),
            )
            out["confirmed"] += len(acts)
        else:
            out["mismatch"] += len(acts)
    return out


def continuity_breaks(
    conn: psycopg.Connection[Any], venue_id: int, sessions: list[date]
) -> list[dict[str, Any]]:
    """Bars whose vendor previous close disagrees with our previous raw close and no action explains it."""
    return [
        dict(r)
        for r in conn.execute(
            """
            WITH pv AS (SELECT vendor_id FROM ref.feed WHERE venue_id = %(venue)s AND dataset = 'eod_bar'
                        AND asset_class = 'equity' AND priority = 1),
            cur AS (
                SELECT DISTINCT ON (b.listing_id) b.listing_id, b.session_date, b.prev_close
                FROM raw.vendor_bar b JOIN pv USING (vendor_id)
                WHERE b.session_date = ANY(%(sessions)s)
                ORDER BY b.listing_id, b.session_date, b.revision DESC
            )
            SELECT cur.listing_id, cur.session_date, cur.prev_close AS vendor_prev_close,
                   prev.close AS our_prev_close, cur.prev_close / prev.close AS implied_factor
            FROM cur
            JOIN LATERAL (
                SELECT b.close FROM raw.vendor_bar b JOIN pv USING (vendor_id)
                WHERE b.listing_id = cur.listing_id AND b.session_date < cur.session_date AND b.close IS NOT NULL
                ORDER BY b.session_date DESC, b.revision DESC LIMIT 1
            ) prev ON true
            WHERE cur.prev_close > 0 AND prev.close > 0
              AND abs(cur.prev_close / prev.close - 1) > %(tol)s
              -- explained only by an action someone (or the evidence) confirmed; a detected or
              -- unparsed one still needs a person, so the break keeps failing the check until then
              AND NOT EXISTS (SELECT 1 FROM clean.corporate_action ca
                              WHERE ca.listing_id = cur.listing_id AND ca.ex_date = cur.session_date
                                AND ca.status IN ('confirmed','applied'))
            """,
            {"venue": venue_id, "sessions": sessions, "tol": TOLERANCE},
        )
    ]


def record_detected(conn: psycopg.Connection[Any], breaks: list[dict[str, Any]]) -> int:
    """Each unexplained break becomes a detected action waiting for a person to name and confirm it."""
    n = 0
    for b in breaks:
        n += conn.execute(
            "INSERT INTO clean.corporate_action (listing_id, ex_date, kind, price_factor, source, subject_raw, "
            "parse_status, status) VALUES (%s, %s, 'other', %s, 'detected', %s, 'needs_review', 'proposed') "
            "ON CONFLICT (listing_id, ex_date, kind) DO NOTHING",
            (
                b["listing_id"], b["session_date"], b["implied_factor"],
                f"previous close {b['vendor_prev_close']} vs our {b['our_prev_close']}",
            ),
        ).rowcount  # fmt: skip
    return n


def apply_confirmed(conn: psycopg.Connection[Any], venue_id: int, through: date) -> int:
    """Apply every confirmed action with an ex-date on or before `through`, oldest first."""
    ids = [
        r["action_id"]
        for r in conn.execute(
            "SELECT ca.action_id FROM clean.corporate_action ca JOIN ref.listing l USING (listing_id) "
            "WHERE l.venue_id = %s AND ca.status = 'confirmed' AND ca.ex_date <= %s ORDER BY ca.ex_date, ca.action_id",
            (venue_id, through),
        )
    ]
    for i in ids:
        conn.execute("SELECT clean.apply_corporate_action(%s)", (i,))
    return len(ids)
