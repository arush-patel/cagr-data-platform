"""clean.bar_1d / bar_1w / bar_1mo: one trusted row per listing per session, rebuilt from raw.

A clean bar is always a pure function of raw bars, applied corporate actions and the calendar, so
any range can be rebuilt at any time and gives the same answer:

  adjusted price = raw price × adj_factor, where adj_factor is the product of the price factors
  of applied actions whose ex-date is after the bar. Untraded sessions are kept with NULL prices
  and is_traded = false; deleting them would change window lengths and cross-sections.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg

QA_NOT_TRADED = 1 << 0
QA_OHLC_INCONSISTENT = 1 << 1
QA_STALE_PRICE = 1 << 2
QA_RETURN_OUTLIER = 1 << 3
QA_CORPORATE_ACTION_DAY = 1 << 4
QA_VENDOR_REVISED = 1 << 6
QA_SPECIAL_SESSION = 1 << 8

OUTLIER_LOG_RETURN = 0.25
STALE_SESSIONS = 5

_BUILD_1D = """
WITH pv AS (
    SELECT vendor_id FROM ref.feed
    WHERE venue_id = %(venue)s AND dataset = 'eod_bar' AND asset_class = 'equity' AND priority = 1
),
lst AS (
    SELECT l.listing_id, l.delisted_on,
           (SELECT min(b.session_date) FROM raw.vendor_bar b, pv
             WHERE b.listing_id = l.listing_id AND b.vendor_id = pv.vendor_id) AS first_raw
    FROM ref.listing l
    WHERE l.venue_id = %(venue)s AND (%(listings)s::int[] IS NULL OR l.listing_id = ANY(%(listings)s::int[]))
),
grid AS (
    SELECT cs.session_date, cs.is_special, l.listing_id
    FROM ref.calendar_session cs
    JOIN ref.venue v ON v.calendar_id = cs.calendar_id AND v.venue_id = %(venue)s
    JOIN lst l ON cs.session_date >= l.first_raw AND (l.delisted_on IS NULL OR cs.session_date < l.delisted_on)
    WHERE cs.session_date BETWEEN %(start)s AND %(end)s
),
r AS (
    SELECT DISTINCT ON (b.listing_id, b.session_date)
           b.listing_id, b.session_date, b.revision, b.vendor_id,
           nullif(b.open, 0) AS open, nullif(b.high, 0) AS high, nullif(b.low, 0) AS low,
           nullif(b.close, 0) AS close, b.adj_close, b.volume, b.quote_volume
    FROM raw.vendor_bar b JOIN pv USING (vendor_id)
    WHERE b.session_date BETWEEN %(start)s AND %(end)s
      AND b.listing_id IN (SELECT listing_id FROM lst)
    ORDER BY b.listing_id, b.session_date, b.revision DESC
),
x AS (
    SELECT g.session_date, g.listing_id, g.is_special, r.revision, r.vendor_id, r.open, r.high, r.low,
           r.close, r.adj_close, r.volume, r.quote_volume,
           coalesce((SELECT exp(sum(ln(ca.price_factor))) FROM clean.corporate_action ca
                      WHERE ca.listing_id = g.listing_id AND ca.status = 'applied' AND ca.ex_date > g.session_date),
                    1.0) AS f,
           EXISTS (SELECT 1 FROM clean.corporate_action ca
                    WHERE ca.listing_id = g.listing_id AND ca.status = 'applied' AND ca.ex_date = g.session_date
                      AND ca.price_factor <> 1) AS ca_day
    FROM grid g LEFT JOIN r ON r.listing_id = g.listing_id AND r.session_date = g.session_date
)
INSERT INTO clean.bar_1d AS t (bar_date, listing_id, open, high, low, close, volume, volume_adj, quote_volume,
                               raw_close, raw_adj_close, adj_factor, is_traded, qa_flags, source_vendor_id, updated_at)
SELECT x.session_date, x.listing_id,
       x.open * x.f, x.high * x.f, x.low * x.f, x.close * x.f,
       x.volume, x.volume / x.f, x.quote_volume,
       x.close, x.adj_close, x.f,
       coalesce(x.volume, 0) > 0 AND x.close IS NOT NULL,
       (CASE WHEN coalesce(x.volume, 0) > 0 AND x.close IS NOT NULL THEN 0 ELSE %(not_traded)s END)
     | (CASE WHEN x.low > least(x.open, x.close) OR x.high < greatest(x.open, x.close) OR x.low > x.high
             THEN %(ohlc)s ELSE 0 END)
     | (CASE WHEN x.is_special THEN %(special)s ELSE 0 END)
     | (CASE WHEN x.revision > 1 THEN %(revised)s ELSE 0 END)
     | (CASE WHEN x.ca_day THEN %(ca_day)s ELSE 0 END),
       CASE WHEN x.close IS NULL THEN NULL ELSE x.vendor_id END,
       now()
