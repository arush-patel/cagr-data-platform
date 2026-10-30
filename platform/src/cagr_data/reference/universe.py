"""The tracked universe (spec 0008): what the platform follows and reviews.

    uv run python -m cagr_data.reference.universe --dry-run
    uv run python -m cagr_data.reference.universe

ref.tracked_listing is rebuilt whole: every main-board equity listing with an EQ-series bar, and
every index whose current name is in tracked_indices.txt. Then the undecided rules items are
scoped. A price adjustment is set aside (out_of_scope, with its scope_reason) when the first of
these holds:

- outside_universe: its listing isn't a tracked equity, its event bar isn't in the EQ series,
  or the listing's market cap never reached the universe floor;
- real_move: no corporate action within 3 days of the ex-date and no ISIN change, unless the
  price dropped to 1/n of the previous close (a split or bonus NSE didn't announce);
- small_demerger: a demerger that moved the price less than 5%;
- unparsed_dividend: every unparsed action near it is a dividend (or other) and the price
  didn't drop 5% or more;
- illiquid: under ₹5 crore a day traded (median of the 20 sessions before), with no split,
  bonus or consolidation near it.

A possible merge is set aside as illiquid when both listings trade under ₹1 crore a day. Every
other undecided item is pending, so an item set aside comes back if the rules or data change.
Nothing is deleted.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings

FLOOR_INR = 15_000_000_000  # ₹1,500 crore, the universe floor (spec 0007, Q1)
SCOPED_BY = "scope"  # decided_by on items the scope rule set aside
INDEX_FILE = Path(__file__).with_name("tracked_indices.txt")
EVENT_WINDOW_DAYS = 60  # the event bar is the first bar on or after the ex-date, within this
ACTION_DAYS = 3  # a corporate action this close to the ex-date counts as its cause
SMALL_MOVE = 0.05  # a demerger or unparsed dividend that moved the price less is not priced
CLEAN_DROP = 0.45  # a drop at least this deep, to within 2% of 1/n, may be an unannounced split
CLEAN_DROP_TOLERANCE = 0.02
LIQUID_INR = 50_000_000  # ₹5 crore a day for a price adjustment (Arush, 30 Oct 2026)
MERGE_LIQUID_INR = 10_000_000  # ₹1 crore a day for either side of a possible merge
LIQUIDITY_SESSIONS = 20


class UnknownIndex(Exception):
    """A name in the index list matches no index listing, or more than one."""


@dataclass(frozen=True)
class ScopeResult:
    equities: int
    indices: int
    set_aside: int
    restored: int
    pending: int
    merges_set_aside: int = 0
    merges_restored: int = 0


def read_index_names(path: Path = INDEX_FILE) -> list[str]:
    """The index names in the list, one per line; blank lines and # comments are skipped."""
    names = (line.split("#", 1)[0].strip() for line in path.read_text().splitlines())
    return [n for n in names if n]


def build_tracked(conn: Connection, index_names: Sequence[str]) -> tuple[int, int]:
    """Replace ref.tracked_listing in the caller's transaction. Returns (equities, indices)."""
    # An index listing's symbol is the name in NSE's index-close file, and follows a rename.
    found = conn.execute(
        """
        SELECT n.name, array_remove(array_agg(c.listing_id), NULL)
          FROM unnest(%s::text[]) AS n(name)
          LEFT JOIN ref.listing_current c
            ON c.asset_class = 'index' AND c.status <> 'merged' AND c.symbol = n.name
         GROUP BY n.name
        """,
        (list(index_names),),
    ).fetchall()
    missing = sorted(name for name, ids in found if not ids)
    if missing:
        raise UnknownIndex(f"no index listing is called {', '.join(missing)}")
    twice = sorted(name for name, ids in found if len(ids) > 1)
    if twice:
        raise UnknownIndex(f"more than one index listing is called {', '.join(twice)}")
    conn.execute("DELETE FROM ref.tracked_listing")
    equities = conn.execute(
        """
        INSERT INTO ref.tracked_listing (listing_id, reason)
        SELECT l.listing_id, 'eq_main'
          FROM ref.listing l
         WHERE l.asset_class = 'equity' AND l.board = 'main' AND l.status <> 'merged'
           AND l.listing_id IN (SELECT DISTINCT listing_id FROM clean.bar_1d WHERE series = 'EQ')
        """
    ).rowcount
    indices = conn.execute(
        "INSERT INTO ref.tracked_listing (listing_id, reason)"
        " SELECT DISTINCT unnest(%s::int[]), 'index'",
        ([ids[0] for _, ids in found],),
    ).rowcount
    return equities, indices


