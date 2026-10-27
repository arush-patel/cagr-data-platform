from datetime import date

import psycopg
import pytest
from psycopg import errors

from cagr_data.core import migrate

pytestmark = pytest.mark.db

Conn = psycopg.Connection[tuple[object, ...]]
SHA_A = "a" * 64
SHA_B = "b" * 64


def test_all_migrations_apply_then_rerun_is_a_no_op(
    migrated_db: tuple[str, str, list[str]],
) -> None:
    url, platform_url, applied = migrated_db
    assert applied == [
        "0001_ops", "0002_ref_calendar", "0003_raw", "0004_seed_xnse", "0005_writer_maintain",
        "0006_ohlc_t0", "0007_ref_listings", "0008_reviewer", "0009_corporate_actions",
        "0010_clean", "0011_market_cap", "0012_raw_archive", "0013_industry", "0014_index_conflict",
    ]  # fmt: skip
    with psycopg.connect(url) as conn:
        owner = conn.execute(
            "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'raw'"
        ).fetchone()
    assert owner == ("cagr_platform",)
    assert migrate.apply(platform_url) == []


def test_nothing_needs_the_public_schema(db: Conn) -> None:
    can_create = db.execute(
        "SELECT has_schema_privilege('cagr_platform', 'public', 'CREATE')"
    ).fetchone()
    assert can_create == (False,)  # as on Azure; the migrations still applied
    in_public = db.execute(
        "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
        " WHERE n.nspname = 'public'"
    ).fetchone()
    assert in_public == (0,)
    yoyo = db.execute(
        "SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'yoyo'"
    ).fetchone()
    assert yoyo == ("cagr_platform",)


def test_expected_tables_exist(db: Conn) -> None:
    rows = db.execute(
        """
        SELECT table_schema || '.' || table_name
          FROM information_schema.tables
         WHERE table_schema IN ('ops', 'ref', 'raw')
           AND table_name NOT LIKE 'nse_cm_bar_2%'
        """
    ).fetchall()
    assert {r[0] for r in rows} == {
        "ops.run", "ops.dq_check", "ops.dq_result",
        "ref.venue", "ref.calendar_exception", "ref.calendar_session",
        "raw.vendor", "raw.feed", "raw.fetch_attempt", "raw.vendor_file",
        "raw.nse_cm_bar", "raw.nse_index_close", "raw.nse_holiday",
        "raw.nse_equity_list", "raw.nse_symbol_change",
        "raw.vendor_file_current", "raw.nse_cm_bar_current", "raw.nse_index_close_current",
        "ref.listing", "ref.listing_change", "ref.listing_symbol", "ref.listing_isin",
        "ref.index_alias", "ref.corporate_action", "ref.price_adjustment", "ref.listing_current",
        "raw.nse_corporate_action", "raw.nse_etf_list", "raw.nse_corporate_action_current",
        "raw.nse_mcap", "raw.nse_index_issue_cap", "raw.nse_mcap_current",
        "raw.nse_index_issue_cap_current", "ref.listing_shares",
        "ref.industry", "ref.industry_change", "ref.listing_industry", "ref.listing_tier",
    }  # fmt: skip


def test_no_extensions_needed(db: Conn) -> None:
    rows = db.execute("SELECT extname FROM pg_extension WHERE extname <> 'plpgsql'").fetchall()
    assert rows == []


def test_seeds(db: Conn) -> None:
    assert db.execute("SELECT count(*) FROM raw.feed").fetchone() == (12,)
    assert db.execute("SELECT count(*) FROM ops.dq_check").fetchone() == (34,)
    venue = db.execute("SELECT time_zone, weekend_days FROM ref.venue").fetchall()
    assert venue == [("Asia/Kolkata", [6, 7])]
    bhavcopy = db.execute(
        "SELECT feed_id, available_from, available_to FROM raw.feed"
        " WHERE dataset = 'nse_cm_bhavcopy' ORDER BY priority"
    ).fetchall()
    assert bhavcopy == [
        ("nse_cm_bhavcopy_legacy", date(2010, 1, 1), date(2024, 7, 5)),
        ("nse_cm_bhavcopy_udiff", date(2024, 1, 1), None),
    ]


def test_bar_partitions_cover_2010_to_2030(db: Conn) -> None:
    rows = db.execute(
        "SELECT count(*) FROM pg_inherits WHERE inhparent = 'raw.nse_cm_bar'::regclass"
    ).fetchone()
    assert rows == (21,)
    file_id = _file(db, "nse_cm_bhavcopy_udiff", date(2031, 1, 2), SHA_A)
    with pytest.raises(errors.CheckViolation), db.transaction():
        _bar(db, file_id, date(2031, 1, 2))


def _file(
    db: Conn, feed: str, day: date, sha: str, revision: int = 1, supersedes: int | None = None
) -> int:
    row = db.execute(
        """
        INSERT INTO raw.vendor_file (feed_id, partition_date, url, sha256, bytes, storage_uri,
                                     revision, supersedes_file_id, row_count)
        VALUES (%s, %s, 'https://example/x', %s, 100, 'file:///x', %s, %s, 1)
        RETURNING file_id
        """,
        (feed, day, sha, revision, supersedes),
    ).fetchone()
    assert row is not None
    return int(str(row[0]))


