"""The listing rules (spec 0002, section 5) on an in-memory store."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from cagr_data.reference.listings import Book, Row, SessionResult, SymbolChange, classify

D0 = date(2024, 1, 1)


@dataclass
class FakeStore:
    listings: list[tuple[Any, ...]] = field(default_factory=list)
    changes: list[dict[str, Any]] = field(default_factory=list)
    periods: list[list[Any]] = field(default_factory=list)  # attr, id, value, from, to

    def new_listing(
        self, asset_class: str, board: str | None, name: str | None, first: date
    ) -> int:
        self.listings.append((asset_class, board, name, first))
        return len(self.listings)

    def change(
        self, kind: str, listing_id: int, effective_date: date, payload: Mapping[str, Any],
        evidence: Mapping[str, Any], status: str,
    ) -> int:  # fmt: skip
        self.changes.append(
            {"kind": kind, "listing_id": listing_id, "date": effective_date,
             "payload": dict(payload), "evidence": dict(evidence), "status": status}
        )  # fmt: skip
        return len(self.changes)

    def open_period(self, attr: str, listing_id: int, value: str, start: date, _: int) -> None:
        self.periods.append([attr, listing_id, value, start, None])

    def close_period(self, attr: str, listing_id: int, end: date) -> None:
        for p in self.periods:
            if p[0] == attr and p[1] == listing_id and p[4] is None:
                p[4] = end

    def symbols(self, listing_id: int) -> list[tuple[str, date, date | None]]:
        return [(p[2], p[3], p[4]) for p in self.periods if p[0] == "symbol" and p[1] == listing_id]

    def isins(self, listing_id: int) -> list[tuple[str, date, date | None]]:
        return [(p[2], p[3], p[4]) for p in self.periods if p[0] == "isin" and p[1] == listing_id]


def day(i: int) -> date:
    return D0 + timedelta(days=i)


def run(
    book: Book, i: int, *rows: Row, changes: Mapping[str, list[SymbolChange]] | None = None
) -> SessionResult:
    return book.apply_session(day(i), i, list(rows), [100 + i], changes or {})


def test_series_make_listings() -> None:
    assert classify("EQ", "INE002A01018") == ("equity", "main")
    assert classify("EQ", "INF204KB14I2") == ("etf", "main")
    assert classify("SM", "INE0ABC01011") == ("equity", "sme")
    assert classify("RR", None) == ("reit", "main")
    assert classify("IV", None) == ("invit", "main")
    assert classify("T0", "INE002A01018") is None
    assert classify("N1", None) is None


def test_new_rows_make_listings_and_a_known_isin_keeps_them() -> None:
    store = FakeStore()
    book = Book(store)
    r = run(book, 1, Row("RELIANCE", "EQ", "INE002A01018"), Row("TCS", "EQ", "INE467B01029"))
    assert r.new_listings == 2
    r = run(book, 2, Row("RELIANCE", "EQ", "INE002A01018"))
    assert r.new_listings == 0
    assert r.mapped == {("RELIANCE", "EQ"): 1}
    assert book.listings[1].last_session == day(2)
    assert book.listings[2].last_session == day(1)


def test_rule_1_symbol_change_on_a_known_isin() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("ZOMATO", "EQ", "INE758T01015"))
    csv = {"ETERNAL": [SymbolChange("ZOMATO", "ETERNAL", day(2), 2)]}
    r = run(book, 2, Row("ETERNAL", "EQ", "INE758T01015"), changes=csv)
    assert r.symbol_changes == 1 and r.new_listings == 0
    assert store.symbols(1) == [("ZOMATO", day(1), day(2)), ("ETERNAL", day(2), None)]
    change = store.changes[-1]
    assert change["kind"] == "symbol_change" and change["status"] == "applied"
    assert change["evidence"]["rule"] == "5.2.1"
    assert change["evidence"]["confirmed"] is True
    assert change["evidence"]["symbolchange"]["old_symbol"] == "ZOMATO"


def test_rule_1_unconfirmed_symbol_change_is_still_applied_and_flagged() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("OLD", "EQ", "INE000A01011"))
    run(book, 2, Row("NEW", "EQ", "INE000A01011"))
    assert store.changes[-1]["evidence"]["confirmed"] is False


def test_rule_2_isin_change_on_a_recent_listing() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("ABC", "EQ", "INE000A01011"))
    r = run(book, 5, Row("ABC", "EQ", "INE000A01029"))  # a face-value split
    assert r.isin_changes == 1 and r.new_listings == 0
    assert store.isins(1) == [("INE000A01011", day(1), day(5)), ("INE000A01029", day(5), None)]


def test_rule_2_long_silence_makes_a_new_listing_and_a_merge_review() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("ABC", "EQ", "INE000A01011"))
    r = run(book, 40, Row("ABC", "EQ", "INE000B01011"))
    assert r.new_listings == 1 and r.review_items == 1
    merge = store.changes[-1]
    assert merge["kind"] == "merge" and merge["status"] == "pending"
    assert merge["payload"] == {"merged_id": 2, "into_id": 1}
    assert merge["evidence"]["candidates"] == [1]
    assert store.symbols(1) == [("ABC", day(1), day(40))]  # the symbol moved on
    assert store.symbols(2) == [("ABC", day(40), None)]


def test_rule_2_needs_the_old_isin_gone() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("ABC", "EQ", "INE000A01011"))
    r = run(book, 2, Row("ABC", "EQ", "INE000A01011"), Row("ABC", "BE", "INE000A01029"))
    assert r.isin_changes == 0
    assert r.new_listings == 1  # a second security under the same symbol


def test_rule_3_symbol_change_before_isins() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("OLDNAME", "EQ", None))
    csv = {"NEWNAME": [SymbolChange("OLDNAME", "NEWNAME", day(4), 4)]}
    r = run(book, 3, Row("NEWNAME", "EQ", None), changes=csv)
    assert r.symbol_changes == 1 and r.new_listings == 0
    assert store.changes[-1]["evidence"]["rule"] == "5.2.3"


def test_rule_3_outside_the_window_is_a_new_listing() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("OLDNAME", "EQ", None))
    csv = {"NEWNAME": [SymbolChange("OLDNAME", "NEWNAME", day(20), 20)]}
    r = run(book, 3, Row("NEWNAME", "EQ", None), changes=csv)
    assert r.new_listings == 1


def test_first_isin_is_dated_from_the_first_session() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("RELIANCE", "EQ", None))
    run(book, 2, Row("RELIANCE", "EQ", "INE002A01018"))
    assert store.isins(1) == [("INE002A01018", day(1), None)]
    assert store.changes[-1]["evidence"]["rule"] == "5.3"


def test_index_renames_follow_the_alias_seed() -> None:
    store = FakeStore()
    book = Book(store, aliases={"S&P CNX Nifty": "Nifty 50", "CNX Nifty": "Nifty 50"})
    book.apply_indices(day(1), 1, ["S&P CNX Nifty", "Nifty Bank"], [1])
    r = book.apply_indices(day(2), 2, ["CNX Nifty", "Nifty Bank"], [2])
    assert r.new_listings == 0 and r.symbol_changes == 1
    book.apply_indices(day(3), 3, ["Nifty 50", "Nifty Bank"], [3])
    assert [s for s, _, _ in store.symbols(1)] == ["S&P CNX Nifty", "CNX Nifty", "Nifty 50"]
    assert store.listings[0] == ("index", None, "Nifty 50", day(1))


def test_an_unknown_index_name_when_another_stops_is_a_review_item() -> None:
    store = FakeStore()
    book = Book(store)
    book.apply_indices(day(1), 1, ["CNX Bank", "CNX IT"], [1])
    r = book.apply_indices(day(2), 2, ["CNX IT", "Nifty Bank"], [2])
    assert r.new_listings == 1 and r.review_items == 1
    review = store.changes[-1]
    assert review["kind"] == "index_rename" and review["status"] == "pending"
    assert review["evidence"]["candidates"] == [1]


def test_status_follows_silence_and_the_masters() -> None:
    store = FakeStore()
    book = Book(store)
    run(book, 1, Row("GONE", "EQ", "INE000A01011"), Row("QUIET", "SM", "INE000B01011"))
    run(book, 30, Row("ACTIVE", "EQ", "INE000C01011"))
    assert book.refresh_status(30, in_masters={"QUIET", "ACTIVE"}) == [1]
    assert book.listings[1].status == "inactive"
    assert book.listings[2].status == "active"
    run(book, 31, Row("GONE", "EQ", "INE000A01011"))
    assert book.listings[1].status == "active"
