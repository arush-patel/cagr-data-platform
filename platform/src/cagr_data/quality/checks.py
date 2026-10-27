"""Raw-layer checks as plain functions (spec 0001, section 8).

Each returns an Outcome for one check id in ops.dq_check. Thresholds come in as the check's
params, so tuning a check is a data change. Nothing here touches the database; the runner
gathers the inputs and records the outcomes.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from statistics import median
from typing import Any

import polars as pl

from cagr_data.vendors.errors import FileShapeError, TradeDateMismatchError, VendorFileError

SAMPLE = 10  # offending keys listed in observed


@dataclass(frozen=True)
class Outcome:
    check_id: str
    passed: bool
    observed: dict[str, Any] = field(default_factory=dict)
    detail: str | None = None


def _sample(frame: pl.DataFrame, keys: Sequence[str]) -> list[str]:
    return ["/".join(str(v) for v in row) for row in frame.select(keys).head(SAMPLE).iter_rows()]


def parse_outcomes(error: VendorFileError | None) -> list[Outcome]:
    """file_shape and trade_date_matches, from what parsing raised (or did not)."""
    if isinstance(error, TradeDateMismatchError):
        found = [str(d) for d in error.found]
        return [
            Outcome("file_shape", True),
            Outcome(
                "trade_date_matches",
                False,
                {"expected": str(error.expected), "found": found},
                str(error),
            ),
        ]
    if isinstance(error, FileShapeError | VendorFileError):
        return [Outcome("file_shape", False, {}, str(error))]
    return [Outcome("file_shape", True), Outcome("trade_date_matches", True)]


def index_conflict(conflicts: pl.DataFrame) -> Outcome:
    """Indices listed twice with different values; neither row was loaded."""
    names = sorted(set(conflicts["index_name"].to_list())) if conflicts.height else []
    return Outcome("index_conflict", not names, {"indices": names, "rows": conflicts.height})


def no_duplicates(frame: pl.DataFrame, keys: Sequence[str]) -> Outcome:
    dupes = frame.filter(pl.struct(list(keys)).is_duplicated()).unique(subset=list(keys))
    return Outcome(
        "no_duplicates",
        dupes.height == 0,
        {"keys": list(keys), "duplicated": dupes.height, "sample": _sample(dupes, keys)},
    )


def prices_positive(frame: pl.DataFrame) -> Outcome:
    prices = ("open", "high", "low", "close")
    bad = frame.filter(
        (pl.col("volume") > 0)
        & ~pl.all_horizontal((pl.col(c) > 0).fill_null(False) for c in prices)
    )
    return Outcome(
        "prices_positive",
        bad.height == 0,
        {"rows": bad.height, "sample": _sample(bad, ("symbol", "series"))},
    )


def ohlc_order(frame: pl.DataFrame, params: Mapping[str, Any] | None = None) -> Outcome:
    """low <= open, close <= high. In params["close_exempt_series"] (NSE's T0 segment) the close
    is the normal segment's closing price, so only open is held between low and high there."""
    o, h, lo, c = (pl.col(n) for n in ("open", "high", "low", "close"))
    exempt = pl.col("series").is_in(list((params or {}).get("close_exempt_series", [])))
    bad = frame.filter((lo > o) | (h < o) | (~exempt & ((lo > c) | (h < c))))
    return Outcome(
        "ohlc_order",
        bad.height == 0,
        {"rows": bad.height, "sample": _sample(bad, ("symbol", "series"))},
    )


def isin_present(frame: pl.DataFrame, day: date, params: Mapping[str, Any]) -> Outcome:
    start = date.fromisoformat(params["from"])
    if day < start:
        return Outcome("isin_present", True, {"skipped": f"before {start}"})
    bad = frame.filter(pl.col("series").is_in(list(params["series"])) & pl.col("isin").is_null())
    return Outcome(
        "isin_present",
        bad.height == 0,
        {"rows": bad.height, "sample": _sample(bad, ("symbol", "series"))},
    )


def benchmark_present(frame: pl.DataFrame, params: Mapping[str, Any]) -> Outcome:
    names = list(params["index_names"])
    found = sorted(set(frame["index_name"].to_list()) & set(names))
    return Outcome("benchmark_present", bool(found), {"looked_for": names, "found": found})


def row_count(rows: int, previous: Sequence[int], params: Mapping[str, Any]) -> Outcome:
    """rows within max(pct of the median, an absolute floor) of the previous sessions' median."""
    if not previous:
        return Outcome("row_count", True, {"rows": rows, "skipped": "no previous sessions"})
    mid = median(previous)
    band = max(mid * params["max_pct_diff"] / 100, params["min_abs_diff"])
    observed = {"rows": rows, "median": mid, "band": band, "sessions": len(previous)}
    return Outcome("row_count", abs(rows - mid) <= band, observed)


def vendor_revision(revision: int, supersedes_file_id: int | None) -> Outcome:
    return Outcome(
        "vendor_revision",
        revision == 1,
        {"revision": revision, "supersedes_file_id": supersedes_file_id},
    )


def arrival(day: date, weekend_days: Sequence[int], listed_holiday: bool) -> list[Outcome]:
    """holiday_session and special_session for a file that arrived for day."""
    weekend = day.isoweekday() in weekend_days
    observed = {"weekday": day.strftime("%A"), "listed_holiday": listed_holiday}
    return [
        Outcome("holiday_session", not listed_holiday, observed),
        Outcome("special_session", not (weekend and not listed_holiday), observed),
    ]


def file_arrived(present: Mapping[str, bool]) -> Outcome:
    missing = sorted(d for d, ok in present.items() if not ok)
    return Outcome("file_arrived", not missing, {"missing": missing})
