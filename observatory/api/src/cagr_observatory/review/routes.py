"""/api/review and /api/listings."""

from collections.abc import Callable, Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from cagr.db import Connection
from cagr_observatory.connections import open_or_503
from cagr_observatory.review import queries, reasons
from cagr_observatory.review.models import (
    Action,
    AdjustmentDetail,
    ChangeDetail,
    Close,
    Item,
    Listing,
    Series,
)


def _item(kind: str, row: dict[str, Any], category: reasons.Category, title: str,
          reason: str) -> dict[str, Any]:  # fmt: skip
    return {
        **row,
        "kind": kind,
        "category": category,
        "category_label": reasons.CATEGORY_LABELS[category],
        "title": title,
        "reason": reason,
        "overdue": row["status"] == "pending"
        and row["waiting_sessions"] > queries.MAX_WAITING_SESSIONS,
    }


def _adjustment(row: dict[str, Any]) -> dict[str, Any]:
    return _item("adjustment", row, reasons.adjustment_category(row),
                 reasons.adjustment_title(row), reasons.adjustment_reason(row))  # fmt: skip


def _candidates(row: dict[str, Any]) -> list[int]:
    """The listings a change could belong to: the evidence's candidates and a merge's target."""
    candidates = [int(c) for c in row["evidence"].get("candidates") or []]
    into = row["payload"].get("into_id")
    if row["change_kind"] == "merge" and into is not None and int(into) not in candidates:
        candidates = [int(into), *candidates]
    return candidates


def _change(row: dict[str, Any], symbol_of: dict[int, str | None]) -> dict[str, Any]:
    symbols = [s for s in (symbol_of.get(c) for c in _candidates(row)) if s]
    return _item("change", row, reasons.change_category(row), reasons.change_title(row),
                 reasons.change_reason(row, symbols))  # fmt: skip


def _changes(conn: Connection, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Queue rows for changes, with every candidate's symbol read in one query."""
    ids = sorted({c for r in rows for c in _candidates(r)})
    symbol_of = {r["listing_id"]: r["symbol"] for r in queries.listings(conn, ids)}
    return [_change(r, symbol_of) for r in rows]


def item_row(conn: Connection, kind: str, row: dict[str, Any]) -> Item:
    """A queue row for an adjustment or change, as the queue lists it."""
    found = _adjustment(row) if kind == "adjustment" else _changes(conn, [row])[0]
    return Item.model_validate(found)


def router(connect: Callable[[], Connection]) -> APIRouter:
    def conn() -> Iterator[Connection]:
        with open_or_503(connect) as c:
            yield c

    Conn = Annotated[Connection, Depends(conn)]
    api = APIRouter()

    @api.get("/review/items")
    def items(c: Conn, status: queries.Status = "pending") -> list[Item]:
        found = [_adjustment(r) for r in queries.adjustments(c, status)]
        found += _changes(c, queries.changes(c, status))
        if status == "pending":
            found.sort(key=lambda i: (-i["waiting_sessions"], i["kind"], i["id"]))
        else:
            found.sort(key=lambda i: i["decided_at"], reverse=True)
        return [Item.model_validate(i) for i in found]

    @api.get("/review/items/adjustment/{adjustment_id}")
    def adjustment(c: Conn, adjustment_id: int) -> AdjustmentDetail:
        row = queries.adjustment(c, adjustment_id)
        if row is None:
            raise HTTPException(404, f"No adjustment {adjustment_id}")
        item = _adjustment(row)
        factor = row["factor"] or row["expected_factor"] or row["observed_factor"]
        approve, reject = reasons.adjustment_effects(row, factor)
        previous, silent = queries.previous_trade(c, row["listing_id"], row["date"])
        action = None
        if row["action_kind"] is not None:
            action = Action(kind=row["action_kind"], **{k: row[k] for k in _ACTION_FIELDS})
        return AdjustmentDetail.model_validate({
            **item,
            "guide": reasons.ADJUSTMENT_GUIDES[item["category"]],
            "approve_effect": approve,
            "reject_effect": reject,
            "chart_factor": factor,
            "previous_trade": previous,
            "silent_sessions": silent,
            "action": action,
            "bars": [Close.model_validate(b) for b in
                     queries.closes_around(c, row["listing_id"], row["date"])],
        })  # fmt: skip

    @api.get("/review/items/change/{change_id}")
    def change(c: Conn, change_id: int) -> ChangeDetail:
        row = queries.change(c, change_id)
        if row is None:
            raise HTTPException(404, f"No change {change_id}")
        ids = [row["listing_id"], *[i for i in _candidates(row) if i != row["listing_id"]]]
        listings = queries.listings(c, ids)
        symbol_of = {r["listing_id"]: r["symbol"] for r in listings}
        item = _change(row, symbol_of)
        others = [str(r["symbol"]) for r in listings[1:] if r["symbol"]]
        approve, reject = reasons.change_effects(row, others)
        series = [
            Series(listing_id=r["listing_id"], symbol=r["symbol"],
                   closes=[Close.model_validate(b) for b in
                           queries.closes_around(c, r["listing_id"], row["date"])])
            for r in listings
        ]  # fmt: skip
        return ChangeDetail.model_validate({
            **item,
            "guide": reasons.CHANGE_GUIDES[item["category"]],
            "approve_effect": approve,
            "reject_effect": reject,
            "listings": listings,
            "series": series,
        })  # fmt: skip

    @api.get("/listings/search")
    def search(c: Conn, q: Annotated[str, Query(min_length=1)]) -> list[Listing]:
        return [Listing.model_validate(r) for r in queries.search_listings(c, q)]

    return api


_ACTION_FIELDS = ("subject", "ratio_new", "ratio_old", "cash_per_share", "issue_price")
