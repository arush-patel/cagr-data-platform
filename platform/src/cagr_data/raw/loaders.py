"""Bulk loads of parsed rows into the raw row tables, with COPY."""

from collections.abc import Iterator
from typing import Any

import polars as pl
from psycopg import sql

from cagr_data.core.db import Connection

ROW_TABLES = frozenset(
    {
        "raw.nse_cm_bar",
        "raw.nse_index_close",
        "raw.nse_holiday",
        "raw.nse_equity_list",
        "raw.nse_symbol_change",
        "raw.nse_etf_list",
        "raw.nse_corporate_action",
    }
)
PARTITIONED = frozenset({"raw.nse_cm_bar"})


def _table(name: str) -> sql.Identifier:
    if name not in ROW_TABLES:
        raise ValueError(f"not a raw row table: {name}")
    schema, table = name.split(".")
    return sql.Identifier(schema, table)


def _rows(file_id: int, frame: pl.DataFrame) -> Iterator[tuple[Any, ...]]:
    for row in frame.iter_rows():
        yield (file_id, *row)


def load_rows(conn: Connection, table: str, file_id: int, frame: pl.DataFrame) -> int:
    """COPY the frame's rows into table, each pointing at file_id. Column names are the frame's.

    Load a file's rows once, in the transaction that wrote its receipt. A second load of the
    same file fails on the primary key rather than duplicating rows.
    """
    columns = sql.SQL(", ").join(sql.Identifier(c) for c in ("file_id", *frame.columns))
    statement = sql.SQL("COPY {} ({}) FROM STDIN").format(_table(table), columns)
    with conn.cursor() as cur, cur.copy(statement) as copy:
        for row in _rows(file_id, frame):
            copy.write_row(row)
    return frame.height


def analyze(conn: Connection, table: str) -> None:
    """Refresh planner statistics; needed after bulk loads into a partitioned table, which
    autovacuum never analyzes."""
    conn.execute(sql.SQL("ANALYZE {}").format(_table(table)))