def scope_adjustments(
    conn: Connection, floor_inr: int = FLOOR_INR, by: str = SCOPED_BY
) -> tuple[int, int]:
    """Set aside the undecided rules adjustments the scope rules catch, and bring back the ones
    they no longer catch, in the caller's transaction. Returns (set_aside, restored)."""
    # One statement, so the pipeline login needs no temporary tables. Every bar lookup is
    # bounded to one listing and a few weeks, so it stays on the partition indexes.
    rows = conn.execute(
        """
        WITH items AS (
            SELECT p.adjustment_id, p.listing_id, p.ex_date, p.status, p.scope_reason, p.note,
                   p.observed_factor AS observed
              FROM ref.price_adjustment p
             WHERE p.proposed_by = 'rules' AND p.status IN ('pending', 'out_of_scope')),
        big AS (
            SELECT DISTINCT m.listing_id FROM clean.market_cap_1d m
             WHERE m.listing_id IN (SELECT listing_id FROM items)
               AND m.market_cap_inr >= %(floor)s),
        facts AS (
            SELECT i.*, t.reason AS tracked, e.series, e.date AS event_date, pb.date AS prev_date,
                   i.listing_id IN (SELECT listing_id FROM big) AS reached_floor,
                   a.kinds, a.unparsed_kinds, liq.median_value
              FROM items i
              LEFT JOIN ref.tracked_listing t USING (listing_id)
              LEFT JOIN LATERAL (
                  SELECT b.series, b.date FROM clean.bar_1d b
                   WHERE b.listing_id = i.listing_id AND b.date >= i.ex_date
                     AND b.date < i.ex_date + %(window)s
                   ORDER BY b.date LIMIT 1) e ON true
              LEFT JOIN LATERAL (
                  SELECT b.date FROM clean.bar_1d b
                   WHERE b.listing_id = i.listing_id AND b.date < i.ex_date
                     AND b.date >= i.ex_date - %(window)s
                   ORDER BY b.date DESC LIMIT 1) pb ON true
              LEFT JOIN LATERAL (
                  SELECT array_agg(c.kind) AS kinds,
                         array_agg(c.kind) FILTER (WHERE c.parse_status = 'unparsed')
                             AS unparsed_kinds
                    FROM ref.corporate_action c
                   WHERE c.listing_id = i.listing_id
                     AND c.ex_date BETWEEN i.ex_date - %(near)s AND i.ex_date + %(near)s) a
                ON true
              LEFT JOIN LATERAL (
                  SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY v.value_traded)
                             AS median_value
                    FROM (SELECT b.value_traded FROM clean.bar_1d b
                           WHERE b.listing_id = i.listing_id AND b.date < i.ex_date
                             AND b.date >= i.ex_date - %(window)s
                           ORDER BY b.date DESC LIMIT %(sessions)s) v) liq ON true),
        judged AS (
            SELECT f.adjustment_id, f.status, f.scope_reason,
                   CASE
                       WHEN NOT coalesce(f.tracked = 'eq_main' AND f.series = 'EQ'
                                         AND f.reached_floor, false)
                           THEN 'outside_universe'
                       WHEN f.kinds IS NULL
                            AND NOT EXISTS (
                                SELECT 1 FROM ref.listing_isin s
                                 WHERE s.listing_id = f.listing_id
                                   AND s.valid_from > f.prev_date
                                   AND s.valid_from <= f.event_date
                                   AND s.valid_from > (SELECT min(valid_from)
                                                         FROM ref.listing_isin s0
                                                        WHERE s0.listing_id = f.listing_id))
                            AND NOT (1 - f.observed >= %(clean_drop)s
                                     AND abs(f.observed * round(1 / f.observed) - 1)
                                         <= %(clean_tol)s)
                           THEN 'real_move'
                       WHEN f.note LIKE 'demerger%%' AND abs(f.observed - 1) < %(small)s
                           THEN 'small_demerger'
                       WHEN f.note = 'an action on this date is unparsed'
                            AND f.unparsed_kinds <@ ARRAY['dividend', 'other']
                            AND f.observed >= 1 - %(small)s
                           THEN 'unparsed_dividend'
                       WHEN coalesce(f.median_value, 0) < %(liquid)s
                            AND NOT coalesce(f.kinds && ARRAY['split', 'bonus', 'consolidation'],
                                             false)
                           THEN 'illiquid'
                   END AS reason
              FROM facts f)
        UPDATE ref.price_adjustment p
           SET status = CASE WHEN j.reason IS NULL THEN 'pending' ELSE 'out_of_scope' END,
               scope_reason = j.reason,
               decided_by = CASE WHEN j.reason IS NULL THEN NULL ELSE %(by)s END,
               decided_at = CASE WHEN j.reason IS NULL THEN NULL ELSE now() END
          FROM judged j
         WHERE j.adjustment_id = p.adjustment_id
           AND j.reason IS DISTINCT FROM j.scope_reason
        RETURNING j.status AS was, p.status
        """,
        {"floor": floor_inr, "window": EVENT_WINDOW_DAYS, "near": ACTION_DAYS,
         "sessions": LIQUIDITY_SESSIONS, "clean_drop": CLEAN_DROP,
         "clean_tol": CLEAN_DROP_TOLERANCE, "small": SMALL_MOVE, "liquid": LIQUID_INR,
         "by": by},
    ).fetchall()  # fmt: skip
    set_aside = sum(1 for was, now in rows if was == "pending" and now == "out_of_scope")
    restored = sum(1 for was, now in rows if was == "out_of_scope" and now == "pending")
    return set_aside, restored


