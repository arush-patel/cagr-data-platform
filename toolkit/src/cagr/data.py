"""Point-in-time reads of listings, bars and market cap, as Polars frames.

    with db.connect_read() as conn:
        ids = data.lookup(conn, ["RELIANCE", "TCS"], date(2024, 6, 28))
        bars = data.bars(conn, date(2024, 1, 1), date(2024, 6, 28), listings=list(ids.values()))

Everything is keyed on the integer listing_id; symbols are looked up as of a date. Reads use
only the objects in specs 0002 (section 9), 0003 (section 5) and 0007, through cagr_read.

- Adjusted bars (the default) stop at the session before a pending adjustment's ex-date, so a
  feature never sees a false jump. `holds` lists the listings held back.
- There is no bar on a day a listing didn't trade.
- Prices and volumes come back as floats, whether adjusted or not.
"""

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Literal, cast

import polars as pl

from cagr import fs
from cagr.db import Connection, read_frame

Timeframe = Literal["1d", "1w", "1mo"]

_PRICES = ("open", "high", "low", "close", "volume")
_LISTING_TEXT = ("symbol", "isin", "name", "asset_class", "board")
_DAILY: dict[str, pl.DataType] = {
    "listing_id": pl.Int64(),
    "date": pl.Date(),
    **{c: pl.Float64() for c in _PRICES},
    "value_traded": pl.Float64(),
    "trade_count": pl.Int64(),
    "series": pl.String(),
}
_PERIOD: dict[str, pl.DataType] = {
    "listing_id": pl.Int64(),
    "date": pl.Date(),
    "period_start": pl.Date(),
    **{c: pl.Float64() for c in _PRICES},
    "value_traded": pl.Float64(),
    "trade_count": pl.Int64(),
    "session_count": pl.Int64(),
    "complete": pl.Boolean(),
    "adjustment_state": pl.Int64(),
}


def _frame(
    conn: Connection, query: str, params: Mapping[str, object], schema: Mapping[str, pl.DataType]
) -> pl.DataFrame:
    frame = read_frame(conn, query, params)
    return frame.select(pl.col(c).cast(t) for c, t in schema.items())


def listings(
    conn: Connection, as_of: date, *, asset_classes: Sequence[str] | None = None
) -> pl.DataFrame:
    """Every listing that had traded by as_of: listing_id, symbol, isin, name, asset_class, board.

    Symbols and ISINs are the ones in use on as_of. Delisted listings stay in.
    """
    query = (
        "SELECT listing_id, symbol, isin, name, asset_class, board FROM ref.listings_as_of(%(d)s)"
        " WHERE %(classes)s::text[] IS NULL OR asset_class = ANY(%(classes)s) ORDER BY listing_id"
    )
    params = {"d": as_of, "classes": list(asset_classes) if asset_classes else None}
    schema = {"listing_id": pl.Int64(), **{c: pl.String() for c in _LISTING_TEXT}}
    return _frame(conn, query, params, schema)


def lookup(conn: Connection, symbols: Sequence[str], as_of: date) -> dict[str, int]:
    """The listing_id each symbol had on as_of. Raises KeyError naming any it can't find."""
    found: dict[str, int] = {}
    for symbol in symbols:
        row = conn.execute("SELECT ref.listing_by_symbol(%s, %s)", (symbol, as_of)).fetchone()
        if row is not None and row[0] is not None:
            found[symbol] = int(row[0])
    missing = [s for s in symbols if s not in found]
    if missing:
        raise KeyError(f"no listing had these symbols on {as_of}: {', '.join(missing)}")
    return found


def classification(conn: Connection, as_of: date) -> pl.DataFrame:
    """listing_id, cagr_industry, sector_index and index tier of every listing on as_of."""
    query = (
        "SELECT listing_id, cagr_industry, sector_index, tier"
        " FROM ref.listing_classification(%(d)s) ORDER BY listing_id"
    )
    schema = {"listing_id": pl.Int64(), "cagr_industry": pl.String(), "sector_index": pl.String(),
              "tier": pl.String()}  # fmt: skip
    return _frame(conn, query, {"d": as_of}, schema)


