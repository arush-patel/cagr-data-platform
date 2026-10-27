"""Turn the raw corporate-actions responses into ref.corporate_action (spec 0002, sections 6.2
and 7.2).

    uv run python -m cagr_data.reference.corporate_actions

Each current response row is matched to a listing (by its ISIN, else by its symbol on the
ex-date) and its subject is parsed into one row per action. Rows are upserted on their natural
key (listing, ex-date, subject, position in the subject), so a rerun changes nothing and an
amended announcement updates its row. A row that has disappeared from NSE's responses is deleted
unless an adjustment already points at it. Runs after the listing build.
"""

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings
from cagr_data.quality.checks import SAMPLE, Outcome
from cagr_data.vendors.nse.corporate_actions import parse_subject

# Kinds whose ratio or amount changes a price factor; an unparsed one needs a person.
PRICE_KINDS = frozenset({"split", "bonus", "consolidation", "rights", "dividend"})


@dataclass
class ActionsResult:
    rows: int = 0
    actions: int = 0
    unparsed: int = 0
    deleted: int = 0
    unmapped: list[tuple[str | None, str | None, date]] = field(default_factory=list)


def _raw_rows(conn: Connection) -> list[tuple[Any, ...]]:
    """Current response rows with the listing each belongs to (a merged listing's survivor)."""
    return conn.execute(
        """
        WITH matched AS (
            SELECT a.file_id, a.row_no, a.symbol, a.isin, a.subject, a.ex_date, a.face_value,
                   coalesce(
                       (SELECT i.listing_id FROM ref.listing_isin i WHERE i.isin = a.isin
                         ORDER BY (i.valid_from <= a.ex_date) DESC, i.valid_from DESC LIMIT 1),
                       (SELECT s.listing_id FROM ref.listing_symbol s
                         JOIN ref.listing l USING (listing_id)
                         WHERE s.symbol = a.symbol AND l.asset_class <> 'index'
                           AND s.valid_from <= a.ex_date
                         ORDER BY s.valid_from DESC LIMIT 1)
                   ) AS listing_id
              FROM raw.nse_corporate_action_current a
             WHERE a.ex_date IS NOT NULL AND a.subject IS NOT NULL
        )
        SELECT m.file_id, m.row_no, m.symbol, m.isin, m.subject, m.ex_date, m.face_value,
               coalesce(l.merged_into, m.listing_id)
          FROM matched m LEFT JOIN ref.listing l USING (listing_id)
         ORDER BY m.ex_date, m.file_id, m.row_no
        """
    ).fetchall()


def build_actions(conn: Connection) -> ActionsResult:
    """Upsert every parsed action from the current responses, in the caller's transaction."""
    result = ActionsResult()
    wanted: dict[tuple[int, date, str, int], tuple[Any, ...]] = {}
    for file_id, _row_no, symbol, isin, subject, ex_date, face_value, listing_id in _raw_rows(conn):
        result.rows += 1
        if listing_id is None:
            result.unmapped.append((symbol, isin, ex_date))
            continue
        fv = Decimal(face_value) if face_value is not None else None
        for seq, action in enumerate(parse_subject(subject, fv), start=1):
            wanted[(listing_id, ex_date, subject, seq)] = (
                action.kind, action.ratio_new, action.ratio_old, action.cash_per_share,
                action.issue_price, "parsed" if action.parsed else "unparsed", file_id, fv,
            )  # fmt: skip

    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ref.corporate_action (listing_id, ex_date, subject, seq, kind, ratio_new,
                   ratio_old, cash_per_share, issue_price, parse_status, source_file_id,
                   face_value)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (listing_id, ex_date, subject, seq) DO UPDATE
               SET kind = excluded.kind, ratio_new = excluded.ratio_new,
                   ratio_old = excluded.ratio_old, cash_per_share = excluded.cash_per_share,
                   issue_price = excluded.issue_price, parse_status = excluded.parse_status,
                   source_file_id = excluded.source_file_id, face_value = excluded.face_value
             WHERE (ref.corporate_action.kind, ref.corporate_action.ratio_new,
                    ref.corporate_action.ratio_old, ref.corporate_action.cash_per_share,
                    ref.corporate_action.issue_price, ref.corporate_action.parse_status,
                    ref.corporate_action.source_file_id, ref.corporate_action.face_value)
                   IS DISTINCT FROM
                   (excluded.kind, excluded.ratio_new, excluded.ratio_old,
                    excluded.cash_per_share, excluded.issue_price, excluded.parse_status,
                    excluded.source_file_id, excluded.face_value)
            """,
            [(*key, *values) for key, values in wanted.items()],
        )
    result.actions = len(wanted)
    result.unparsed = sum(1 for v in wanted.values() if v[5] == "unparsed")

    existing = conn.execute(
        "SELECT action_id, listing_id, ex_date, subject, seq FROM ref.corporate_action"
    ).fetchall()
    stale = [r[0] for r in existing if (r[1], r[2], r[3], r[4]) not in wanted]
    if stale:
        deleted = conn.execute(
            "DELETE FROM ref.corporate_action c WHERE c.action_id = ANY(%s)"
            " AND NOT EXISTS (SELECT 1 FROM ref.price_adjustment p"
            "                  WHERE p.action_id = c.action_id)",
            (stale,),
        )
        result.deleted = deleted.rowcount
    return result


def corporate_action_parsed(conn: Connection, after: date, sessions: int = 3) -> Outcome:
    """Every price-changing action with an ex-date in the next few sessions after `after` is
    parsed (check corporate_action_parsed)."""
    rows = conn.execute(
        """
        WITH horizon AS (
            SELECT max(date) AS d FROM (
                SELECT date FROM ref.calendar_session
                 WHERE venue_id = 'XNSE' AND is_session AND date > %s
                 ORDER BY date LIMIT %s) s)
        SELECT c.ex_date, c.listing_id, c.subject FROM ref.corporate_action c, horizon
         WHERE c.parse_status = 'unparsed' AND c.kind = ANY(%s)
           AND c.ex_date > %s AND c.ex_date <= horizon.d
         ORDER BY 1, 2
        """,
        (after, sessions, sorted(PRICE_KINDS), after),
    ).fetchall()
    return _outcome("corporate_action_parsed", rows, "unparsed actions about to take effect")


def _outcome(check_id: str, rows: Sequence[tuple[Any, ...]], what: str) -> Outcome:
    if not rows:
        return Outcome(check_id, True)
    sample = ["/".join(str(v) for v in r) for r in rows[:SAMPLE]]
    return Outcome(check_id, False, {"count": len(rows), "sample": sample}, f"{len(rows)} {what}")


def main(argv: list[str] | None = None) -> int:
    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        result = build_actions(conn)
        conn.commit()
    print(
        f"{result.rows} response rows: {result.actions} actions ({result.unparsed} unparsed),"
        f" {result.deleted} withdrawn, {len(result.unmapped)} rows with no listing"
    )
    for symbol, isin, ex_date in result.unmapped[:SAMPLE]:
        print(f"  no listing: {symbol} {isin} {ex_date}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
