"""Approve and reject (spec 0005, section 4.3), through the platform's review module.

The page and the command line share cagr_data.reference.review, so the rules about who may
decide what are checked in one place. Each decision is one transaction on the reviewer's login.
"""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field

from cagr.db import Connection
from cagr_data.reference import review
from cagr_observatory.review import queries
from cagr_observatory.review.models import Item, Kind
from cagr_observatory.review.routes import item_row

TABLE: dict[Kind, review.Item] = {"adjustment": "price_adjustment", "change": "listing_change"}


class Approve(BaseModel):
    note: str | None = None
    into_id: int | None = None
    """For a possible index rename: the old index listing it renames (one of the candidates)."""


class Reject(BaseModel):
    note: Annotated[str, Field(min_length=1)]
    """Why it was rejected. Required, so the history explains itself."""


def router(
    connect: Callable[[], Connection], connect_review: Callable[[], Connection], reviewer: str
) -> APIRouter:
    api = APIRouter()

    def decided(kind: Kind, item_id: int, decide: Callable[[Connection], None]) -> Item:
        try:
            with connect_review() as conn:
                decide(conn)
                conn.commit()
        except review.NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except review.ReviewError as exc:
            raise HTTPException(409, str(exc)) from exc
        with connect() as conn:
            row = (queries.adjustment if kind == "adjustment" else queries.change)(conn, item_id)
            assert row is not None
            return item_row(conn, kind, row)

    @api.post("/review/items/{kind}/{item_id}/approve")
    def approve(kind: Kind, item_id: int, body: Annotated[Approve, Body()]) -> Item:
        note = body.note or None
        return decided(
            kind,
            item_id,
            lambda c: review.approve(c, TABLE[kind], item_id, reviewer, note, into_id=body.into_id),
        )

    @api.post("/review/items/{kind}/{item_id}/reject")
    def reject(kind: Kind, item_id: int, body: Annotated[Reject, Body()]) -> Item:
        return decided(
            kind, item_id, lambda c: review.reject(c, TABLE[kind], item_id, reviewer, body.note)
        )

    return api
