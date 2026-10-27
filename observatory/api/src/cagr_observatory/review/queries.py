"""SQL for the review page, over spec 0002's tables. Reads only, as cagr_read."""

from datetime import date
from typing import Any, Literal

from psycopg.rows import dict_row

from cagr.db import Connection

# Sessions an item may wait before the review_waiting check warns (spec 0002, section 8).
MAX_WAITING_SESSIONS = 3

Status = Literal["pending", "decided"]

_WAITING = """
    (SELECT count(*) FROM ref.calendar_session s
      WHERE s.venue_id = 'XNSE' AND s.is_session
        AND s.date > {t}.created_at::date AND s.date <= current_date)::int AS waiting_sessions
"""

_SYMBOL_ON = """
    (SELECT symbol FROM ref.listing_symbol
      WHERE listing_id = {t}.listing_id AND valid_from <= {d}
      ORDER BY valid_from DESC LIMIT 1) AS symbol
"""

ADJUSTMENTS = f"""
SELECT a.adjustment_id AS id, a.listing_id, l.name, l.board, a.ex_date AS date, a.status,
       a.factor, a.expected_factor, a.observed_factor, a.close_before, a.close_on,
       a.proposed_by, a.decided_by, a.decided_at, a.note,
       c.kind AS action_kind, c.subject, c.ratio_new, c.ratio_old, c.cash_per_share,
       c.issue_price,
       (SELECT isin FROM ref.listing_isin
         WHERE listing_id = a.listing_id AND valid_from < a.ex_date
         ORDER BY valid_from DESC LIMIT 1) AS isin_before,
       (SELECT isin FROM ref.listing_isin
         WHERE listing_id = a.listing_id AND valid_from <= a.ex_date
         ORDER BY valid_from DESC LIMIT 1) AS isin_on,
       {_SYMBOL_ON.format(t="a", d="a.ex_date")},
       {_WAITING.format(t="a")}
  FROM ref.price_adjustment a
  JOIN ref.listing l USING (listing_id)
  LEFT JOIN ref.corporate_action c USING (action_id)
"""

CHANGES = f"""
SELECT ch.change_id AS id, ch.listing_id, l.name, l.board, ch.effective_date AS date,
       ch.status, ch.kind AS change_kind, ch.payload, ch.evidence,
       ch.proposed_by, ch.decided_by, ch.decided_at, ch.note,
       {_SYMBOL_ON.format(t="ch", d="ch.effective_date")},
       {_WAITING.format(t="ch")}
  FROM ref.listing_change ch
  JOIN ref.listing l USING (listing_id)
"""

# Pending: oldest first. Decided: what a person decided, newest first; rows the rules applied
# on their own are not review history.
_FILTER = {
    "pending": ("WHERE {t}.status = 'pending'", "ORDER BY {t}.created_at, id"),
    "decided": (
        "WHERE {t}.status <> 'pending' AND {t}.decided_by IS NOT NULL",
        "ORDER BY {t}.decided_at DESC, id DESC LIMIT 200",
    ),
}


def _rows(conn: Connection, query: str, params: Any = None) -> list[dict[str, Any]]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(query, params)
        return cur.fetchall()


def adjustments(conn: Connection, status: Status) -> list[dict[str, Any]]:
    where, order = (s.format(t="a") for s in _FILTER[status])
    return _rows(conn, f"{ADJUSTMENTS} {where} {order}")


def changes(conn: Connection, status: Status) -> list[dict[str, Any]]:
    where, order = (s.format(t="ch") for s in _FILTER[status])
    return _rows(conn, f"{CHANGES} {where} {order}")


def adjustment(conn: Connection, adjustment_id: int) -> dict[str, Any] | None:
    rows = _rows(conn, f"{ADJUSTMENTS} WHERE a.adjustment_id = %s", (adjustment_id,))
    return rows[0] if rows else None


def change(conn: Connection, change_id: int) -> dict[str, Any] | None:
    rows = _rows(conn, f"{CHANGES} WHERE ch.change_id = %s", (change_id,))
    return rows[0] if rows else None


def listings(conn: Connection, listing_ids: list[int]) -> list[dict[str, Any]]:
    """Summaries from the stock master, in the order asked."""
    rows = _rows(
        conn,
        "SELECT listing_id, symbol, isin, name, asset_class, board, status, first_session,"
        " last_session FROM ref.listing_current WHERE listing_id = ANY(%s)",
        (listing_ids,),
    )
    by_id = {r["listing_id"]: r for r in rows}
    return [by_id[i] for i in listing_ids if i in by_id]


def search_listings(conn: Connection, q: str, limit: int = 20) -> list[dict[str, Any]]:
    """Listings by symbol or ISIN (exact, then prefix) or name (contains)."""
    term = q.strip().upper()
    return _rows(
        conn,
        "SELECT listing_id, symbol, isin, name, asset_class, board, status, first_session,"
        " last_session FROM ref.listing_current"
        " WHERE upper(symbol) LIKE %(p)s OR isin = %(t)s OR upper(name) LIKE %(c)s"
        " ORDER BY (upper(symbol) = %(t)s) DESC, (status = 'active') DESC, symbol"
        " LIMIT %(n)s",
        {"t": term, "p": term + "%", "c": "%" + term + "%", "n": limit},
    )


def closes_around(
    conn: Connection, listing_id: int, ex_date: date, sessions: int = 20
) -> list[dict[str, Any]]:
    """The listing's unadjusted closes, `sessions` XNSE sessions either side of ex_date, from
    spec 0003's clean.bar_1d_unadjusted (one bar per listing and session, never T0)."""
    return _rows(
        conn,
        """
        WITH window_ AS (
            SELECT min(date) AS lo, max(date) AS hi FROM (
                (SELECT date FROM ref.calendar_session
                  WHERE venue_id = 'XNSE' AND is_session AND date < %(ex)s
                  ORDER BY date DESC LIMIT %(n)s)
                UNION ALL
                (SELECT date FROM ref.calendar_session
                  WHERE venue_id = 'XNSE' AND is_session AND date >= %(ex)s
                  ORDER BY date LIMIT %(n)s)
            ) d
        )
        SELECT b.date, b.close
          FROM clean.bar_1d_unadjusted b CROSS JOIN window_ w
         WHERE b.listing_id = %(id)s AND b.date BETWEEN w.lo AND w.hi
         ORDER BY b.date
        """,
        {"ex": ex_date, "n": sessions, "id": listing_id},
    )