FROM x
ON CONFLICT (bar_date, listing_id) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
    volume = EXCLUDED.volume, volume_adj = EXCLUDED.volume_adj, quote_volume = EXCLUDED.quote_volume,
    raw_close = EXCLUDED.raw_close, raw_adj_close = EXCLUDED.raw_adj_close, adj_factor = EXCLUDED.adj_factor,
    is_traded = EXCLUDED.is_traded, qa_flags = EXCLUDED.qa_flags, source_vendor_id = EXCLUDED.source_vendor_id,
    updated_at = now()
WHERE (t.open, t.high, t.low, t.close, t.volume, t.adj_factor, t.qa_flags, t.is_traded)
      IS DISTINCT FROM (EXCLUDED.open, EXCLUDED.high, EXCLUDED.low, EXCLUDED.close, EXCLUDED.volume,
                        EXCLUDED.adj_factor, EXCLUDED.qa_flags, EXCLUDED.is_traded)
"""

# Flags that need neighbouring bars. Looks back far enough to see five traded sessions.
_FLAGS_1D = """
WITH w AS (
    SELECT bar_date, listing_id,
           close / lag(close) OVER win AS ret,
           count(*) OVER last_n AS n, min(close) OVER last_n AS lo, max(close) OVER last_n AS hi
    FROM clean.bar_1d
    WHERE is_traded AND bar_date BETWEEN %(lookback)s AND %(end)s
      AND (%(listings)s::int[] IS NULL OR listing_id = ANY(%(listings)s::int[]))
      AND listing_id IN (SELECT listing_id FROM ref.listing WHERE venue_id = %(venue)s)
    WINDOW win AS (PARTITION BY listing_id ORDER BY bar_date),
           last_n AS (PARTITION BY listing_id ORDER BY bar_date ROWS BETWEEN %(stale_back)s PRECEDING AND CURRENT ROW)
)
UPDATE clean.bar_1d b
SET qa_flags = (b.qa_flags & ~(%(stale)s | %(outlier)s))
             | (CASE WHEN w.n = %(stale_n)s AND w.lo = w.hi THEN %(stale)s ELSE 0 END)
             | (CASE WHEN abs(ln(w.ret)) > %(outlier_lr)s THEN %(outlier)s ELSE 0 END)
FROM w
WHERE b.bar_date = w.bar_date AND b.listing_id = w.listing_id AND b.bar_date BETWEEN %(start)s AND %(end)s
  AND b.qa_flags IS DISTINCT FROM (
        (b.qa_flags & ~(%(stale)s | %(outlier)s))
      | (CASE WHEN w.n = %(stale_n)s AND w.lo = w.hi THEN %(stale)s ELSE 0 END)
      | (CASE WHEN abs(ln(w.ret)) > %(outlier_lr)s THEN %(outlier)s ELSE 0 END))
"""

_PERIODS = """
SELECT DISTINCT cs.{end_col} AS bar_date
FROM ref.calendar_session cs JOIN ref.venue v ON v.calendar_id = cs.calendar_id AND v.venue_id = %(venue)s
WHERE cs.session_date BETWEEN %(start)s AND %(end)s
"""

_RESAMPLE_DELETE = """
DELETE FROM clean.bar_{tf} t
WHERE t.bar_date = ANY(%(periods)s)
  AND t.listing_id IN (SELECT listing_id FROM ref.listing WHERE venue_id = %(venue)s
                       AND (%(listings)s::int[] IS NULL OR listing_id = ANY(%(listings)s::int[])))
"""

_RESAMPLE = """
INSERT INTO clean.bar_{tf} (bar_date, listing_id, period_start, open, high, low, close, volume, sessions_traded,
                           is_complete)
