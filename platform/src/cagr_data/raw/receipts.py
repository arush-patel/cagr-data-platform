"""Receipts: raw.fetch_attempt for every request and raw.vendor_file for every file received
(spec 0001, sections 6.3, 7.3 and 7.4).

The same bytes for the same feed and date are one receipt, however often they arrive. Different
bytes become the next revision, stored under a new key and superseding the previous one.
"""

import hashlib
from dataclasses import dataclass
from datetime import date

from cagr_data.core.db import Connection
from cagr_data.core.storage import Storage, raw_key
from cagr_data.raw.feeds import Feed, stored_name

VENDOR_FOLDER = {"nse_archives": "nse", "nse_api": "nse"}


@dataclass(frozen=True)
class Receipt:
    file_id: int
    revision: int
    supersedes_file_id: int | None
    storage_uri: str
    sha256: str
    is_new: bool


def record_attempt(
    conn: Connection,
    *,
    run_id: int | None,
    feed_id: str,
    day: date,
    url: str,
    http_status: int | None = None,
    error: str | None = None,
    elapsed_ms: int | None = None,
) -> int:
    row = conn.execute(
        """
        INSERT INTO raw.fetch_attempt
               (run_id, feed_id, partition_date, url, http_status, error, elapsed_ms)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING attempt_id
        """,
        (run_id, feed_id, day, url, http_status, error, elapsed_ms),
    ).fetchone()
    assert row is not None
    return int(row[0])


def receive(
    conn: Connection,
    storage: Storage,
    *,
    run_id: int | None,
    feed: Feed,
    day: date,
    url: str,
    content: bytes,
    row_count: int,
) -> Receipt:
    """Store the bytes and write the receipt, or return the receipt these bytes already have.

    Runs in the caller's transaction, which should also load the rows, so a receipt never
    exists without its rows.
    """
    sha = hashlib.sha256(content).hexdigest()
    same = conn.execute(
        "SELECT file_id, revision, supersedes_file_id, storage_uri FROM raw.vendor_file"
        " WHERE feed_id = %s AND partition_date = %s AND sha256 = %s",
        (feed.feed_id, day, sha),
    ).fetchone()
    if same is not None:
        return Receipt(same[0], same[1], same[2], same[3], sha, is_new=False)

    latest = conn.execute(
        "SELECT file_id, revision FROM raw.vendor_file"
        " WHERE feed_id = %s AND partition_date = %s ORDER BY revision DESC LIMIT 1",
        (feed.feed_id, day),
    ).fetchone()
    revision = 1 if latest is None else latest[1] + 1
    supersedes = None if latest is None else latest[0]

    key = raw_key(
        VENDOR_FOLDER.get(feed.vendor_id, feed.vendor_id),
        feed.feed_id,
        day,
        stored_name(feed, day, url),
        sha if revision > 1 else None,
    )
    uri = storage.put(key, content)
    row = conn.execute(
        """
        INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes, storage_uri,
                                     run_id, revision, supersedes_file_id, row_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING file_id
        """,
        (feed.feed_id, day, url, sha, len(content), uri, run_id, revision, supersedes, row_count),
    ).fetchone()
    assert row is not None
    return Receipt(int(row[0]), revision, supersedes, uri, sha, is_new=True)