def bars(
    conn: Connection,
    start: date,
    end: date,
    *,
    listings: Sequence[int] | None = None,
    timeframe: Timeframe = "1d",
    adjusted: bool = True,
    warmup: int = 0,
) -> pl.DataFrame:
    """Bars from start to end, sorted by listing_id and date; None for listings means all.

    Weekly and monthly bars are dated by their last session (`date`) and also carry
    `period_start`, `session_count` and `complete` (false for a period still in progress).
    `warmup` adds up to that many bars before start for each listing, so an indicator has its
    history; a feature needs `min_bars - 1`.
    """
    if end < start:
        raise ValueError("end is before start")
    if warmup < 0:
        raise ValueError("warmup can't be negative")
    if timeframe == "1d":
        source = "clean.bar_1d_adjusted" if adjusted else "clean.bar_1d_unadjusted"
        cols = [f"b.{c}::float8 AS {c}" for c in _PRICES]
        select = ", ".join(["b.listing_id", "b.date", *cols, "b.value_traded::float8 AS"
                            " value_traded", "b.trade_count", "b.series"])  # fmt: skip
        schema = dict(_DAILY)
        if adjusted:
            select += ", b.adj_factor"
            schema["adj_factor"] = pl.Float64()
        day, where = "b.date", ""
    elif timeframe in ("1w", "1mo"):
        source = f"clean.bar_{timeframe}"
        prefix = "adj_" if adjusted else ""
        cols = [f"b.{prefix}{c}::float8 AS {c}" for c in _PRICES]
        select = ", ".join(["b.listing_id", "b.period_end AS date", "b.period_start", *cols,
                            "b.value_traded::float8 AS value_traded", "b.trade_count",
                            "b.session_count", "b.complete", "b.adjustment_state"])  # fmt: skip
        schema = dict(_PERIOD)
        # a period with a held daily bar has no adjusted prices
        day = "b.period_end"
        where = " AND b.adj_close IS NOT NULL" if adjusted else ""
    else:
        raise ValueError(f"timeframe must be 1d, 1w or 1mo, not {timeframe!r}")
    query = f"""
        WITH ids AS (
            SELECT listing_id FROM ref.listing
             WHERE %(ids)s::int[] IS NULL OR listing_id = ANY(%(ids)s)
        )
        SELECT * FROM (
            SELECT {select} FROM {source} b JOIN ids USING (listing_id)
             WHERE {day} BETWEEN %(start)s AND %(end)s{where}
            UNION ALL
            SELECT w.* FROM ids CROSS JOIN LATERAL (
                SELECT {select} FROM {source} b
                 WHERE b.listing_id = ids.listing_id AND {day} < %(start)s{where}
                 ORDER BY {day} DESC LIMIT %(warmup)s
            ) w
        ) bars
        ORDER BY listing_id, date
    """  # only the fixed names above are interpolated, never input
    params = {"ids": list(listings) if listings is not None else None, "start": start,
              "end": end, "warmup": warmup}  # fmt: skip
    return _frame(conn, query, params, schema)


def market_cap(
    conn: Connection, start: date, end: date, *, listings: Sequence[int] | None = None
) -> pl.DataFrame:
    """Daily market cap: listing_id, date, shares, close, market_cap_inr, source.

    The unadjusted close times the shares outstanding that day, so it needs no adjustment.
    """
    query = (
        "SELECT listing_id, date, shares, close::float8 AS close,"
        " market_cap_inr::float8 AS market_cap_inr, source FROM clean.market_cap_1d"
        " WHERE date BETWEEN %(start)s AND %(end)s"
        " AND (%(ids)s::int[] IS NULL OR listing_id = ANY(%(ids)s))"
        " ORDER BY listing_id, date"
    )
    params = {"ids": list(listings) if listings is not None else None, "start": start, "end": end}
    schema = {"listing_id": pl.Int64(), "date": pl.Date(), "shares": pl.Int64(),
              "close": pl.Float64(), "market_cap_inr": pl.Float64(),
              "source": pl.String()}  # fmt: skip
    return _frame(conn, query, params, schema)


def holds(conn: Connection) -> pl.DataFrame:
    """Listings whose adjusted bars are held back: listing_id, held_from, adjustment_id."""
    query = "SELECT listing_id, held_from, adjustment_id FROM clean.adjustment_hold ORDER BY 1"
    schema = {"listing_id": pl.Int64(), "held_from": pl.Date(), "adjustment_id": pl.Int64()}
    return _frame(conn, query, {}, schema)


def adjustment_state(conn: Connection) -> int:
    """The highest applied adjustment id (0 if none): record it with a dataset or run.

    Applied factors never change, so the same number means the same adjusted history.
    """
    row = conn.execute(
        "SELECT coalesce(max(adjustment_id), 0) FROM ref.price_adjustment WHERE status = 'applied'"
    ).fetchone()
    return int(row[0]) if row else 0


def preview(
    f: fs.Feature,
    conn: Connection,
    symbols: Sequence[str],
    start: date,
    end: date,
) -> pl.DataFrame:
    """A feature's values for a few symbols: listing_id, symbol, date and the value.

    Reads just enough history before start for the warm-up, and stores nothing. Symbols are
    looked up as of end.
    """
    if f.timeframe not in ("1d", "1w", "1mo"):
        raise ValueError(f"{f.key} has timeframe {f.timeframe!r}; bars are 1d, 1w or 1mo")
    ids = lookup(conn, symbols, end)
    timeframe = cast(Timeframe, f.timeframe)
    history = bars(
        conn, start, end, listings=list(ids.values()), timeframe=timeframe,
        warmup=f.min_bars - 1,
    )  # fmt: skip
    names = pl.DataFrame(
        {"listing_id": list(ids.values()), "symbol": list(ids)},
        schema={"listing_id": pl.Int64(), "symbol": pl.String()},
    )
    values = fs.compute(f, history).filter(pl.col("date") >= start)
    return values.join(names, on="listing_id").select("listing_id", "symbol", "date", f.key)
