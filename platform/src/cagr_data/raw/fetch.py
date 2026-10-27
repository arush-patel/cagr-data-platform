"""Fetch one feed's file for one partition and ingest it (spec 0001, sections 7.1 to 7.4).

A partition already received is not downloaded again unless a revision check asks for it. Every
request lands in raw.fetch_attempt, including 404s and failures. A 404 is the answer "no file
for that day" and is not an error.
"""

from dataclasses import dataclass
from datetime import date

from cagr_data.core.db import Connection
from cagr_data.core.storage import Storage
from cagr_data.raw.feeds import Feed
from cagr_data.raw.ingest import IngestResult, ingest_file
from cagr_data.raw.receipts import record_attempt
from cagr_data.vendors.http import VendorClient
from cagr_data.vendors.nse.urls import build_url

API_HEADERS = {"Referer": "https://www.nseindia.com/", "Accept": "application/json"}


@dataclass(frozen=True)
class FetchOutcome:
    feed_id: str
    day: date
    url: str
    status: str  # "stored" (already had it), "missing" (404), "ingested", "rejected"
    http_status: int | None = None
    result: IngestResult | None = None


def feeds_for(conn: Connection, dataset: str, day: date) -> list[Feed]:
    """Active feeds of a dataset that publish files for day, primary first."""
    rows = conn.execute(
        """
        SELECT feed_id, vendor_id, dataset, format, url_template, cadence
          FROM raw.feed
         WHERE dataset = %s AND active
           AND (available_from IS NULL OR available_from <= %s)
           AND (available_to IS NULL OR available_to >= %s)
         ORDER BY priority
        """,
        (dataset, day, day),
    ).fetchall()
    return [Feed(*r) for r in rows]


def has_receipt(conn: Connection, feed_id: str, day: date) -> bool:
    row = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM raw.vendor_file WHERE feed_id = %s AND partition_date = %s)",
        (feed_id, day),
    ).fetchone()
    return bool(row and row[0])


def fetch_and_ingest(
    conn: Connection,
    client: VendorClient,
    storage: Storage,
    *,
    run_id: int,
    feed: Feed,
    day: date,
    url_day: date | None = None,
    revision_check: bool = False,
) -> FetchOutcome:
    """Fetch feed's file for partition day (url_day fills the URL if it differs) and ingest it.

    Commits nothing; the caller commits once it has looked at the result.
    """
    url = build_url(feed.url_template, url_day or day)
    if not revision_check and has_receipt(conn, feed.feed_id, day):
        return FetchOutcome(feed.feed_id, day, url, "stored")
    headers = API_HEADERS if feed.vendor_id == "nse_api" else None
    try:
        fetched = client.get(url, headers)
    except Exception as e:
        record_attempt(conn, run_id=run_id, feed_id=feed.feed_id, day=day, url=url,
                       error=f"{type(e).__name__}: {e}"[:1000])  # fmt: skip
        raise
    record_attempt(conn, run_id=run_id, feed_id=feed.feed_id, day=day, url=url,
                   http_status=fetched.status, elapsed_ms=fetched.elapsed_ms)  # fmt: skip
    if not fetched.found:
        return FetchOutcome(feed.feed_id, day, url, "missing", fetched.status)
    result = ingest_file(
        conn, storage, run_id=run_id, feed=feed, day=day, url=url, content=fetched.content
    )
    status = "ingested" if result.receipt is not None else "rejected"
    return FetchOutcome(feed.feed_id, day, url, status, fetched.status, result)
