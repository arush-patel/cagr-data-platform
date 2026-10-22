"""Small samples of the real NSE archive (Jul–Aug 2024), trimmed to a few listings per file.

They pin down what the first real backfill found and the synthetic files had wrong:
  * the holiday list is per year (`year=`), not just the current one
  * NSE's previous close on an ex-date is NOT adjusted; a split is confirmed by the price move
  * a face-value split comes with a new ISIN on the same ticker (KSB 25 Jul 2024, legacy and UDiFF)
  * a split and a bonus can share an ex-date (VERTOZ 5 Jul 2024)
  * SME actions are a separate list whose `isin` field is a scrip code (CELLECOR 9 Aug 2024)
  * rights entitlements (MITCON-RE…) trade in the BE/ST series and are not shares

tests/data/nse_2024 is laid out like the download cache, so the pipeline replays it offline.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from cagr_data import pipeline
from cagr_data.reference.ca_parser import parse_subject
from cagr_data.reference.corporate_actions import move_matches_factor
from cagr_data.settings import get_settings
from cagr_data.vendors import nse_api, nse_archives
from cagr_data.vendors.http import DirectoryFetcher

DATA = Path(__file__).parent / "data" / "nse_2024"
Conn = psycopg.Connection[dict[str, Any]]


def _bhav(d: date) -> nse_archives.pd.DataFrame:
    bf, fetched = nse_archives.fetch_bhavcopy(DirectoryFetcher(DATA / "nse_archives"), d)
    assert fetched.content is not None
    return nse_archives.parse_bhavcopy(fetched.content, bf.fmt, expected_date=d)


@pytest.mark.parametrize("d", [date(2024, 7, 5), date(2024, 7, 25)])  # legacy, then UDiFF
def test_real_files_parse_to_shares_only(d: date) -> None:
    eq = nse_archives.equity_rows(_bhav(d), get_settings().equity_series)
    assert {"RELIANCE", "TCS", "KSB", "VERTOZ", "BPCL"} <= set(eq["symbol"])
    assert not eq["symbol"].str.contains("-RE").any()  # rights entitlements
    assert not eq["symbol"].str.contains("GS").any()  # government bonds
    assert eq["isin"].str.match(r"^IN[EF]").all()


def test_previous_close_on_the_ex_date_is_not_adjusted() -> None:
    before, on_ex = _bhav(date(2024, 7, 24)), _bhav(date(2024, 7, 25))
    ksb_before = before[before["symbol"] == "KSB"].iloc[0]
    ksb_ex = on_ex[on_ex["symbol"] == "KSB"].iloc[0]
    assert ksb_ex["isin"] != ksb_before["isin"] and ksb_ex["isin"][:9] == ksb_before["isin"][:9]
    assert ksb_ex["prev_close"] == ksb_before["close"]  # 4991.30, not 998.26
    assert move_matches_factor(ksb_ex["close"], ksb_before["close"], 0.2)
    assert not move_matches_factor(ksb_before["close"], ksb_before["prev_close"], 0.2)
    # BPCL's 1:1 bonus on 21 Jun 2024: 626.25 → 307.60
    assert move_matches_factor(307.6, 626.25, 0.5) and not move_matches_factor(620.0, 626.25, 0.5)


def test_holiday_list_is_per_year() -> None:
    url, key = nse_api.holidays_url(2024, today=date(2026, 10, 22))
    assert url.endswith("year=2024") and key == "holiday-master/trading-2024.json"
    assert (
        nse_api.holidays_url(2026, today=date(2026, 10, 22))[1] == "holiday-master/trading-2026-20261022.json"
    )
    hol = nse_api.parse_holidays(json.loads((DATA / "nse_api" / key).read_text()))
    assert {date(2024, 6, 17), date(2024, 7, 17), date(2024, 8, 15)} <= set(hol["holiday_date"])
    assert nse_api.parse_holidays({}).empty  # years before 2011 and next year's list come back as {}


def test_sme_actions_have_no_isin() -> None:
    payload = json.loads((DATA / "nse_api/corporate-actions/sme-20240808-20240809.json").read_text())
    acts = nse_api.parse_corporate_actions(payload)
    cell = acts[acts["symbol"] == "CELLECOR"].iloc[0]
    assert cell["isin"] == ""  # '418962' in the payload
    assert [(a.kind, a.price_factor) for a in parse_subject(cell["subject"])] == [("split", 0.1)]
    assert [a.kind for a in parse_subject("Buy Back")] == ["buyback"]
    assert parse_subject("Annual General Meeting /Dividend - 1 Rs Per Share")[0].cash_amount == 1.0


def _one(conn: Conn, sql: str, *params: Any) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else next(iter(row.values()))


def _listing(conn: Conn, ticker: str) -> int:
    return int(
        _one(conn, "SELECT listing_id FROM ref.listing_symbol WHERE ticker = %s AND valid_to IS NULL", ticker)
    )


def _backfill(conn: Conn, start: date, end: date, lake: Path) -> pipeline.EodResult:
    return pipeline.backfill(
        conn, pipeline.Fetchers.offline(DATA), start, end, lake_root=lake, master_as_of=end
    )


def _common(conn: Conn, end: date) -> None:
    assert _one(conn, "SELECT count(*) FROM ref.listing_change WHERE status IN ('proposed','approved')") == 0
    assert _one(conn, "SELECT count(*) FROM ref.listing_symbol WHERE ticker LIKE '%%-RE%%'") == 0
    assert _one(conn, "SELECT count(*) FROM clean.corporate_action WHERE status = 'proposed' AND ex_date <= %s",
                end) == 0  # fmt: skip
    assert _one(conn, "SELECT count(*) FROM public.run_log WHERE status <> 'success'") == 0


@pytest.mark.db
def test_split_with_new_isin_keeps_one_history(fresh_db: Conn, tmp_path: Path) -> None:
    """KSB, 25 Jul 2024 (UDiFF): Rs 10 → Rs 2 and ISIN INE999A01015 → INE999A01023."""
    conn = fresh_db
    _backfill(conn, date(2024, 7, 24), date(2024, 7, 25), tmp_path / "lake")
    _common(conn, date(2024, 7, 25))
    ksb = _listing(conn, "KSB")
    isins = [r["value"] for r in conn.execute(
        "SELECT ii.value FROM ref.instrument_identifier ii JOIN ref.listing l USING (instrument_id) "
        "WHERE l.listing_id = %s ORDER BY ii.valid_from", (ksb,))]  # fmt: skip
    assert isins == ["INE999A01015", "INE999A01023"]
    bars = conn.execute(
        "SELECT bar_date, raw_close, close, adj_factor FROM clean.bar_1d WHERE listing_id = %s ORDER BY bar_date",
        (ksb,),
    ).fetchall()
    assert [b["bar_date"] for b in bars] == [date(2024, 7, 24), date(2024, 7, 25)]
    assert bars[0]["adj_factor"] == pytest.approx(0.2) and bars[0]["close"] == pytest.approx(998.26)
    assert abs(bars[1]["close"] / bars[0]["close"] - 1) < 0.01


@pytest.mark.db
def test_split_and_bonus_on_one_ex_date(fresh_db: Conn, tmp_path: Path) -> None:
    """VERTOZ, 5 Jul 2024 (legacy format): 1:10 split and 1:1 bonus, 686.50 → 36.05."""
    conn = fresh_db
    _backfill(conn, date(2024, 7, 4), date(2024, 7, 5), tmp_path / "lake")
    _common(conn, date(2024, 7, 5))
    v = _listing(conn, "VERTOZ")
    kinds = {r["kind"]: r["status"] for r in conn.execute(
        "SELECT kind, status FROM clean.corporate_action WHERE listing_id = %s AND ex_date = '2024-07-05'", (v,))}  # fmt: skip
    assert kinds == {"split": "applied", "bonus": "applied"}
    assert _one(conn, "SELECT adj_factor FROM clean.bar_1d WHERE listing_id = %s AND bar_date = '2024-07-04'",
                v) == pytest.approx(0.05)  # fmt: skip


@pytest.mark.db
def test_sme_split_from_the_sme_list(fresh_db: Conn, tmp_path: Path) -> None:
    """CELLECOR, 9 Aug 2024: the split is only in the SME list, and brings a new ISIN."""
    conn = fresh_db
    res = _backfill(conn, date(2024, 8, 8), date(2024, 8, 9), tmp_path / "lake")
    _common(conn, date(2024, 8, 9))
    c = _listing(conn, "CELLECOR")
    assert _one(conn, "SELECT status FROM clean.corporate_action WHERE listing_id = %s AND kind = 'split'",
                c) == "applied"  # fmt: skip
    assert _one(conn, "SELECT count(*) FROM clean.bar_1d WHERE listing_id = %s AND is_traded", c) == 2
    assert res.lake_files > 0
