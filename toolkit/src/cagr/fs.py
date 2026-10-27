"""Features and labels: how they are declared, computed and checked for look-ahead.

A feature is a function that returns a Polars expression over one listing's bars. The
decorator records its name, version, inputs and warm-up; `compute` runs it per listing and
blanks the warm-up; `check_no_future` recomputes it on history cut at several dates and fails
if any value changes, which is what a feature that peeks at later bars does.

A label is declared the same way with `@label` and is marked `uses_future=True`: it is meant to
look ahead, and only datasets' target columns may read it.

`preview` and `register` (the experimental lane) arrive with the lake, in later slices.
"""

import hashlib
import inspect
import textwrap
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

import polars as pl

FeatureFn = Callable[[], pl.Expr]


class LookaheadError(AssertionError):
    """Raised when a feature's values depend on bars after the date they are for."""


@dataclass(frozen=True)
class Feature:
    name: str
    version: int
    fn: FeatureFn = field(repr=False)
    inputs: tuple[str, ...]
    min_bars: int
    asset_classes: tuple[str, ...] = ("equity",)
    timeframe: str = "1d"
    uses_future: bool = False
    code_hash: str | None = None
    """sha256 of the function's source, so a changed function can't reuse a version."""

    @property
    def key(self) -> str:
        return f"{self.name}@{self.version}"

    def expr(self) -> pl.Expr:
        return self.fn()


def _hash(fn: FeatureFn) -> str | None:
    try:
        source = textwrap.dedent(inspect.getsource(fn))
    except (OSError, TypeError):  # defined somewhere without source, such as a bare REPL
        return None
    return hashlib.sha256(source.encode()).hexdigest()


def _declare(
    name: str,
    version: int,
    inputs: Sequence[str],
    min_bars: int,
    asset_classes: Sequence[str],
    timeframe: str,
    uses_future: bool,
) -> Callable[[FeatureFn], Feature]:
    if not name.isidentifier() or name != name.lower():
        raise ValueError(f"name {name!r} must be lower_snake_case")
    if version < 1:
        raise ValueError("version starts at 1")
    if min_bars < 1:
        raise ValueError("min_bars must be at least 1")
    if not inputs:
        raise ValueError("declare the input columns the feature reads")

    def wrap(fn: FeatureFn) -> Feature:
        return Feature(
            name=name,
            version=version,
            fn=fn,
            inputs=tuple(inputs),
            min_bars=min_bars,
            asset_classes=tuple(asset_classes),
            timeframe=timeframe,
            uses_future=uses_future,
            code_hash=_hash(fn),
        )

    return wrap


def feature(
    *,
    name: str,
    version: int,
    inputs: Sequence[str],
    min_bars: int,
    asset_classes: Sequence[str] = ("equity",),
    timeframe: str = "1d",
) -> Callable[[FeatureFn], Feature]:
    """Declare a feature: `@fs.feature(name="rsi_14", version=1, inputs=["close"], min_bars=15)`."""
    return _declare(name, version, inputs, min_bars, asset_classes, timeframe, uses_future=False)


def label(
    *,
    name: str,
    version: int,
    inputs: Sequence[str],
    min_bars: int = 1,
    asset_classes: Sequence[str] = ("equity",),
    timeframe: str = "1d",
) -> Callable[[FeatureFn], Feature]:
    """Declare a label. Labels look ahead by design and are marked uses_future=True."""
    return _declare(name, version, inputs, min_bars, asset_classes, timeframe, uses_future=True)


def compute(
    f: Feature, bars: pl.DataFrame, *, by: str | None = "listing_id", date_col: str = "date"
) -> pl.DataFrame:
    """Values of f for every row of bars: [by,] date, f.key. Null during the warm-up.

    bars holds one or many listings; with by=None it must be a single listing.
    """
    missing = [c for c in (*f.inputs, date_col, *([by] if by else [])) if c not in bars.columns]
    if missing:
        raise ValueError(f"{f.key} needs columns {missing}")
    keys = [by, date_col] if by else [date_col]
    ordered = bars.sort(keys)
    value = f.expr()
    position = pl.int_range(pl.len())
    if by:
        value, position = value.over(by), position.over(by)
    warm = pl.when(position < f.min_bars - 1).then(None).otherwise(value)
    return ordered.select(*keys, warm.alias(f.key))


def check_no_future(
    f: Feature,
    bars: pl.DataFrame,
    *,
    cuts: int = 8,
    by: str | None = "listing_id",
    date_col: str = "date",
) -> list[date]:
    """Recompute f on history cut at `cuts` dates spread over the bars; fail on any difference.

    Labels are exempt: they look ahead on purpose. Returns the cut dates it checked.
    """
    if f.uses_future:
        raise ValueError(f"{f.key} is a label; labels use future data by design")
    full = compute(f, bars, by=by, date_col=date_col)
    dates = bars[date_col].unique().sort()
    candidates = dates.slice(f.min_bars - 1, max(dates.len() - f.min_bars, 0))
    if candidates.len() == 0:
        raise ValueError("not enough bars to check: need more than min_bars dates")
    step = max(candidates.len() // cuts, 1)
    chosen: list[date] = candidates.gather_every(step).to_list()[:cuts]
    keys = [by, date_col] if by else [date_col]
    for cut in chosen:
        cut_bars = bars.filter(pl.col(date_col) <= cut)
        trunc = compute(f, cut_bars, by=by, date_col=date_col)
        before = full.filter(pl.col(date_col) <= cut)
        joined = before.join(trunc, on=keys, how="left", suffix="_cut")
        a, b = joined[f.key], joined[f"{f.key}_cut"]
        same = (a.is_null() & b.is_null()) | (a == b).fill_null(False)
        if a.dtype.is_float():
            same = same | (a.is_nan() & b.is_nan()).fill_null(False)
        if not same.all():
            bad = joined.filter(~same).row(0, named=True)
            raise LookaheadError(
                f"{f.key} changes when history after {cut} is removed: "
                f"{bad[date_col]} was {bad[f.key]} with full history, {bad[f.key + '_cut']} without"
            )
    return chosen
