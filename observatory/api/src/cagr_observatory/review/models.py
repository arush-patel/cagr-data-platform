"""What the review API returns. The UI's TypeScript types mirror these."""

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel

from cagr_observatory.review.reasons import Category

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
    category: Category
    """Which of the kinds of review item it is; the queue filters on it."""
    category_label: str
    """The category in a few words, such as "Possible merge"."""
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


class Close(BaseModel):
    date: date
    close: float


class Series(BaseModel):
    """One listing's unadjusted closes around a date, for a chart."""

    listing_id: int
    symbol: str | None
    closes: list[Close]


class Explained(Item):
    guide: str
    """How to decide an item of this kind."""
    approve_effect: str
    """What approving writes, in words."""
    reject_effect: str


class AdjustmentDetail(Explained):
    factor: float | None
    """The factor the rules propose; may be empty when nothing explains the jump."""
    expected_factor: float | None
    observed_factor: float | None
    chart_factor: float | None
    """The factor approving applies, and the chart's adjusted line uses: factor, else expected,
    else observed."""
    close_before: float | None
    close_on: float | None
    previous_trade: date | None
    """The listing's last traded session before the ex-date."""
    silent_sessions: int | None
    """XNSE sessions between the previous trade and the ex-date with no trade."""
    isin_before: str | None
    isin_on: str | None
    action: Action | None
    bars: list[Close]
    """The listing's own last 40 closes before the ex-date and first 40 from it, unadjusted."""


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


class ChangeDetail(Explained):
    change_kind: str
    payload: dict[str, Any]
    evidence: dict[str, Any]
    listings: list[Listing]
    """The listing the change is on, then any candidates it could belong to."""
    series: list[Series]
    """Each of those listings' closes, 40 traded sessions either side of the date."""
