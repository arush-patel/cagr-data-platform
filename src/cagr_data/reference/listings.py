"""Listings, identifiers and tickers (D4).

Every change goes through ref.listing_change and is applied by ref.apply_listing_change, so the
queue is the audit trail. Exact-ISIN cases auto-apply: a new ISIN whose ticker is free, and a new
ticker for a known ISIN. A new ISIN on a ticker another listing holds is usually the new ISIN NSE
issues on a face-value split (KSB, PGEL, ELECON in July 2024). It auto-applies as an identifier
change only on evidence: the holder's ISIN is gone from that file, both ISINs share the issuer code,
and a parsed split or consolidation for the holder has its ex-date in the days before. Anything
else waits for a person.

All changes are date-forward: process sessions oldest first, as the nightly run and the history
load both do.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
import psycopg


@dataclass
class ListingIndex:
    """Lookups for one venue, as of the latest applied change."""

    venue_id: int
    by_isin: dict[str, int] = field(default_factory=dict)  # every ISIN the instrument ever had → listing
    current_ticker: dict[int, tuple[str, date]] = field(
        default_factory=dict
    )  # listing → (ticker, valid_from)
    ticker_holder: dict[str, int] = field(default_factory=dict)  # current ticker → listing
    pending_isins: set[str] = field(default_factory=set)  # ISINs waiting in the queue

    @classmethod
    def load(cls, conn: psycopg.Connection[Any], venue_id: int) -> ListingIndex:
        ix = cls(venue_id)
        for r in conn.execute(
            "SELECT ii.value, l.listing_id FROM ref.instrument_identifier ii "
            "JOIN ref.listing l ON l.instrument_id = ii.instrument_id "
            "WHERE ii.id_type = 'isin' AND l.venue_id = %s ORDER BY ii.valid_from",
            (venue_id,),
        ):
            ix.by_isin[r["value"]] = r["listing_id"]
        for r in conn.execute(
            "SELECT listing_id, ticker, valid_from FROM ref.listing_symbol WHERE venue_id = %s AND valid_to IS NULL",
            (venue_id,),
        ):
            ix.current_ticker[r["listing_id"]] = (r["ticker"], r["valid_from"])
            ix.ticker_holder[r["ticker"]] = r["listing_id"]
        for r in conn.execute(
            "SELECT payload ->> 'isin' AS isin FROM ref.listing_change "
            "WHERE status IN ('proposed','approved') AND payload ? 'isin'"
        ):
            ix.pending_isins.add(r["isin"])
        return ix

    def ticker_as_of(self, conn: psycopg.Connection[Any], ticker: str, d: date) -> int | None:
        row = conn.execute(
            "SELECT listing_id FROM ref.listing_symbol WHERE venue_id = %s AND ticker = %s "
            "AND valid_from <= %s AND (valid_to IS NULL OR valid_to > %s)",
            (self.venue_id, ticker, d, d),
        ).fetchone()
        return None if row is None else int(row["listing_id"])


# NSE's new ISIN can first trade a few sessions after the split's ex-date (KAMOPAINTS: 14 → 18 Jun 2024).
SPLIT_ISIN_WINDOW_DAYS = 10


@dataclass
class SyncResult:
    new_listings: int = 0
    ticker_changes: int = 0
    isin_changes: int = 0
    queued_for_review: int = 0
    details: list[dict[str, Any]] = field(default_factory=list)


def asset_class_for(isin: str) -> str:
    # Indian ISINs: INE… companies, INF… mutual-fund units (ETFs on the exchange).
    return "etf" if isin.startswith("INF") else "equity"


def _queue(
    conn: psycopg.Connection[Any],
    kind: str,
    listing_id: int | None,
    effective: date,
    payload: dict[str, Any],
    feed_id: int | None,
    auto: bool,
    note: str | None = None,
) -> int:
    row = conn.execute(
        "INSERT INTO ref.listing_change (kind, listing_id, effective_date, payload, source_feed_id, auto_apply, note) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING change_id",
        (kind, listing_id, effective, json.dumps(payload, default=str), feed_id, auto, note),
    ).fetchone()
    assert row is not None
    change_id = int(row["change_id"])
    if auto:
        conn.execute("SELECT ref.apply_listing_change(%s)", (change_id,))
    return change_id


def same_issuer(a: str, b: str) -> bool:
    """Indian ISINs: IN + issuer type and code (5) + security type (2) + serial (2) + check digit."""
    return len(a) == len(b) == 12 and a[:9] == b[:9]


def split_explains(conn: psycopg.Connection[Any], listing_id: int, d: date) -> int | None:
    """The action_id of a parsed split or consolidation of `listing_id` with ex-date shortly before `d`."""
    row = conn.execute(
        "SELECT action_id FROM clean.corporate_action WHERE listing_id = %s "
        "AND kind IN ('split','consolidation') AND parse_status IN ('parsed','manual') AND status <> 'rejected' "
        "AND ex_date <= %s AND ex_date > %s::date - %s ORDER BY ex_date DESC LIMIT 1",
        (listing_id, d, d, SPLIT_ISIN_WINDOW_DAYS),
    ).fetchone()
    return None if row is None else int(row["action_id"])


def _isin_change(
    conn: psycopg.Connection[Any], ix: ListingIndex, listing_id: int, isin: str, d: date,
    feed_id: int | None, action_id: int,
) -> int:  # fmt: skip
    change_id = _queue(conn, "identifier_change", listing_id, d, {"id_type": "isin", "value": isin}, feed_id,
                       True, note=f"new ISIN after corporate_action {action_id}")  # fmt: skip
    ix.by_isin[isin] = listing_id
    ix.pending_isins.discard(isin)
    return change_id


def resolve_isin_changes(conn: psycopg.Connection[Any], ix: ListingIndex) -> list[tuple[int, date]]:
    """Queued new ISINs that a split loaded since explains become identifier changes on the holder.

    A backfill sees the files before it loads corporate actions, so it queues every post-split ISIN
    first and resolves them here. Returns (listing, date) for each, so the caller can re-read the
    files from that date: those rows were left unmapped the first time.
    """
    out: list[tuple[int, date]] = []
    for c in conn.execute(
        "SELECT change_id, effective_date, payload, source_feed_id FROM ref.listing_change "
        "WHERE kind = 'new_listing' AND status = 'proposed' AND (payload ->> 'holder_isin_absent')::boolean "
        "ORDER BY effective_date, change_id"
    ).fetchall():
        p, d = c["payload"], c["effective_date"]
        holder = int(p["ticker_held_by_listing"])
        holder_isins = [k for k, v in ix.by_isin.items() if v == holder]
        action = split_explains(conn, holder, d)
        if action is None or not any(same_issuer(p["isin"], h) for h in holder_isins):
            continue
        new_id = _isin_change(conn, ix, holder, p["isin"], d, c["source_feed_id"], action)
        conn.execute(
            "UPDATE ref.listing_change SET status = 'rejected', decided_by = 'pipeline', decided_at = now(), "
            "note = %s WHERE change_id = %s",
            (f"superseded by identifier_change {new_id}", c["change_id"]),
        )
        out.append((holder, d))
    return out


def observe(
    conn: psycopg.Connection[Any],
    ix: ListingIndex,
    rows: pd.DataFrame,
    session: date,
    *,
    venue_code: str,
    feed_id: int | None,
    listed_on: dict[str, date] | None = None,
) -> SyncResult:
    """Reconcile one session's (isin, symbol, name) rows with the reference data.

    `listed_on` gives listing dates from the exchange master when known; otherwise a new listing
    starts on the session it was first seen.
    """
    res = SyncResult()
    isins_today = set(rows["isin"])
    for r in rows.itertuples(index=False):
        isin, ticker, name = r.isin, r.symbol, (r.name or r.symbol)
        if not isin:
            continue
        lid = ix.by_isin.get(isin)
        if lid is not None:
            cur = ix.current_ticker.get(lid)
            if cur is None or cur[0] == ticker or session <= cur[1]:
                continue
            holder = ix.ticker_holder.get(ticker)
            if holder is None:
                _queue(
                    conn,
                    "ticker_change",
                    lid,
                    session,
                    {"ticker": ticker, "display_name": name},
                    feed_id,
                    True,
                )
                del ix.ticker_holder[cur[0]]
                ix.current_ticker[lid] = (ticker, session)
                ix.ticker_holder[ticker] = lid
                res.ticker_changes += 1
            else:
                res.details.append(
                    {"isin": isin, "issue": "ticker_swap", "ticker": ticker, "held_by": holder}
                )
            continue
        if isin in ix.pending_isins:
            continue
        holder = ix.ticker_holder.get(ticker)
        start = (listed_on or {}).get(isin) or session
        if start > session:
            start = session
        payload = {
            "venue": venue_code,
            "asset_class": asset_class_for(isin),
            "name": name,
            "ticker": ticker,
            "isin": isin,
            "listed_on": start,
        }
        if holder is None:
            _queue(conn, "new_listing", None, session, payload, feed_id, True)
            new_id = conn.execute(
                "SELECT l.listing_id FROM ref.instrument_identifier ii JOIN ref.listing l USING (instrument_id) "
                "WHERE ii.id_type = 'isin' AND ii.value = %s AND l.venue_id = %s",
                (isin, ix.venue_id),
            ).fetchone()
            assert new_id is not None
            lid = int(new_id["listing_id"])
            ix.by_isin[isin] = lid
            ix.current_ticker[lid] = (ticker, session)
            ix.ticker_holder[ticker] = lid
            res.new_listings += 1
        else:
            holder_isins = {k for k, v in ix.by_isin.items() if v == holder}
            absent = not holder_isins & isins_today
            action = split_explains(conn, holder, session) if absent else None
            if action is not None and any(same_issuer(isin, h) for h in holder_isins):
                _isin_change(conn, ix, holder, isin, session, feed_id, action)
                res.isin_changes += 1
                continue
            reason = (
                "ticker held by a listing whose ISIN is absent today: likely a new ISIN for the same company"
                if absent
                else "ticker held by another listing that also trades today"
            )
            payload["ticker_held_by_listing"] = holder
            payload["holder_isin_absent"] = absent
            _queue(conn, "new_listing", None, session, payload, feed_id, False, note=reason)
            ix.pending_isins.add(isin)
            res.queued_for_review += 1
            res.details.append(
                {"isin": isin, "issue": "unknown_isin_ticker_taken", "ticker": ticker, "held_by": holder}
            )
    return res


def sync_master(
    conn: psycopg.Connection[Any],
    ix: ListingIndex,
    master: pd.DataFrame,
    as_of: date,
    *,
    venue_code: str,
    feed_id: int,
    create: bool = True,
) -> SyncResult:
    """EQUITY_L / SME_EQUITY_L: new ISINs become listings from their listing date; names and dates fill in.

    The master is a snapshot of the day it is downloaded. With `create` off (a backfill that ends
    in the past) it only fills names and listing dates: today's tickers and ISINs did not hold then.
    """
    res = SyncResult()
    if create:
        res = observe(
            conn,
            ix,
            master[["isin", "symbol", "name"]],
            as_of,
            venue_code=venue_code,
            feed_id=feed_id,
            listed_on={r.isin: r.listed_on for r in master.itertuples(index=False) if pd.notna(r.listed_on)},
        )
    # The master knows the real listing date and name; fill them where we only had first-seen values.
    for r in master.itertuples(index=False):
        lid = ix.by_isin.get(r.isin)
        if lid is None:
            continue
        conn.execute(
            "UPDATE ref.listing SET listed_on = %s WHERE listing_id = %s AND (listed_on IS NULL OR listed_on > %s)",
            (r.listed_on, lid, r.listed_on),
        ) if pd.notna(r.listed_on) else None
        conn.execute(
            "UPDATE ref.instrument i SET name = %s FROM ref.listing l "
            "WHERE l.listing_id = %s AND l.instrument_id = i.instrument_id AND i.name IS DISTINCT FROM %s",
            (r.name, lid, r.name),
        )
    return res


def apply_symbol_changes(
    conn: psycopg.Connection[Any], ix: ListingIndex, changes: pd.DataFrame, *, since: date, feed_id: int
) -> SyncResult:
    """symbolchange.csv rows on or after `since`: the old ticker's listing takes the new ticker."""
    res = SyncResult()
    for r in changes[changes["changed_on"] >= since].sort_values("changed_on").itertuples(index=False):
        lid = ix.ticker_holder.get(r.old_symbol)
        if lid is None:
            continue
        cur = ix.current_ticker[lid]
        if r.changed_on <= cur[1] or r.new_symbol in ix.ticker_holder:
            continue
        _queue(
            conn,
            "ticker_change",
            lid,
            r.changed_on,
            {"ticker": r.new_symbol, "display_name": r.name},
            feed_id,
            True,
        )
        del ix.ticker_holder[r.old_symbol]
        ix.current_ticker[lid] = (r.new_symbol, r.changed_on)
        ix.ticker_holder[r.new_symbol] = lid
        res.ticker_changes += 1
    return res
