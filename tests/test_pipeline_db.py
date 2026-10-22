"""End to end on the Docker Postgres: empty database → calendar → listings → actions → bars → checks → lake."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
import pytest

from cagr_data import pipeline
from cagr_data import sample as fx
from cagr_data.clean import bars
from cagr_data.core import migrate
from cagr_data.quality.checks import DataQualityError

pytestmark = pytest.mark.db

Conn = psycopg.Connection[dict[str, Any]]


def _one(conn: Conn, sql: str, *params: Any) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else next(iter(row.values()))


def _listing(conn: Conn, isin: str) -> int:
    return int(
        _one(
            conn,
            "SELECT l.listing_id FROM ref.instrument_identifier ii JOIN ref.listing l USING (instrument_id) "
            "WHERE ii.value = %s",
            isin,
        )
    )


def _bootstrap(conn: Conn, files: Path, lake: Path) -> pipeline.EodResult:
    return pipeline.backfill(
        conn,
        pipeline.Fetchers.offline(files),
        fx.START,
        fx.END,
        action_window_days=60,
        lake_root=lake,
        master_as_of=fx.END,
    )


def test_full_pipeline(fresh_db: Conn, nse_files: Path, tmp_path: Path) -> None:
    conn = fresh_db
    res = _bootstrap(conn, nse_files, tmp_path / "lake")

    # calendar: holidays out, the proven Saturday in
    days = {r["session_date"] for r in conn.execute("SELECT session_date FROM ref.calendar_session")}
    assert date(2026, 10, 2) not in days and date(2026, 10, 20) not in days
    assert date(2026, 10, 10) in days
    assert _one(conn, "SELECT is_special FROM ref.calendar_session WHERE session_date = '2026-10-10'") is True
    assert res.missing == []
    assert len(res.sessions) == len(fx.sessions())

    # listings: five from the first file, NEWCO from its first session, bonds ignored
    assert _one(conn, "SELECT count(*) FROM ref.listing") == 6
    newco = _listing(conn, fx.NEWCO.isin)
    assert _one(conn, "SELECT listed_on FROM ref.listing WHERE listing_id = %s", newco) == fx.NEWCO_FROM
    assert _one(conn, "SELECT count(*) FROM ref.listing_change WHERE status <> 'applied'") == 0

    # a rename is a new ticker row, never new prices
    xyz = _listing(conn, "INE999Z01010")
    tickers = conn.execute(
        "SELECT ticker, valid_from, valid_to FROM ref.listing_symbol WHERE listing_id = %s ORDER BY valid_from",
        (xyz,),
    ).fetchall()
    assert [t["ticker"] for t in tickers] == ["XYZOLD", "XYZNEW"]
    assert tickers[0]["valid_to"] == fx.RENAME_ON == tickers[1]["valid_from"]
    assert _one(conn, "SELECT count(DISTINCT bar_date) FROM clean.bar_1d WHERE listing_id = %s", xyz) == len(
        fx.sessions()
    )

    # the split: confirmed by the ex-date price move, applied once, history restated
    abc = _listing(conn, "INE123A01016")
    ca = conn.execute(
        "SELECT * FROM clean.corporate_action WHERE listing_id = %s AND kind = 'split'", (abc,)
    ).fetchone()
    assert ca is not None and ca["status"] == "applied" and ca["price_factor"] == pytest.approx(0.2)
    before = conn.execute(
        "SELECT close, raw_close, adj_factor, volume, volume_adj, qa_flags FROM clean.bar_1d "
        "WHERE listing_id = %s AND bar_date = '2026-10-14'",
        (abc,),
    ).fetchone()
    assert before is not None
    assert before["raw_close"] > 900 and before["close"] == pytest.approx(before["raw_close"] * 0.2)
    assert before["volume_adj"] == pytest.approx(before["volume"] * 5)
    on_ex = _one(
        conn, "SELECT qa_flags FROM clean.bar_1d WHERE listing_id = %s AND bar_date = %s", abc, fx.SPLIT_EX
    )
    assert on_ex & bars.QA_CORPORATE_ACTION_DAY and not on_ex & bars.QA_RETURN_OUTLIER
    # the dividend has no price effect and is applied; the AGM is not an action
    assert _one(conn, "SELECT status FROM clean.corporate_action WHERE kind = 'dividend'") == "applied"
    assert _one(conn, "SELECT count(*) FROM clean.corporate_action") == 2

    # flags and timeframes
    assert (
        _one(conn, "SELECT count(*) FROM clean.bar_1d WHERE qa_flags & %s <> 0", bars.QA_RETURN_OUTLIER) == 0
    )
    special = _one(conn, "SELECT bool_and(qa_flags & %s <> 0) FROM clean.bar_1d WHERE bar_date = %s",
                   bars.QA_SPECIAL_SESSION, fx.SPECIAL)  # fmt: skip
    assert special is True
    wk = conn.execute(
        "SELECT * FROM clean.bar_1w WHERE listing_id = %s AND bar_date = '2026-10-16'", (abc,)
    ).fetchone()
    assert wk is not None and wk["is_complete"] and wk["sessions_traded"] == 5 and wk["close"] < 250
    assert _one(conn, "SELECT count(*) FROM clean.bar_1mo WHERE bar_date = '2026-09-30'") == 5

    # every check ran and the critical ones passed
    assert res.checks and all(res.checks[c] for c in ("holiday_mismatch", "coverage_latest_session",
                                                      "prev_close_continuity", "bar_on_calendar"))  # fmt: skip
    assert _one(conn, "SELECT count(*) FROM public.run_log WHERE status <> 'success'") == 0

    # the lake has the same bars
    lake = pd.read_parquet(tmp_path / "lake" / "bars" / "1d" / "venue=XNSE" / "year=2026" / "part-0.parquet")
    assert len(lake) == _one(conn, "SELECT count(*) FROM clean.bar_1d")


def test_rerun_is_idempotent_and_revisions_are_tracked(
    fresh_db: Conn, nse_files: Path, tmp_path: Path
) -> None:
    conn = fresh_db
    _bootstrap(conn, nse_files, tmp_path / "lake")
    raw_rows = _one(conn, "SELECT count(*) FROM raw.vendor_bar")
    f = pipeline.Fetchers.offline(nse_files)

    pipeline.eod(conn, f, fx.START, fx.END, lake_root=tmp_path / "lake")
    assert _one(conn, "SELECT count(*) FROM raw.vendor_bar") == raw_rows

    # NSE re-publishes 7 Oct with a corrected TCS close
    d = date(2026, 10, 7)
    i = fx.sessions().index(d)
    prev: dict[str, float] = {}
    for k, s in enumerate(fx.sessions()[:i]):
        fx.bhavcopy_rows(s, k, prev)
    fx.write_bhavcopy(nse_files, d, i, prev, overrides={"TCS": 4321.0})
    pipeline.eod(conn, f, d, d, run_checks=False, lake_root=tmp_path / "lake")

    tcs = _listing(conn, "INE467B01029")
    assert (
        _one(
            conn,
            "SELECT max(revision) FROM raw.vendor_bar WHERE listing_id = %s AND session_date = %s",
            tcs,
            d,
        )
        == 2
    )
    assert _one(conn, "SELECT count(*) FROM raw.vendor_bar") == raw_rows + 1
    bar = conn.execute("SELECT close, qa_flags FROM clean.bar_1d WHERE listing_id = %s AND bar_date = %s",
                       (tcs, d)).fetchone()  # fmt: skip
    assert bar is not None and bar["close"] == 4321.0 and bar["qa_flags"] & bars.QA_VENDOR_REVISED
    assert _one(conn, "SELECT count(*) FROM clean.dirty_range WHERE reason = 'vendor_revision' "
                      "AND clean_rebuilt_at IS NOT NULL") == 1  # fmt: skip


def test_unannounced_adjustment_blocks_the_run(fresh_db: Conn, nse_files: Path, tmp_path: Path) -> None:
    """NSE halves INFY's previous close (an action we never heard of): the run stops and asks for review."""
    conn = fresh_db
    _bootstrap(conn, nse_files, tmp_path / "lake")
    d = date(2026, 10, 26)
    prev: dict[str, float] = {}
    for k, s in enumerate(fx.sessions()):
        fx.bhavcopy_rows(s, k, prev)
    prev["INE009A01021"] /= 2
    fx.write_bhavcopy(
        nse_files, d, len(fx.sessions()), prev, overrides={"INFY": round(prev["INE009A01021"], 2)}
    )

    with pytest.raises(DataQualityError) as err:
        pipeline.eod(conn, pipeline.Fetchers.offline(nse_files), d, d, lake_root=tmp_path / "lake")
    assert "prev_close_continuity" in str(err.value)
    infy = _listing(conn, "INE009A01021")
    det = conn.execute("SELECT * FROM clean.corporate_action WHERE listing_id = %s AND source = 'detected'",
                       (infy,)).fetchone()  # fmt: skip
    assert (
        det is not None
        and det["parse_status"] == "needs_review"
        and det["price_factor"] == pytest.approx(0.5, 1e-3)
    )
    assert (
        _one(
            conn, "SELECT status FROM public.run_log WHERE runner = 'checks' ORDER BY started_at DESC LIMIT 1"
        )
        == "failed"
    )


