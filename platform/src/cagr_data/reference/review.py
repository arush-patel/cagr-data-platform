"""Decide what the listing and adjustment rules left pending (spec 0002, section 7.4).

The Observatory's review page and the command below call the same functions, so the rules about
who may decide are checked in one place. Each function runs in the caller's transaction and
commits nothing; a refused decision raises a ReviewError whose message can be shown as it is.

    uv run python -m cagr_data.reference.review list
    uv run python -m cagr_data.reference.review approve listing_change 812 --as arush
    uv run python -m cagr_data.reference.review reject price_adjustment 813 --as arush \
        --note "not a split"
    uv run python -m cagr_data.reference.review approve listing_change 4021 --into 377 --as arush
    uv run python -m cagr_data.reference.review propose-merge 4021 --into 377 --as arush

What a decision writes:
- ref.listing_change: status, decided_by, decided_at and note; a new row for a proposed merge.
- ref.price_adjustment: status, decided_by, decided_at, note, applied_at and, when approved
  without a factor, factor (the expected factor, else the observed one).
- An approved merge (a `merge` item, or an `index_rename` item with the listing it renames):
  ref.listing_symbol and ref.listing_isin rows move to the surviving listing (listing_id, and
  valid_to where the survivor's open period meets the merged one), and ref.listing gets status
  `merged` and merged_into on the merged listing, and the wider first and last session on the
  survivor.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from psycopg.types.json import Jsonb

from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings

Item = Literal["listing_change", "price_adjustment"]
_KEY = {"listing_change": "change_id", "price_adjustment": "adjustment_id"}


class ReviewError(Exception):
    """A decision that can't be made; the message says why in plain words."""


class NotFound(ReviewError):
    pass


class NotPending(ReviewError):
    pass


class SelfDecision(ReviewError):
    pass


class MissingTarget(ReviewError):
    pass


class MergeConflict(ReviewError):
    pass


@dataclass(frozen=True)
class Pending:
    item: Item
    item_id: int
    kind: str
    listing_id: int
    effective_date: date
    proposed_by: str
    payload: dict[str, Any]


def _load(conn: Connection, item: Item, item_id: int) -> dict[str, Any]:
    if item not in _KEY:
        raise ReviewError(f"unknown item type {item!r}")
    cur = conn.execute(f"SELECT * FROM ref.{item} WHERE {_KEY[item]} = %s FOR UPDATE", (item_id,))
    row = cur.fetchone()
    if row is None:
        raise NotFound(f"{item} {item_id} does not exist")
    names = [c.name for c in cur.description or ()]
    record = dict(zip(names, row, strict=True))
    if record["status"] != "pending":
        raise NotPending(f"{item} {item_id} is already {record['status']}")
    return record


def _decider(record: dict[str, Any], by: str) -> None:
    if not by.strip():
        raise ReviewError("a decision needs the reviewer's name")
    if record["proposed_by"] == by:
        raise SelfDecision(f"{by} proposed this item, so someone else has to decide it")


def _decide(conn: Connection, item: Item, item_id: int, status: str, by: str, note: str | None,
            extra: str = "", params: tuple[Any, ...] = ()) -> None:  # fmt: skip
    conn.execute(
        f"UPDATE ref.{item} SET status = %s, decided_by = %s, decided_at = now(), note = %s"
        f"{extra} WHERE {_KEY[item]} = %s",
        (status, by, note, *params, item_id),
    )


def _merge(conn: Connection, merged_id: int, into_id: int) -> None:
    """Move the merged listing's symbol and ISIN periods to the survivor and mark it merged."""
    if merged_id == into_id:
        raise MergeConflict("a listing can't be merged into itself")
    rows = conn.execute(
        "SELECT listing_id, status FROM ref.listing WHERE listing_id IN (%s, %s) FOR UPDATE",
        (merged_id, into_id),
    ).fetchall()
    status: dict[int, str] = {r[0]: r[1] for r in rows}
    if len(status) != 2:
        raise NotFound(f"listing {merged_id} or {into_id} does not exist")
    if "merged" in (status[merged_id], status[into_id]):
        raise MergeConflict("one of the two listings is already merged")
    for attr in ("symbol", "isin"):
        first = conn.execute(
            f"SELECT min(valid_from) FROM ref.listing_{attr} WHERE listing_id = %s", (merged_id,)
        ).fetchone()
        start = first[0] if first else None
        if start is not None:
            # The survivor's open period ends where the merged listing's first one begins.
            conn.execute(
                f"UPDATE ref.listing_{attr} SET valid_to = %s"
                " WHERE listing_id = %s AND valid_to IS NULL AND valid_from < %s",
                (start, into_id, start),
            )
        overlap = conn.execute(
            f"""
            SELECT 1 FROM ref.listing_{attr} a JOIN ref.listing_{attr} b
              ON daterange(a.valid_from, a.valid_to) && daterange(b.valid_from, b.valid_to)
             WHERE a.listing_id = %s AND b.listing_id = %s LIMIT 1
            """,
            (merged_id, into_id),
        ).fetchone()
        if overlap:
            raise MergeConflict(
                f"both listings had a {attr} on the same days; they can't be one listing"
            )
        conn.execute(
            f"UPDATE ref.listing_{attr} SET listing_id = %s WHERE listing_id = %s",
            (into_id, merged_id),
        )
    conn.execute(
        """
        UPDATE ref.listing s
           SET first_session = least(s.first_session, m.first_session),
               last_session = greatest(s.last_session, m.last_session),
               status = CASE WHEN m.status = 'active' THEN 'active' ELSE s.status END
          FROM ref.listing m
         WHERE s.listing_id = %s AND m.listing_id = %s
        """,
        (into_id, merged_id),
    )
    conn.execute(
        "UPDATE ref.listing SET status = 'merged', merged_into = %s WHERE listing_id = %s",
        (into_id, merged_id),
    )


