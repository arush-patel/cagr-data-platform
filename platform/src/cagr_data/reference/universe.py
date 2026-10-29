"""The tracked universe (spec 0008): what the platform follows and reviews.

    uv run python -m cagr_data.reference.universe --dry-run
    uv run python -m cagr_data.reference.universe

ref.tracked_listing is rebuilt whole: every main-board equity listing with an EQ-series bar, and
every index whose current name is in tracked_indices.txt. Then the pending price adjustments are
scoped: an item stays pending only if its listing is a tracked equity, its event bar is in the
EQ series and the listing's market cap reached the universe floor on some day. Every other
pending item becomes out_of_scope, and an out_of_scope item that now passes goes back to
pending. Nothing is deleted.
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


class UnknownIndex(Exception):
    """A name in the index list matches no index listing, or more than one."""


@dataclass(frozen=True)
class ScopeResult:
    equities: int
    indices: int
    set_aside: int
    restored: int
    pending: int


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
    """Set aside the pending adjustments outside the tracked universe and bring back the
    out_of_scope ones inside it, in the caller's transaction. Returns (set_aside, restored)."""
    conn.execute(
        """
        CREATE TEMP TABLE adjustment_scope ON COMMIT DROP AS
        WITH items AS (
            SELECT p.adjustment_id, p.listing_id, p.ex_date, p.status
              FROM ref.price_adjustment p
             WHERE p.status IN ('pending', 'out_of_scope')),
        big AS (
            SELECT DISTINCT m.listing_id FROM clean.market_cap_1d m
             WHERE m.listing_id IN (SELECT listing_id FROM items)
               AND m.market_cap_inr >= %(floor)s)
        SELECT i.adjustment_id, i.status,
               coalesce(t.reason = 'eq_main' AND e.series = 'EQ'
                        AND i.listing_id IN (SELECT listing_id FROM big), false) AS in_scope
          FROM items i
          LEFT JOIN ref.tracked_listing t USING (listing_id)
          LEFT JOIN LATERAL (
              SELECT b.series FROM clean.bar_1d b
               WHERE b.listing_id = i.listing_id AND b.date >= i.ex_date
                 AND b.date < i.ex_date + %(window)s
               ORDER BY b.date LIMIT 1) e ON true
        """,
        {"floor": floor_inr, "window": EVENT_WINDOW_DAYS},
    )
    set_aside = conn.execute(
        """
        UPDATE ref.price_adjustment p
           SET status = 'out_of_scope', decided_by = %s, decided_at = now()
          FROM adjustment_scope s
         WHERE s.adjustment_id = p.adjustment_id AND s.status = 'pending' AND NOT s.in_scope
        """,
        (by,),
    ).rowcount
    restored = conn.execute(
        """
        UPDATE ref.price_adjustment p
           SET status = 'pending', decided_by = NULL, decided_at = NULL
          FROM adjustment_scope s
         WHERE s.adjustment_id = p.adjustment_id AND s.status = 'out_of_scope' AND s.in_scope
        """
    ).rowcount
    conn.execute("DROP TABLE adjustment_scope")
    return set_aside, restored


def build_universe(conn: Connection, index_names: Sequence[str]) -> ScopeResult:
    """Rebuild the tracked listings and scope the review queue, in the caller's transaction."""
    equities, indices = build_tracked(conn, index_names)
    set_aside, restored = scope_adjustments(conn)
    row = conn.execute(
        "SELECT count(*) FROM ref.price_adjustment WHERE status = 'pending'"
    ).fetchone()
    return ScopeResult(equities, indices, set_aside, restored, int(row[0]) if row else 0)


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
          f" {result.restored} brought back, {result.pending} pending")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
