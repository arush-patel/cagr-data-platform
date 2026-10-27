import io
import zipfile
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import unquote

import psycopg
import pytest

from cagr_data.core.runs import finish_run, start_run
from cagr_data.core.storage import LocalStorage
from cagr_data.raw.feeds import FORMATS, load_feed
from cagr_data.raw.ingest import check_arrived, ingest_file
from cagr_data.raw.receipts import record_attempt

pytestmark = pytest.mark.db

Conn = psycopg.Connection[tuple[object, ...]]
UDIFF = "BhavCopy_NSE_CM_0_0_0_20240708_F_0000.csv.zip"
UDIFF_DAY = date(2024, 7, 8)
UDIFF_URL = "https://nsearchives.nseindia.com/content/cm/" + UDIFF
INDEX = "ind_close_all_23102026.csv"
INDEX_URL = "https://nsearchives.nseindia.com/content/indices/" + INDEX


@pytest.fixture
def writer(db: Conn) -> Iterator[Conn]:
    """The admin connection acting as cagr_writer, as the pipeline will."""
    db.execute("SET ROLE cagr_writer")
    yield db


@pytest.fixture
def storage(tmp_path: Path) -> LocalStorage:
    return LocalStorage(tmp_path / "raw")


def _results(conn: Conn, run_id: int) -> dict[str, bool]:
    rows = conn.execute(
        "SELECT check_id, passed FROM ops.dq_result WHERE run_id = %s", (run_id,)
    ).fetchall()
    return {str(c): bool(p) for c, p in rows}