def approve(
    conn: Connection,
    item: Item,
    item_id: int,
    by: str,
    note: str | None = None,
    *,
    into_id: int | None = None,
) -> None:
    """Approve a pending item.

    A `merge` item merges its listing into the one in its payload. An `index_rename` item needs
    into_id, the old index listing it renames (one of its candidates). An adjustment is applied
    with its factor, or its expected factor, or its observed one.
    """
    record = _load(conn, item, item_id)
    _decider(record, by)
    if item == "price_adjustment":
        factor = record["factor"] or record["expected_factor"] or record["observed_factor"]
        if factor is None:
            raise MissingTarget(f"adjustment {item_id} has no factor to apply")
        _decide(conn, item, item_id, "applied", by, note, ", factor = %s, applied_at = now()",
                (factor,))  # fmt: skip
        return
    kind = record["kind"]
    if kind == "merge":
        target = into_id or record["payload"].get("into_id")
        if target is None:
            raise MissingTarget("say which listing to merge into")
        _merge(conn, record["listing_id"], int(target))
    elif kind == "index_rename":
        candidates = record["evidence"].get("candidates", [])
        if into_id is None:
            raise MissingTarget(f"say which index it renames: one of {candidates}")
        if candidates and into_id not in candidates:
            raise MissingTarget(f"{into_id} is not one of the candidates {candidates}")
        _merge(conn, record["listing_id"], into_id)
    _decide(conn, item, item_id, "applied", by, note)


def reject(conn: Connection, item: Item, item_id: int, by: str, note: str | None = None) -> None:
    """Reject a pending item. Nothing else changes: a rejected merge leaves two listings, and a
    rejected adjustment leaves prices as they are."""
    record = _load(conn, item, item_id)
    _decider(record, by)
    _decide(conn, item, item_id, "rejected", by, note)


def propose_merge(
    conn: Connection, merged_id: int, into_id: int, by: str, note: str | None = None
) -> int:
    """Propose by hand that merged_id is the same security as into_id. Someone other than the
    proposer approves it. Returns the new item's change_id."""
    row = conn.execute(
        "SELECT first_session FROM ref.listing WHERE listing_id = %s", (merged_id,)
    ).fetchone()
    if row is None:
        raise NotFound(f"listing {merged_id} does not exist")
    created = conn.execute(
        "INSERT INTO ref.listing_change (kind, listing_id, effective_date, payload, evidence,"
        " status, auto, proposed_by, note) VALUES ('merge', %s, %s, %s, %s, 'pending', false,"
        " %s, %s) RETURNING change_id",
        (merged_id, row[0], Jsonb({"merged_id": merged_id, "into_id": into_id}),
         Jsonb({"rule": "manual", "candidates": [into_id]}), by, note),
    ).fetchone()  # fmt: skip
    assert created is not None
    return int(created[0])


def pending(conn: Connection) -> list[Pending]:
    """Every pending item, oldest first."""
    rows = conn.execute(
        """
        SELECT 'listing_change', change_id, kind, listing_id, effective_date, proposed_by,
               payload, created_at
          FROM ref.listing_change WHERE status = 'pending'
        UNION ALL
        SELECT 'price_adjustment', adjustment_id, 'adjustment', listing_id, ex_date,
               proposed_by,
               jsonb_build_object('observed_factor', observed_factor,
                                  'expected_factor', expected_factor, 'action_id', action_id),
               created_at
          FROM ref.price_adjustment WHERE status = 'pending'
         ORDER BY 8, 2
        """
    ).fetchall()
    return [Pending(*r[:7]) for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    for name in ("approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("item", choices=sorted(_KEY))
        p.add_argument("item_id", type=int)
        p.add_argument("--as", dest="by", required=True)
        p.add_argument("--note")
        if name == "approve":
            p.add_argument("--into", type=int)
    p = sub.add_parser("propose-merge")
    p.add_argument("merged_id", type=int)
    p.add_argument("--into", type=int, required=True)
    p.add_argument("--as", dest="by", required=True)
    p.add_argument("--note")
    args = parser.parse_args(argv)

    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        try:
            if args.command == "list":
                for p_ in pending(conn):
                    print(f"{p_.item} {p_.item_id}: {p_.kind} listing {p_.listing_id}"
                          f" on {p_.effective_date} by {p_.proposed_by} {p_.payload}")  # fmt: skip
                return 0
            if args.command == "approve":
                approve(conn, args.item, args.item_id, args.by, args.note, into_id=args.into)
            elif args.command == "reject":
                reject(conn, args.item, args.item_id, args.by, args.note)
            else:
                change_id = propose_merge(conn, args.merged_id, args.into, args.by, args.note)
                print(f"proposed as listing_change {change_id}")
        except ReviewError as e:
            print(e, file=sys.stderr)
            return 1
        conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
