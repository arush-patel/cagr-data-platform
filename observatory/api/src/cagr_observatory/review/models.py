"""What the review API returns. The UI's TypeScript types mirror these."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

Kind = Literal["adjustment", "change"]


class Item(BaseModel):
    """One row of the queue or the history."""

    kind: Kind
    id: int
    listing_id: int
    symbol: str | None
    """The listing's symbol on the item's date."""
    name: str | None
    date: date
    """The ex-date of an adjustment, the effective date of a change."""
    title: str
    """A few words, such as "Bonus 1:1" or "Possible merge"."""
    reason: str
    """One sentence: why the rules stopped and a person must decide."""
    status: Literal["pending", "applied", "rejected"]
    proposed_by: str
    decided_by: str | None
    decided_at: datetime | None
    note: str | None
    waiting_sessions: int
    """XNSE sessions since the item was raised."""
    overdue: bool
    """Waiting longer than the review_waiting check allows (3 sessions)."""


class Action(BaseModel):
    kind: str
    subject: str
    ratio_new: float | None
    ratio_old: float | None
    cash_per_share: float | None
    issue_price: float | None


class Bar(BaseModel):
    date: date
    close: float
    adjusted: float
    """The close multiplied by the proposed factor before the ex-date."""


class AdjustmentDetail(Item):
    factor: float | None
    """The factor the rules propose; may be empty when nothing explains the jump."""
    expected_factor: float | None
    observed_factor: float | None
    chart_factor: float | None
    """The factor the chart's adjusted line uses: factor, else expected, else observed."""
    close_before: float | None
    close_on: float | None
    isin_before: str | None
    isin_on: str | None
    action: Action | None
    bars: list[Bar]


class Listing(BaseModel):
    listing_id: int
    symbol: str | None
    isin: str | None
    name: str | None
    asset_class: str
    board: str | None
    status: str
    first_session: date
    last_session: date


class ChangeDetail(Item):
    change_kind: str
    payload: dict[str, Any]
    evidence: dict[str, Any]
    listings: list[Listing]
    """The listing the change is on, then any candidates it could belong to."""
