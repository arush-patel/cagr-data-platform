"""Feeds as rows (raw.feed) and what each format means in code: its parser, its row table and
the name its files are stored under."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from urllib.parse import urlsplit

import polars as pl

from cagr_data.core.db import Connection
from cagr_data.vendors.nse.bhavcopy import parse_bhavcopy
from cagr_data.vendors.nse.holidays import parse_holidays
from cagr_data.vendors.nse.index_close import parse_index_close
from cagr_data.vendors.nse.listings import parse_equity_list, parse_symbol_change


@dataclass(frozen=True)
class Feed:
    feed_id: str
    vendor_id: str
    dataset: str
    format: str
    url_template: str
    cadence: str


@dataclass(frozen=True)
class FormatSpec:
    table: str
    parse: Callable[[bytes, date], pl.DataFrame]
    """Parse a file's bytes; the date is the partition date (a session, a snapshot day or, for
    the holiday list, 1 Jan of its year)."""


FORMATS: dict[str, FormatSpec] = {
    "legacy_v1": FormatSpec("raw.nse_cm_bar", lambda b, d: parse_bhavcopy(b, "legacy_v1", d)),
    "udiff_v1": FormatSpec("raw.nse_cm_bar", lambda b, d: parse_bhavcopy(b, "udiff_v1", d)),
    "ind_close_all_v1": FormatSpec("raw.nse_index_close", parse_index_close),
    "holiday_master_v1": FormatSpec("raw.nse_holiday", lambda b, d: parse_holidays(b, d.year)),
    "equity_l_v1": FormatSpec("raw.nse_equity_list", lambda b, d: parse_equity_list(b, "main", d)),
    "sme_equity_l_v1": FormatSpec(
        "raw.nse_equity_list", lambda b, d: parse_equity_list(b, "sme", d)
    ),
    "symbolchange_v1": FormatSpec("raw.nse_symbol_change", parse_symbol_change),
}


def load_feed(conn: Connection, feed_id: str) -> Feed:
    row = conn.execute(
        "SELECT feed_id, vendor_id, dataset, format, url_template, cadence"
        " FROM raw.feed WHERE feed_id = %s",
        (feed_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"unknown feed {feed_id}")
    return Feed(*row)


def stored_name(feed: Feed, day: date, url: str) -> str:
    """The file name a feed's file is stored under.

    Session files keep NSE's name, which already holds the date. Snapshots are fetched daily
    under a fixed name, so the snapshot date goes before the extension. The holiday API has no
    file name; its list is named after its year.
    """
    if feed.format == "holiday_master_v1":
        return f"holiday-master_trading_{day:%Y}.json"
    name = urlsplit(url).path.rsplit("/", 1)[-1]
    if feed.cadence == "daily":
        stem, dot, rest = name.partition(".")
        return f"{stem}_{day:%Y%m%d}{dot}{rest}"
    return name