def scope_merges(
    conn: Connection, liquid_inr: int = MERGE_LIQUID_INR, by: str = SCOPED_BY
) -> tuple[int, int]:
    """Set aside the undecided rules merges whose two listings both trade under liquid_inr a
    day (the older one before it went quiet, the newer one from its first session), and bring
    back the rest. Returns (set_aside, restored)."""
    rows = conn.execute(
        """
        WITH items AS (
            SELECT c.change_id, c.status, c.listing_id AS newer,
                   (c.payload->>'into_id')::int AS older
              FROM ref.listing_change c
             WHERE c.kind = 'merge' AND c.proposed_by = 'rules'
               AND c.status IN ('pending', 'out_of_scope')),
        judged AS (
            SELECT i.change_id, i.status,
                   coalesce(o.v, 0) < %(liquid)s AND coalesce(n.v, 0) < %(liquid)s AS illiquid
              FROM items i
              JOIN ref.listing lo ON lo.listing_id = i.older
              JOIN ref.listing ln ON ln.listing_id = i.newer
              LEFT JOIN LATERAL (
                  SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY x.value_traded) AS v
                    FROM (SELECT b.value_traded FROM clean.bar_1d b
                           WHERE b.listing_id = i.older AND b.date <= lo.last_session
                             AND b.date > lo.last_session - %(window)s
                           ORDER BY b.date DESC LIMIT %(sessions)s) x) o ON true
              LEFT JOIN LATERAL (
                  SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY x.value_traded) AS v
                    FROM (SELECT b.value_traded FROM clean.bar_1d b
                           WHERE b.listing_id = i.newer AND b.date >= ln.first_session
                             AND b.date < ln.first_session + %(window)s
                           ORDER BY b.date LIMIT %(sessions)s) x) n ON true)
        UPDATE ref.listing_change c
           SET status = CASE WHEN j.illiquid THEN 'out_of_scope' ELSE 'pending' END,
               scope_reason = CASE WHEN j.illiquid THEN 'illiquid' END,
               decided_by = CASE WHEN j.illiquid THEN %(by)s END,
               decided_at = CASE WHEN j.illiquid THEN now() END
          FROM judged j
         WHERE j.change_id = c.change_id AND j.illiquid <> (j.status = 'out_of_scope')
        RETURNING c.status
        """,
        {"liquid": liquid_inr, "window": EVENT_WINDOW_DAYS, "sessions": LIQUIDITY_SESSIONS,
         "by": by},
    ).fetchall()  # fmt: skip
    set_aside = sum(1 for (status,) in rows if status == "out_of_scope")
    return set_aside, len(rows) - set_aside


def build_universe(conn: Connection, index_names: Sequence[str]) -> ScopeResult:
    """Rebuild the tracked listings and scope the review queue, in the caller's transaction."""
    equities, indices = build_tracked(conn, index_names)
    set_aside, restored = scope_adjustments(conn)
    merges_aside, merges_back = scope_merges(conn)
    row = conn.execute(
        "SELECT count(*) FROM ref.price_adjustment WHERE status = 'pending'"
    ).fetchone()
    return ScopeResult(equities, indices, set_aside, restored, int(row[0]) if row else 0,
                       merges_aside, merges_back)  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="report, then roll back")
    args = parser.parse_args(argv)
    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        try:
            result = build_universe(conn, read_index_names())
        except UnknownIndex as e:
            print(e, file=sys.stderr)
            return 1
        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()
    print(f"{'would track' if args.dry_run else 'tracking'} {result.equities} equities and"
          f" {result.indices} indices; {result.set_aside} adjustments set aside,"
          f" {result.restored} brought back, {result.pending} pending; merges:"
          f" {result.merges_set_aside} set aside,"
          f" {result.merges_restored} brought back")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