def test_ticker_taken_by_another_listing_waits_for_review(
    fresh_db: Conn, nse_files: Path, tmp_path: Path
) -> None:
    """A new ISIN on RELIANCE's ticker (as after a face-value split) is queued, not guessed."""
    conn = fresh_db
    _bootstrap(conn, nse_files, tmp_path / "lake")
    d = date(2026, 10, 26)
    rows = fx.bhavcopy_rows(d, 50, {})
    for r in rows:
        if r["TckrSymb"] == "RELIANCE":
            r["ISIN"] = "INE002A01026"
    p = fx.bhav_path(nse_files, d)
    p.write_bytes(fx.udiff_zip(d, rows))
    pipeline.eod(
        conn, pipeline.Fetchers.offline(nse_files), d, d, run_checks=False, lake_root=tmp_path / "lake"
    )
    ch = conn.execute("SELECT * FROM ref.listing_change WHERE status = 'proposed'").fetchone()
    assert ch is not None and ch["kind"] == "new_listing" and ch["payload"]["isin"] == "INE002A01026"
    assert "new ISIN" in ch["note"]
    rel = _listing(conn, "INE002A01018")
    assert (
        _one(conn, "SELECT is_traded FROM clean.bar_1d WHERE listing_id = %s AND bar_date = %s", rel, d)
        is False
    )


def test_changed_migration_is_refused(test_url: str, tmp_path: Path) -> None:
    migrate.reset(test_url)
    mdir = tmp_path / "m"
    mdir.mkdir()
    (mdir / "0001_a.sql").write_text("CREATE SCHEMA IF NOT EXISTS ops;")
    assert migrate.migrate(test_url, mdir) == ["0001_a.sql"]
    (mdir / "0001_a.sql").write_text("CREATE SCHEMA IF NOT EXISTS ops; SELECT 1;")
    with pytest.raises(RuntimeError, match="changed after it was applied"):
        migrate.migrate(test_url, mdir)
    migrate.reset(test_url)