def _bar(db: Conn, file_id: int, day: date, symbol: str = "RELIANCE") -> None:
    db.execute(
        """
        INSERT INTO raw.nse_cm_bar (file_id, trade_date, symbol, series, isin, close, volume,
                                    source_format)
        VALUES (%s, %s, %s, 'EQ', 'INE002A01018', 2950.5, 1000, 'udiff_v1')
        """,
        (file_id, day, symbol),
    )


def test_writer_can_only_append_to_raw(db: Conn) -> None:
    db.execute("SET ROLE cagr_writer")
    file_id = _file(db, "nse_cm_bhavcopy_udiff", date(2024, 7, 8), SHA_A)
    _bar(db, file_id, date(2024, 7, 8))
    for statement in (
        "UPDATE raw.nse_cm_bar SET close = 1",
        "DELETE FROM raw.nse_cm_bar",
        "TRUNCATE raw.nse_cm_bar",
        "UPDATE raw.vendor_file SET bytes = 1",
        "DELETE FROM raw.vendor_file",
        "UPDATE raw.feed SET active = false",
    ):
        with pytest.raises(errors.InsufficientPrivilege), db.transaction():
            db.execute(statement)


def test_writer_records_runs_and_results(db: Conn) -> None:
    db.execute("SET ROLE cagr_writer")
    row = db.execute("INSERT INTO ops.run (kind) VALUES ('manual') RETURNING run_id").fetchone()
    assert row is not None
    db.execute(
        "INSERT INTO ops.dq_result (run_id, check_id, passed) VALUES (%s, 'file_shape', true)",
        (row[0],),
    )
    db.execute(
        "UPDATE ops.run SET status = 'succeeded', finished_at = now() WHERE run_id = %s", (row[0],)
    )
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("UPDATE ops.dq_check SET active = false")


def test_reader_cannot_write(db: Conn) -> None:
    db.execute("SET ROLE cagr_read")
    assert db.execute("SELECT count(*) FROM raw.feed").fetchone() == (12,)
    with pytest.raises(errors.InsufficientPrivilege), db.transaction():
        db.execute("INSERT INTO ops.run (kind) VALUES ('manual')")


def test_current_view_prefers_feed_priority_then_latest_revision(db: Conn) -> None:
    overlap, later = date(2024, 7, 5), date(2024, 7, 8)
    legacy = _file(db, "nse_cm_bhavcopy_legacy", overlap, SHA_A)
    udiff_overlap = _file(db, "nse_cm_bhavcopy_udiff", overlap, SHA_A)
    first = _file(db, "nse_cm_bhavcopy_udiff", later, SHA_A)
    revised = _file(db, "nse_cm_bhavcopy_udiff", later, SHA_B, revision=2, supersedes=first)
    for file_id, day in (
        (legacy, overlap),
        (udiff_overlap, overlap),
        (first, later),
        (revised, later),
    ):
        _bar(db, file_id, day)
    rows = db.execute(
        "SELECT trade_date, file_id FROM raw.nse_cm_bar_current ORDER BY trade_date"
    ).fetchall()
    assert rows == [(overlap, legacy), (later, revised)]


def test_revision_must_point_at_what_it_supersedes(db: Conn) -> None:
    with pytest.raises(errors.CheckViolation), db.transaction():
        _file(db, "nse_cm_bhavcopy_udiff", date(2024, 7, 8), SHA_A, revision=2)
    _file(db, "nse_cm_bhavcopy_udiff", date(2024, 7, 8), SHA_A)
    with pytest.raises(errors.UniqueViolation), db.transaction():
        _file(db, "nse_cm_bhavcopy_udiff", date(2024, 7, 8), SHA_A)


def test_calendar_rules(db: Conn) -> None:
    db.execute(
        "INSERT INTO ref.calendar_exception (venue_id, date, kind, source) VALUES"
        " ('XNSE', '2026-11-08', 'holiday', 'holiday_api'),"
        " ('XNSE', '2026-11-08', 'special_session', 'archive_proof')"
    )
    db.execute(
        "INSERT INTO ref.calendar_session (venue_id, date, is_session, session_index, reason)"
        " VALUES ('XNSE', '2026-11-08', true, 1, 'special_session')"
    )
    for values in (
        "('XNSE', '2026-11-09', true, NULL, 'regular')",
        "('XNSE', '2026-11-10', false, 2, 'holiday')",
        "('XNSE', '2026-11-11', true, 3, 'holiday')",
    ):
        with pytest.raises(errors.CheckViolation), db.transaction():
            db.execute(
                "INSERT INTO ref.calendar_session (venue_id, date, is_session, session_index,"
                " reason) VALUES " + values
            )


def test_ohlc_order_lets_t0_borrow_its_close(migrated_db: tuple[str, str, list[str]]) -> None:
    with psycopg.connect(migrated_db[0]) as conn:
        params = conn.execute(
            "SELECT params FROM ops.dq_check WHERE check_id = 'ohlc_order'"
        ).fetchone()
    assert params == ({"close_exempt_series": ["T0"]},)
