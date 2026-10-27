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
    Bar,
    ChangeDetail,
    Item,
    Listing,
)


def _item(kind: str, row: dict[str, Any], title: str, reason: str) -> dict[str, Any]:
    return {
        **row,
        "kind": kind,
        "title": title,
        "reason": reason,
        "overdue": row["status"] == "pending"
        and row["waiting_sessions"] > queries.MAX_WAITING_SESSIONS,
    }


def _adjustment(row: dict[str, Any]) -> dict[str, Any]:
    return _item("adjustment", row, reasons.adjustment_title(row), reasons.adjustment_reason(row))


def _change(conn: Connection, row: dict[str, Any]) -> tuple[dict[str, Any], list[int]]:
    candidates = [int(c) for c in row["evidence"].get("candidates") or []]
    into = row["payload"].get("into_id")
    if row["change_kind"] == "merge" and into is not None and int(into) not in candidates:
        candidates = [int(into), *candidates]
    symbols = [str(r["symbol"]) for r in queries.listings(conn, candidates) if r["symbol"]]
    item = _item("change", row, reasons.change_title(row), reasons.change_reason(row, symbols))
    return item, candidates


def item_row(conn: Connection, kind: str, row: dict[str, Any]) -> Item:
    """A queue row for an adjustment or change, as the queue lists it."""
    found = _adjustment(row) if kind == "adjustment" else _change(conn, row)[0]
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
        found += [_change(c, r)[0] for r in queries.changes(c, status)]
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
        factor = row["factor"] or row["expected_factor"] or row["observed_factor"]
        bars = [
            Bar(
                date=b["date"],
                close=b["close"],
                adjusted=float(b["close"]) * (factor if factor and b["date"] < row["date"] else 1),
            )
            for b in queries.closes_around(c, row["listing_id"], row["date"])
        ]
        action = None
        if row["action_kind"] is not None:
            action = Action(kind=row["action_kind"], **{k: row[k] for k in _ACTION_FIELDS})
        return AdjustmentDetail.model_validate(
            {**_adjustment(row), "chart_factor": factor, "action": action, "bars": bars}
        )

    @api.get("/review/items/change/{change_id}")
    def change(c: Conn, change_id: int) -> ChangeDetail:
        row = queries.change(c, change_id)
        if row is None:
            raise HTTPException(404, f"No change {change_id}")
        item, candidates = _change(c, row)
        ids = [row["listing_id"], *[i for i in candidates if i != row["listing_id"]]]
        return ChangeDetail.model_validate({**item, "listings": queries.listings(c, ids)})

    @api.get("/listings/search")
    def search(c: Conn, q: Annotated[str, Query(min_length=1)]) -> list[Listing]:
        return [Listing.model_validate(r) for r in queries.search_listings(c, q)]

    return api


_ACTION_FIELDS = ("subject", "ratio_new", "ratio_old", "cash_per_share", "issue_price")
