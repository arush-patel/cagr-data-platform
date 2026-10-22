"""Nightly export of clean bars and reference data to the parquet lake, read by research with DuckDB.

  bars/<tf>/venue=<code>/year=<yyyy>/part-0.parquet   rewritten for each year a run touched
  reference/listings/venue=<code>/listings.parquet     listing, ISIN and ticker history

Files are written to a temporary name and renamed, so a reader never sees half a file.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

BAR_COLUMNS = {
    "1d": "bar_date, listing_id, open, high, low, close, volume, volume_adj, quote_volume, raw_close, adj_factor, "
    "is_traded, qa_flags",
    "1w": "bar_date, listing_id, period_start, open, high, low, close, volume, sessions_traded, is_complete",
    "1mo": "bar_date, listing_id, period_start, open, high, low, close, volume, sessions_traded, is_complete",
}


def _write(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_pandas(df, preserve_index=False), tmp, compression="zstd")
    tmp.replace(path)


def _frame(conn: psycopg.Connection[Any], sql: str, params: dict[str, Any]) -> pd.DataFrame:
    cur = conn.execute(sql, params)
    cols = [d.name for d in cur.description or []]
    return pd.DataFrame(cur.fetchall(), columns=cols)


def export_bars(
    conn: psycopg.Connection[Any], lake_root: Path, venue_id: int, years: list[int]
) -> list[Path]:
    venue = conn.execute("SELECT code FROM ref.venue WHERE venue_id = %s", (venue_id,)).fetchone()
    assert venue is not None
    written: list[Path] = []
    for tf, cols in BAR_COLUMNS.items():
        for y in sorted(set(years)):
            df = _frame(
                conn,
                f"SELECT {cols} FROM clean.bar_{tf} b JOIN ref.listing l USING (listing_id) "
                "WHERE l.venue_id = %(venue)s AND b.bar_date >= %(lo)s AND b.bar_date < %(hi)s "
                "ORDER BY b.listing_id, b.bar_date",
                {"venue": venue_id, "lo": date(y, 1, 1), "hi": date(y + 1, 1, 1)},
            )
            if df.empty:
                continue
            path = lake_root / "bars" / tf / f"venue={venue['code']}" / f"year={y}" / "part-0.parquet"
            _write(df, path)
            written.append(path)
    return written


def export_reference(conn: psycopg.Connection[Any], lake_root: Path, venue_id: int) -> Path:
    venue = conn.execute("SELECT code FROM ref.venue WHERE venue_id = %s", (venue_id,)).fetchone()
    assert venue is not None
    df = _frame(
        conn,
        "SELECT l.listing_id, i.instrument_id, i.asset_class, i.name, l.listed_on, l.delisted_on, l.status, "
        "s.ticker, s.valid_from AS ticker_from, s.valid_to AS ticker_to, "
        "(SELECT ii.value FROM ref.instrument_identifier ii WHERE ii.instrument_id = i.instrument_id "
        " AND ii.id_type = 'isin' AND ii.valid_to IS NULL) AS isin "
        "FROM ref.listing l JOIN ref.instrument i USING (instrument_id) "
        "JOIN ref.listing_symbol s ON s.listing_id = l.listing_id "
        "WHERE l.venue_id = %(venue)s ORDER BY l.listing_id, s.valid_from",
        {"venue": venue_id},
    )
    path = lake_root / "reference" / "listings" / f"venue={venue['code']}" / "listings.parquet"
    _write(df, path)
    return path