def _rezip(content: bytes, old: bytes, new: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        (name,) = zf.namelist()
        csv = zf.read(name)
    assert old in csv
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(name, csv.replace(old, new, 1))
    return out.getvalue()


def test_every_seeded_format_has_a_parser(db: Conn) -> None:
    formats = {r[0] for r in db.execute("SELECT format FROM raw.feed").fetchall()}
    assert formats == set(FORMATS)


def test_runs_and_attempts(writer: Conn) -> None:
    run_id = start_run(writer, "manual", code_version="abc123")
    record_attempt(writer, run_id=run_id, feed_id="nse_index_close", day=UDIFF_DAY,
                   url=INDEX_URL, http_status=404, elapsed_ms=120)  # fmt: skip
    finish_run(writer, run_id, succeeded=True)
    status = writer.execute("SELECT status FROM ops.run WHERE run_id = %s", (run_id,)).fetchone()
    assert status == ("succeeded",)


def test_ingest_then_rerun_is_a_no_op(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    run_id = start_run(writer, "manual")
    feed = load_feed(writer, "nse_cm_bhavcopy_udiff")
    content = (nse_fixtures / UDIFF).read_bytes()
    first = ingest_file(writer, storage, run_id=run_id, feed=feed, day=UDIFF_DAY,
                        url=UDIFF_URL, content=content)  # fmt: skip
    assert first.receipt is not None and first.receipt.is_new and first.receipt.revision == 1
    assert first.rows_loaded > 0 and first.critical == []
    assert first.receipt.storage_uri.endswith("raw/nse/nse_cm_bhavcopy_udiff/2024/07/" + UDIFF)
    assert storage.get(first.receipt.storage_uri) == content
    bars = writer.execute(
        "SELECT count(*) FROM raw.nse_cm_bar_current WHERE trade_date = %s", (UDIFF_DAY,)
    ).fetchone()
    assert bars == (first.rows_loaded,)
    assert _results(writer, run_id) == {
        "file_shape": True, "trade_date_matches": True, "no_duplicates": True,
        "prices_positive": True, "ohlc_order": True, "isin_present": True, "row_count": True,
        "holiday_session": True, "special_session": True, "vendor_revision": True,
    }  # fmt: skip

    again = ingest_file(writer, storage, run_id=run_id, feed=feed, day=UDIFF_DAY,
                        url=UDIFF_URL, content=content)  # fmt: skip
    assert again.receipt is not None and not again.receipt.is_new
    assert again.receipt.file_id == first.receipt.file_id
    assert again.rows_loaded == 0
    files = writer.execute("SELECT count(*) FROM raw.vendor_file").fetchone()
    assert files == (1,)


def test_new_bytes_become_a_revision(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    run_id = start_run(writer, "revision_check")
    feed = load_feed(writer, "nse_cm_bhavcopy_udiff")
    content = (nse_fixtures / UDIFF).read_bytes()
    first = ingest_file(writer, storage, run_id=run_id, feed=feed, day=UDIFF_DAY,
                        url=UDIFF_URL, content=content)  # fmt: skip
    revised = _rezip(content, b"3201.8", b"3201.9")
    second = ingest_file(writer, storage, run_id=run_id, feed=feed, day=UDIFF_DAY,
                         url=UDIFF_URL, content=revised)  # fmt: skip
    assert first.receipt is not None and second.receipt is not None
    assert second.receipt.revision == 2
    assert second.receipt.supersedes_file_id == first.receipt.file_id
    assert "@" + second.receipt.sha256[:12] in unquote(second.receipt.storage_uri)
    assert storage.get(first.receipt.storage_uri) == content  # never overwritten
    close = writer.execute(
        "SELECT close::text FROM raw.nse_cm_bar_current WHERE trade_date = %s"
        " AND symbol = 'RELIANCE' AND series = 'EQ'",
        (UDIFF_DAY,),
    ).fetchall()
    assert close == [("3201.9000",)]
    revision = writer.execute(
        "SELECT passed FROM ops.dq_result WHERE check_id = 'vendor_revision' ORDER BY result_id"
    ).fetchall()
    assert revision == [(True,), (False,)]
    assert second.critical == []  # a revision is a warning


def test_wrong_date_writes_no_receipt_but_says_why(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    run_id = start_run(writer, "manual")
    feed = load_feed(writer, "nse_cm_bhavcopy_udiff")
    result = ingest_file(writer, storage, run_id=run_id, feed=feed,
                         day=UDIFF_DAY + timedelta(1), url=UDIFF_URL,
                         content=(nse_fixtures / UDIFF).read_bytes())  # fmt: skip
    assert result.receipt is None
    assert [o.check_id for o in result.critical] == ["trade_date_matches"]
    assert _results(writer, run_id) == {"file_shape": True, "trade_date_matches": False}
    assert writer.execute("SELECT count(*) FROM raw.vendor_file").fetchone() == (0,)


def test_row_count_outside_the_band_is_critical(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    run_id = start_run(writer, "manual")
    for i in range(20):
        day = UDIFF_DAY - timedelta(days=i + 1)
        writer.execute(
            "INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes,"
            " storage_uri, run_id, row_count) VALUES ('nse_cm_bhavcopy_udiff', %s, 'u', %s,"
            " 1, 'file:///x', %s, 3000)",
            (day, f"{i:064x}", run_id),
        )
    feed = load_feed(writer, "nse_cm_bhavcopy_udiff")
    result = ingest_file(writer, storage, run_id=run_id, feed=feed, day=UDIFF_DAY,
                         url=UDIFF_URL, content=(nse_fixtures / UDIFF).read_bytes())  # fmt: skip
    assert [o.check_id for o in result.critical] == ["row_count"]
    assert result.critical[0].observed["median"] == 3000
    assert result.rows_loaded > 0  # raw keeps what NSE sent; the run fails


def test_index_close_on_a_weekend_is_a_special_session(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    run_id = start_run(writer, "manual")
    feed = load_feed(writer, "nse_index_close")
    saturday = date(2026, 10, 24)
    content = (nse_fixtures / INDEX).read_bytes().replace(b"23-10-2026", b"24-10-2026")
    result = ingest_file(writer, storage, run_id=run_id, feed=feed, day=saturday,
                         url=INDEX_URL, content=content)  # fmt: skip
    assert result.critical == []
    results = _results(writer, run_id)
    assert results["benchmark_present"] and results["no_duplicates"]
    assert results["special_session"] is False
    exception = writer.execute(
        "SELECT kind, source FROM ref.calendar_exception WHERE date = %s", (saturday,)
    ).fetchall()
    assert exception == [("special_session", "archive_proof")]


def test_file_on_a_listed_holiday_warns(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    day = date(2026, 10, 23)
    writer.execute(
        "INSERT INTO ref.calendar_exception (venue_id, date, kind, source)"
        " VALUES ('XNSE', %s, 'holiday', 'holiday_api')",
        (day,),
    )
    run_id = start_run(writer, "manual")
    feed = load_feed(writer, "nse_index_close")
    result = ingest_file(writer, storage, run_id=run_id, feed=feed, day=day, url=INDEX_URL,
                         content=(nse_fixtures / INDEX).read_bytes())  # fmt: skip
    assert result.critical == []
    assert _results(writer, run_id)["holiday_session"] is False
    kinds = writer.execute(
        "SELECT kind FROM ref.calendar_exception WHERE date = %s ORDER BY kind", (day,)
    ).fetchall()
    assert kinds == [("holiday",), ("special_session",)]


def test_holiday_list_fills_the_calendar_exceptions(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    run_id = start_run(writer, "manual")
    feed = load_feed(writer, "nse_holidays")
    result = ingest_file(
        writer, storage, run_id=run_id, feed=feed, day=date(2026, 1, 1),
        url="https://www.nseindia.com/api/holiday-master?type=trading&year=2026",
        content=(nse_fixtures / "holiday-master_trading_2026.json").read_bytes(),
    )  # fmt: skip
    assert result.rows_loaded == 239
    assert result.receipt is not None
    assert result.receipt.storage_uri.endswith(
        "nse/nse_holidays/2026/01/holiday-master_trading_2026.json"
    )
    exceptions = writer.execute(
        "SELECT count(*), min(source_file_id) FROM ref.calendar_exception WHERE kind = 'holiday'"
    ).fetchone()
    assert exceptions == (20, result.receipt.file_id)


def test_snapshots_load(writer: Conn, storage: LocalStorage, nse_fixtures: Path) -> None:
    run_id = start_run(writer, "manual")
    day = date(2026, 10, 27)
    loaded = {}
    for feed_id, name, url in (
        ("nse_equity_list", "EQUITY_L.csv",
         "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"),
        ("nse_sme_equity_list", "SME_EQUITY_L.csv",
         "https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv"),
        ("nse_symbol_change", "symbolchange.csv",
         "https://nsearchives.nseindia.com/content/equities/symbolchange.csv"),
    ):  # fmt: skip
        content = (nse_fixtures / name).read_bytes()
        result = ingest_file(writer, storage, run_id=run_id, feed=load_feed(writer, feed_id),
                             day=day, url=url, content=content)  # fmt: skip
        assert result.receipt is not None and result.critical == []
        assert result.receipt.storage_uri.endswith(name.replace(".csv", "_20261027.csv"))
        loaded[feed_id] = result.rows_loaded
    assert loaded == {"nse_equity_list": 17, "nse_sme_equity_list": 16, "nse_symbol_change": 18}
    boards = writer.execute(
        "SELECT board, count(*) FROM raw.nse_equity_list GROUP BY board ORDER BY board"
    ).fetchall()
    assert boards == [("main", 17), ("sme", 16)]


def test_missing_index_file_after_the_deadline(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    run_id = start_run(writer, "nightly")
    feed = load_feed(writer, "nse_cm_bhavcopy_udiff")
    ingest_file(writer, storage, run_id=run_id, feed=feed, day=UDIFF_DAY, url=UDIFF_URL,
                content=(nse_fixtures / UDIFF).read_bytes())  # fmt: skip
    result = check_arrived(writer, run_id=run_id, day=UDIFF_DAY)
    assert [o.check_id for o in result.critical] == ["file_arrived"]
    assert result.critical[0].observed == {"missing": ["nse_index_close"]}


def test_writer_may_maintain_the_bar_table(db: Conn) -> None:
    version = db.execute("SHOW server_version_num").fetchone()
    if version is None or int(str(version[0])) < 170000:
        pytest.skip("MAINTAIN needs PostgreSQL 17")
    rows = db.execute(
        "SELECT c.oid::regclass::text FROM pg_class c"
        " WHERE c.relname LIKE 'nse_cm_bar%%' AND c.relkind IN ('r', 'p')"
        " AND NOT has_table_privilege('cagr_writer', c.oid, 'MAINTAIN')"
    ).fetchall()
    assert rows == []


@pytest.mark.parametrize(
    ("feed_id", "fixture", "day", "table", "rows"),
    [
        ("nse_mcap", "PR010224.zip", date(2024, 2, 1), "raw.nse_mcap_current", 6),
        ("nse_index_issue_cap", "PR040110.zip", date(2010, 1, 4),
         "raw.nse_index_issue_cap_current", 8),
    ],
)  # fmt: skip
def test_pr_zip_feeds_load_as_the_writer(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path, feed_id: str, fixture: str,
    day: date, table: str, rows: int,
) -> None:  # fmt: skip
    run_id = start_run(writer, "manual")
    feed = load_feed(writer, feed_id)
    content = (nse_fixtures / "pr" / fixture).read_bytes()
    url = "https://nsearchives.nseindia.com/archives/equities/bhavcopy/pr/" + fixture
    result = ingest_file(writer, storage, run_id=run_id, feed=feed, day=day, url=url,
                         content=content)  # fmt: skip
    assert result.critical == [] and result.rows_loaded == rows
    assert writer.execute(f"SELECT count(*) FROM {table}").fetchone() == (rows,)
    assert _results(writer, run_id) == {
        "file_shape": True, "trade_date_matches": True, "no_duplicates": True,
        "vendor_revision": True,
    }  # fmt: skip


def test_archive_packs_a_month_and_reads_back(
    writer: Conn, storage: LocalStorage, nse_fixtures: Path
) -> None:
    from cagr_data.raw.archive import archive

    run_id = start_run(writer, "manual")
    feed = load_feed(writer, "nse_cm_bhavcopy_udiff")
    content = (nse_fixtures / UDIFF).read_bytes()
    first = ingest_file(writer, storage, run_id=run_id, feed=feed, day=UDIFF_DAY,
                        url=UDIFF_URL, content=content)  # fmt: skip
    assert first.receipt is not None
    loose = first.receipt.storage_uri

    # A month too recent to have settled stays loose.
    assert archive(writer, storage, date(2024, 8, 3)).months == 0
    result = archive(writer, storage, date(2024, 9, 1), commit=lambda: None)
    assert (result.months, result.files, result.failed) == (1, 1, [])
    (uri,) = writer.execute("SELECT storage_uri FROM raw.vendor_file").fetchone() or ()
    assert str(uri).endswith("raw/nse/nse_cm_bhavcopy_udiff/2024/07.zip#" + UDIFF)
    assert storage.get(str(uri)) == content
    assert not Path(unquote(loose.removeprefix("file://"))).exists()
    assert archive(writer, storage, date(2024, 9, 1)).months == 0  # nothing loose is left
