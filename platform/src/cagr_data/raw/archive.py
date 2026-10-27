"""Pack loaded raw files into one archive per feed and month, then delete the loose files.

    uv run python -m cagr_data.raw.archive            # every month that ended 7+ days ago
    uv run python -m cagr_data.raw.archive --dry-run  # say what would be packed

A month of a feed is packed when it has ended at least SETTLE days ago, so a late revision has
had time to arrive. Every file is read back and checked against the sha256 in its receipt before
it goes in, and again from the archive before its receipt is pointed at the archive. Only then,
after the commit, is the loose file deleted. A file that fails a check stays loose and is
reported. Files received after a month was packed go into a second archive (MM@2.zip) on the
next run. The receipts keep their hashes, sizes and URLs, so the archives are the originals.
"""

import argparse
import hashlib
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings
from cagr_data.core.storage import LocalStorage, Storage, StorageConflictError
from cagr_data.raw.receipts import VENDOR_FOLDER

SETTLE = timedelta(days=7)


@dataclass
class ArchiveResult:
    months: int = 0
    files: int = 0
    bytes: int = 0
    failed: list[str] = field(default_factory=list)


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _months(conn: Connection, before: date) -> list[tuple[str, str, date]]:
    """(feed_id, vendor_id, month) with loose files, for months that ended before `before`."""
    return [
        (r[0], r[1], r[2])
        for r in conn.execute(
            """
            SELECT f.feed_id, fd.vendor_id, date_trunc('month', f.partition_date)::date AS month
              FROM raw.vendor_file f JOIN raw.feed fd USING (feed_id)
             WHERE position('#' IN f.storage_uri) = 0
               AND (date_trunc('month', f.partition_date) + interval '1 month')::date <= %s
             GROUP BY 1, 2, 3 ORDER BY 3, 1
            """,
            (before,),
        ).fetchall()
    ]


def archive_month(
    conn: Connection, storage: Storage, feed_id: str, vendor_id: str, month: date,
    result: ArchiveResult, commit: Callable[[], None] | None = None,
) -> None:  # fmt: skip
    """Pack one feed's loose files of one month, commit, then delete the loose files."""
    rows = conn.execute(
        """
        SELECT file_id, storage_uri, sha256 FROM raw.vendor_file
         WHERE feed_id = %s AND date_trunc('month', partition_date) = %s
           AND position('#' IN storage_uri) = 0
         ORDER BY file_id
        """,
        (feed_id, month),
    ).fetchall()
    members: dict[str, bytes] = {}
    uris: dict[str, tuple[int, str, str]] = {}
    for file_id, uri, sha in rows:
        name = uri.rsplit("/", 1)[-1]
        try:
            content = storage.get(uri)
        except OSError as e:
            result.failed.append(f"{feed_id} {month:%Y-%m} file {file_id}: unreadable ({e})")
            continue
        if _sha(content) != sha or name in members:
            result.failed.append(f"{feed_id} {month:%Y-%m} file {file_id}: hash or name clash")
            continue
        members[name] = content
        uris[name] = (file_id, uri, sha)
    if not members:
        return

    folder = VENDOR_FOLDER.get(vendor_id, vendor_id)
    packed: dict[str, str] | None = None
    for n in range(1, 100):
        suffix = "" if n == 1 else f"@{n}"
        key = f"{folder}/{feed_id}/{month:%Y}/{month:%m}{suffix}.zip"
        try:
            packed = storage.pack(key, members)
            break
        except StorageConflictError:
            continue  # this month was packed before; use the next name
    if packed is None:
        result.failed.append(f"{feed_id} {month:%Y-%m}: no free archive name")
        return

    for name, member_uri in packed.items():
        file_id, _, sha = uris[name]
        if _sha(storage.get(member_uri)) != sha:
            conn.rollback()
            result.failed.append(f"{feed_id} {month:%Y-%m}: archive read-back failed")
            return
        conn.execute(
            "UPDATE raw.vendor_file SET storage_uri = %s WHERE file_id = %s", (member_uri, file_id)
        )
    (commit or conn.commit)()
    for name in packed:
        storage.delete(uris[name][1])
    result.months += 1
    result.files += len(packed)
    result.bytes += sum(len(c) for c in members.values())


def archive(conn: Connection, storage: Storage, today: date, *, dry_run: bool = False,
            commit: Callable[[], None] | None = None) -> ArchiveResult:  # fmt: skip
    result = ArchiveResult()
    for feed_id, vendor_id, month in _months(conn, today - SETTLE):
        if dry_run:
            result.months += 1
            continue
        archive_month(conn, storage, feed_id, vendor_id, month, result, commit)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings()
    secret = settings.pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    storage = LocalStorage(Path(settings.raw_root))
    with connect(secret.get_secret_value()) as conn:
        result = archive(conn, storage, date.today(), dry_run=args.dry_run)
    verb = "would pack" if args.dry_run else "packed"
    print(f"{verb} {result.months} feed-months, {result.files} files, {result.bytes:,} bytes")
    for line in result.failed:
        print(f"  kept loose: {line}", file=sys.stderr)
    return 1 if result.failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
