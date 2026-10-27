"""The listing builder: one listing per security, with dated symbols and ISINs (spec 0002,
section 5).

Sessions are applied one at a time, in date order. The state after session d is the state after
the session before d plus d's bhavcopy rows, so history and the nightly run share one code path.
For each row (symbol s, ISIN i) the rules are, in order:

1. Known ISIN: the row belongs to the ISIN's listing. A different symbol is a symbol change.
2. Known symbol, new ISIN: an ISIN change, when the listing traded within the last 20 sessions
   and its old ISIN is not in today's file. A listing silent for longer gets a new listing and a
   review item asking whether to merge them.
3. No ISIN (before 22 Jun 2011): a symbolchange.csv entry to s within 5 sessions, from a symbol
   whose listing traded within 20 sessions, is a symbol change.
4. Anything else is a new listing.

The Book holds the state in memory and writes every change through a Store as it is made, so a
listing gets its database id the moment it exists.
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol

AssetClass = Literal["equity", "etf", "reit", "invit", "index"]
Board = Literal["main", "sme"]
ChangeKind = Literal["new_listing", "symbol_change", "isin_change", "merge", "index_rename"]
Status = Literal["applied", "pending", "rejected"]

RECENT_SESSIONS = 20  # a listing silent for longer is not continued by a new ISIN
SYMBOL_CHANGE_SESSIONS = 5  # how far a symbolchange.csv date may be from the session
FIRST_ISIN_DATE = date(2011, 6, 22)

MAIN_SERIES = frozenset({"EQ", "BE", "BZ"})
SME_SERIES = frozenset({"SM", "ST", "SZ"})
LISTING_SERIES = MAIN_SERIES | SME_SERIES | {"RR", "IV"}


def classify(series: str, isin: str | None) -> tuple[AssetClass, Board] | None:
    """The asset class and board a bhavcopy row's series makes, or None when the series makes
    no listing (T0, debt, rights entitlements)."""
    if series in SME_SERIES:
        return "equity", "sme"
    if series == "RR":
        return "reit", "main"
    if series == "IV":
        return "invit", "main"
    if series in MAIN_SERIES:
        return ("etf" if isin is not None and isin.startswith("INF") else "equity"), "main"
    return None


@dataclass(frozen=True)
class Row:
    """One bhavcopy row in a listing series."""

    symbol: str
    series: str
    isin: str | None
    close: float | None = None
    volume: int | None = None

    def evidence(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "series": self.series,
            "isin": self.isin,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass(frozen=True)
class SymbolChange:
    """A symbolchange.csv row, with its date as a session index (the next session when the
    date is not one)."""

    old_symbol: str
    new_symbol: str
    change_date: date
    session_index: int


@dataclass
class Listing:
    listing_id: int
    asset_class: AssetClass
    board: Board | None
    first_session: date
    last_session: date
    last_index: int
    symbol: str | None
    isin: str | None
    status: str = "active"
    first_isin_date: date | None = None  # the valid_from of its first ISIN period


class Store(Protocol):
    """Where the Book writes. Each call is one change to ref.listing*."""

    def new_listing(
        self, asset_class: AssetClass, board: Board | None, name: str | None, first_session: date
    ) -> int: ...

    def change(
        self,
        kind: ChangeKind,
        listing_id: int,
        effective_date: date,
        payload: Mapping[str, Any],
        evidence: Mapping[str, Any],
        status: Status,
    ) -> int: ...

    def open_period(
        self, attr: Literal["symbol", "isin"], listing_id: int, value: str, valid_from: date,
        change_id: int,
    ) -> None: ...  # fmt: skip

    def close_period(
        self, attr: Literal["symbol", "isin"], listing_id: int, valid_to: date
    ) -> None: ...


@dataclass
class SessionResult:
    session: date
    mapped: dict[tuple[str, str], int] = field(default_factory=dict)  # (symbol, series) -> id
    new_listings: int = 0
    symbol_changes: int = 0
    isin_changes: int = 0
    review_items: int = 0


class Book:
    """The listing state after the last applied session."""

    def __init__(
        self,
        store: Store,
        listings: Iterable[Listing] = (),
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        self.store = store
        self.aliases = dict(aliases or {})
        self.listings: dict[int, Listing] = {}
        self.by_symbol: dict[str, int] = {}
        self.by_isin: dict[str, int] = {}
        self.by_index_name: dict[str, int] = {}
        for listing in listings:
            self._index(listing)

    def _index(self, listing: Listing) -> None:
        self.listings[listing.listing_id] = listing
        if listing.status == "merged":
            return
        if listing.symbol is not None and listing.asset_class == "index":
            self.by_index_name[self.aliases.get(listing.symbol, listing.symbol)] = (
                listing.listing_id
            )
        elif listing.symbol is not None:
            self.by_symbol[listing.symbol] = listing.listing_id
        if listing.isin is not None:
            self.by_isin[listing.isin] = listing.listing_id

    def _recent(self, listing: Listing, index: int) -> bool:
        return index - listing.last_index <= RECENT_SESSIONS

    def _touch(self, listing: Listing, day: date, index: int) -> None:
        if day > listing.last_session:
            listing.last_session, listing.last_index = day, index
        if listing.status == "inactive":
            listing.status = "active"

    def _evidence(
        self, rule: str, day: date, file_ids: Sequence[int], **extra: Any
    ) -> dict[str, Any]:
        return {"rule": rule, "session": day.isoformat(), "file_ids": list(file_ids), **extra}

    def _take_symbol(self, listing: Listing, symbol: str, day: date, change_id: int) -> None:
        """Give listing the symbol from day, closing the periods it replaces."""
        holder = self.by_symbol.get(symbol)
        if holder is not None and holder != listing.listing_id:
            self.store.close_period("symbol", holder, day)
            self.listings[holder].symbol = None
        if listing.symbol is not None:
            self.store.close_period("symbol", listing.listing_id, day)
            if self.by_symbol.get(listing.symbol) == listing.listing_id:
                del self.by_symbol[listing.symbol]
        self.store.open_period("symbol", listing.listing_id, symbol, day, change_id)
        listing.symbol = symbol
        self.by_symbol[symbol] = listing.listing_id

    def _take_isin(self, listing: Listing, isin: str, day: date, change_id: int) -> None:
        if listing.isin is not None:
            self.store.close_period("isin", listing.listing_id, day)
            if self.by_isin.get(listing.isin) == listing.listing_id:
                del self.by_isin[listing.isin]
        self.store.open_period("isin", listing.listing_id, isin, day, change_id)
        listing.isin = isin
        if listing.first_isin_date is None:
            listing.first_isin_date = day
        self.by_isin[isin] = listing.listing_id

    def _new_listing(
        self, row: Row, day: date, index: int, file_ids: Sequence[int], rule: str,
        **extra: Any,
    ) -> Listing:  # fmt: skip
        kind = classify(row.series, row.isin)
        assert kind is not None
        asset_class, board = kind
        listing_id = self.store.new_listing(asset_class, board, None, day)
        listing = Listing(listing_id, asset_class, board, day, day, index, None, None)
        self.listings[listing_id] = listing
        change_id = self.store.change(
            "new_listing", listing_id, day,
            {"symbol": row.symbol, "isin": row.isin},
            self._evidence(rule, day, file_ids, row=row.evidence(), **extra), "applied",
        )  # fmt: skip
        self._take_symbol(listing, row.symbol, day, change_id)
        if row.isin is not None:
            self._take_isin(listing, row.isin, day, change_id)
        return listing

    def apply_session(
        self,
        day: date,
        index: int,
        rows: Sequence[Row],
        file_ids: Sequence[int],
        symbol_changes: Mapping[str, Sequence[SymbolChange]],
    ) -> SessionResult:
        """Apply one session's bhavcopy rows. symbol_changes maps a new symbol to the
        symbolchange.csv rows that end in it."""
        result = SessionResult(day)
        today_isins = {r.isin for r in rows if r.isin is not None}
        # Rows whose ISIN is known go first, so a listing that moved to a new symbol is
        # matched before its old symbol is offered to anyone else.
        known = [r for r in rows if r.isin is not None and r.isin in self.by_isin]
        rest = [r for r in rows if not (r.isin is not None and r.isin in self.by_isin)]

        for row in known:
            assert row.isin is not None
            listing = self.listings[self.by_isin[row.isin]]
            if listing.symbol != row.symbol:
                confirmed = self._find_change(symbol_changes, listing.symbol, row.symbol, index)
                change_id = self.store.change(
                    "symbol_change", listing.listing_id, day,
                    {"old_symbol": listing.symbol, "new_symbol": row.symbol},
                    self._evidence(
                        "5.2.1", day, file_ids, row=row.evidence(),
                        previous=self._previous(listing), confirmed=confirmed is not None,
                        **({"symbolchange": self._csv(confirmed)} if confirmed else {}),
                    ),
                    "applied",
                )  # fmt: skip
                self._take_symbol(listing, row.symbol, day, change_id)
                result.symbol_changes += 1
            self._touch(listing, day, index)
            result.mapped[(row.symbol, row.series)] = listing.listing_id

        for row in rest:
            listing = self._match_unknown(row, day, index, file_ids, today_isins, symbol_changes,
                                          result)  # fmt: skip
            self._touch(listing, day, index)
            result.mapped[(row.symbol, row.series)] = listing.listing_id
        return result

    def _match_unknown(
        self,
        row: Row,
        day: date,
        index: int,
        file_ids: Sequence[int],
        today_isins: set[str],
        symbol_changes: Mapping[str, Sequence[SymbolChange]],
        result: SessionResult,
    ) -> Listing:
        holder_id = self.by_symbol.get(row.symbol)
        holder = self.listings[holder_id] if holder_id is not None else None

        if row.isin is None:
            if holder is not None:  # before ISINs a symbol is the identity
                return holder
            change = self._rule_3(row, index, symbol_changes)
            if change is not None:
                listing = self.listings[self.by_symbol[change.old_symbol]]
                change_id = self.store.change(
                    "symbol_change", listing.listing_id, day,
                    {"old_symbol": change.old_symbol, "new_symbol": row.symbol},
                    self._evidence("5.2.3", day, file_ids, row=row.evidence(),
                                   previous=self._previous(listing),
                                   symbolchange=self._csv(change)),
                    "applied",
                )  # fmt: skip
                self._take_symbol(listing, row.symbol, day, change_id)
                result.symbol_changes += 1
                return listing
            result.new_listings += 1
            return self._new_listing(row, day, index, file_ids, "5.2.4")

        if holder is not None and holder.isin is None and self._recent(holder, index):
            # The first file with ISINs (5.3): the listing takes its row's ISIN, dated from its
            # first session, since the ISIN names the same security.
            change_id = self.store.change(
                "isin_change", holder.listing_id, holder.first_session,
                {"old_isin": None, "new_isin": row.isin},
                self._evidence("5.3", day, file_ids, row=row.evidence()), "applied",
            )  # fmt: skip
            self._take_isin(holder, row.isin, holder.first_session, change_id)
            return holder

        if holder is not None and holder.isin not in today_isins:
            if self._recent(holder, index):
                change_id = self.store.change(
                    "isin_change", holder.listing_id, day,
                    {"old_isin": holder.isin, "new_isin": row.isin},
                    self._evidence("5.2.2", day, file_ids, row=row.evidence(),
                                   previous=self._previous(holder)),
                    "applied",
                )  # fmt: skip
                self._take_isin(holder, row.isin, day, change_id)
                result.isin_changes += 1
                return holder
            listing = self._new_listing(row, day, index, file_ids, "5.2.4")
            self.store.change(
                "merge", listing.listing_id, day,
                {"merged_id": listing.listing_id, "into_id": holder.listing_id},
                self._evidence("5.2.2", day, file_ids, row=row.evidence(),
                               previous=self._previous(holder),
                               candidates=[holder.listing_id]),
                "pending",
            )  # fmt: skip
            result.new_listings += 1
            result.review_items += 1
            return listing

        result.new_listings += 1
        return self._new_listing(row, day, index, file_ids, "5.2.4")

    def _rule_3(
        self, row: Row, index: int, symbol_changes: Mapping[str, Sequence[SymbolChange]]
    ) -> SymbolChange | None:
        for change in symbol_changes.get(row.symbol, ()):
            if abs(change.session_index - index) > SYMBOL_CHANGE_SESSIONS:
                continue
            old_id = self.by_symbol.get(change.old_symbol)
            if old_id is not None and self._recent(self.listings[old_id], index):
                return change
        return None

    @staticmethod
    def _find_change(
        symbol_changes: Mapping[str, Sequence[SymbolChange]],
        old: str | None,
        new: str,
        index: int,
    ) -> SymbolChange | None:
        for change in symbol_changes.get(new, ()):
            if change.old_symbol == old and (
                abs(change.session_index - index) <= SYMBOL_CHANGE_SESSIONS
            ):
                return change
        return None

    @staticmethod
    def _csv(change: SymbolChange) -> dict[str, str]:
        return {
            "old_symbol": change.old_symbol,
            "new_symbol": change.new_symbol,
            "date": change.change_date.isoformat(),
        }

    @staticmethod
    def _previous(listing: Listing) -> dict[str, Any]:
        return {
            "date": listing.last_session.isoformat(),
            "symbol": listing.symbol,
            "isin": listing.isin,
        }

    def apply_indices(
        self,
        day: date,
        index: int,
        names: Sequence[str],
        file_ids: Sequence[int],
    ) -> SessionResult:
        """Apply one session's index names from ind_close_all (spec 0002, 5.5). A name maps to
        its canonical name through the alias seed; a listing is kept per canonical name."""
        result = SessionResult(day)
        seen_before = {
            lid for lid in self.by_index_name.values()
            if self.listings[lid].last_index == index - 1
        }  # fmt: skip
        new: list[Listing] = []
        for name in names:
            canonical = self.aliases.get(name, name)
            listing_id = self.by_index_name.get(canonical)
            if listing_id is None:
                listing_id = self.store.new_listing("index", None, canonical, day)
                listing = Listing(listing_id, "index", None, day, day, index, None, None)
                self.listings[listing_id] = listing
                change_id = self.store.change(
                    "new_listing", listing_id, day, {"symbol": name},
                    self._evidence("5.5", day, file_ids), "applied",
                )  # fmt: skip
                self.store.open_period("symbol", listing_id, name, day, change_id)
                listing.symbol = name
                self.by_index_name[canonical] = listing_id
                new.append(listing)
                result.new_listings += 1
            else:
                listing = self.listings[listing_id]
                if listing.symbol != name:
                    change_id = self.store.change(
                        "index_rename", listing_id, day,
                        {"old_name": listing.symbol, "new_name": name},
                        self._evidence("5.5", day, file_ids), "applied",
                    )  # fmt: skip
                    self.store.close_period("symbol", listing_id, day)
                    self.store.open_period("symbol", listing_id, name, day, change_id)
                    listing.symbol = name
                    result.symbol_changes += 1
            self._touch(listing, day, index)
            result.mapped[(name, "")] = listing.listing_id

        # A new name on the day an old one stopped may be a rename the seed doesn't know.
        gone = sorted(lid for lid in seen_before if self.listings[lid].last_index < index)
        if gone:
            for listing in new:
                self.store.change(
                    "index_rename", listing.listing_id, day,
                    {"new_name": listing.symbol},
                    self._evidence("5.5", day, file_ids, candidates=gone), "pending",
                )  # fmt: skip
                result.review_items += 1
        return result

    def refresh_status(self, latest_index: int, in_masters: set[str]) -> list[int]:
        """Mark inactive every listing silent for more than 20 sessions whose symbol is in none
        of the current masters. Returns the listings whose status changed."""
        changed = []
        for listing in self.listings.values():
            if listing.status == "merged":
                continue
            silent = latest_index - listing.last_index > RECENT_SESSIONS
            listed = listing.symbol is not None and listing.symbol in in_masters
            status = "inactive" if silent and not listed else "active"
            if status != listing.status:
                listing.status = status
                changed.append(listing.listing_id)
        return changed