SELECT cs.{end_col}, b.listing_id, min(b.bar_date),
       (array_agg(b.open ORDER BY b.bar_date) FILTER (WHERE b.is_traded))[1],
       max(b.high) FILTER (WHERE b.is_traded),
       min(b.low) FILTER (WHERE b.is_traded),
       (array_agg(b.close ORDER BY b.bar_date DESC) FILTER (WHERE b.is_traded))[1],
       sum(b.volume_adj) FILTER (WHERE b.is_traded),
       count(*) FILTER (WHERE b.is_traded),
       max(b.bar_date) = cs.{end_col}
FROM clean.bar_1d b
JOIN ref.listing l ON l.listing_id = b.listing_id AND l.venue_id = %(venue)s
JOIN ref.calendar_session cs ON cs.calendar_id = (SELECT calendar_id FROM ref.venue WHERE venue_id = %(venue)s)
                            AND cs.session_date = b.bar_date
WHERE cs.{end_col} = ANY(%(periods)s)
  AND (%(listings)s::int[] IS NULL OR b.listing_id = ANY(%(listings)s::int[]))
GROUP BY cs.{end_col}, b.listing_id
"""


def _params(venue_id: int, start: date, end: date, listing_ids: list[int] | None) -> dict[str, Any]:
    return {"venue": venue_id, "start": start, "end": end, "listings": listing_ids}


def build_1d(
    conn: psycopg.Connection[Any], venue_id: int, start: date, end: date, listing_ids: list[int] | None = None
) -> int:
    """Rebuild daily bars for a session range (and optionally some listings). Returns rows changed."""
    p = _params(venue_id, start, end, listing_ids) | {
        "not_traded": QA_NOT_TRADED,
        "ohlc": QA_OHLC_INCONSISTENT,
        "special": QA_SPECIAL_SESSION,
        "revised": QA_VENDOR_REVISED,
        "ca_day": QA_CORPORATE_ACTION_DAY,
    }
    n = conn.execute(_BUILD_1D, p).rowcount
    # Sessions before the range are only read, never written.
    lookback = conn.execute(
        "SELECT coalesce(min(session_date), %(start)s) AS d FROM (SELECT cs.session_date FROM ref.calendar_session cs "
        "JOIN ref.venue v ON v.calendar_id = cs.calendar_id AND v.venue_id = %(venue)s "
        "WHERE cs.session_date < %(start)s ORDER BY cs.session_date DESC LIMIT 20) s",
        p,
    ).fetchone()
    assert lookback is not None
    conn.execute(
        _FLAGS_1D,
        p
        | {
            "lookback": lookback["d"],
            "stale": QA_STALE_PRICE,
            "outlier": QA_RETURN_OUTLIER,
            "stale_n": STALE_SESSIONS,
            "stale_back": STALE_SESSIONS - 1,
            "outlier_lr": OUTLIER_LOG_RETURN,
        },
    )
    return n


def resample(
    conn: psycopg.Connection[Any], venue_id: int, start: date, end: date, listing_ids: list[int] | None = None
) -> dict[str, int]:
    """Rebuild every week and month touched by the session range from the daily bars."""
    p = _params(venue_id, start, end, listing_ids)
    out = {}
    for tf, end_col in (("1w", "week_end"), ("1mo", "month_end")):
        periods = [r["bar_date"] for r in conn.execute(_PERIODS.format(end_col=end_col), p)]
        q = p | {"periods": periods}
        conn.execute(_RESAMPLE_DELETE.format(tf=tf), q)
        out[tf] = conn.execute(_RESAMPLE.format(tf=tf, end_col=end_col), q).rowcount
    return out


def rebuild_dirty(conn: psycopg.Connection[Any], venue_id: int, through: date) -> int:
    """Rebuild bars for every dirty range the clean layer has not consumed yet. Returns ranges consumed."""
    ranges = conn.execute(
        "SELECT dirty_id, listing_id, from_date, coalesce(to_date, %s) AS to_date FROM clean.dirty_range "
        "WHERE venue_id = %s AND clean_rebuilt_at IS NULL "
        "AND reason IN ('corporate_action','vendor_revision','mapping_fix','calendar_fix','manual') "
        "ORDER BY from_date",
        (through, venue_id),
    ).fetchall()
    for r in ranges:
        ids = None if r["listing_id"] is None else [r["listing_id"]]
        build_1d(conn, venue_id, r["from_date"], r["to_date"], ids)
        resample(conn, venue_id, r["from_date"], r["to_date"], ids)
        conn.execute(
            "UPDATE clean.dirty_range SET clean_rebuilt_at = now() WHERE dirty_id = %s", (r["dirty_id"],)
        )
    return len(ranges)
