"""Datasets: rows to learn from, with features and labels joined as they were known.

Two shapes of rows:

- listing-date rows: one per (listing_id, date), for screens and cross-sections
- event rows: one per event (a golden, say), anchored at a session such as the day before it
  started (`at="T-1"`)

Features are joined on the exact (listing_id, date) of each row, so a row only ever sees the
value computed for that session. A missing value stays null; nothing is carried forward from
an earlier day unless you ask for it with `asof`.

Reading features and labels from the lake comes in a later slice. These functions take frames,
so they work today on anything `cagr.fs.compute` produces.
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import polars as pl

from cagr.calendar import Calendar

KEYS = ("listing_id", "date")


class LeakError(ValueError):
    """A label was asked for as a feature, or a feature as a label."""


@dataclass(frozen=True)
class Dataset:
    frame: pl.DataFrame
    features: tuple[str, ...]
    labels: tuple[str, ...]
    recipe: dict[str, Any] = field(default_factory=dict)

    @property
    def recipe_hash(self) -> str:
        """Stable id of what built this dataset; the cache key once datasets are stored."""
        text = json.dumps(self.recipe, sort_keys=True, default=str)
        return hashlib.sha256(text.encode()).hexdigest()[:16]

    def X(self) -> pl.DataFrame:
        return self.frame.select(self.features)

    def y(self, label: str | None = None) -> pl.Series:
        if label is None:
            if len(self.labels) != 1:
                raise ValueError(f"choose one of the labels: {', '.join(self.labels)}")
            label = self.labels[0]
        return self.frame[label]


_AT = re.compile(r"^T(?:([+-])(\d+))?$")


def anchor_offset(at: str) -> int:
    """Sessions from an event's start: "T" is 0, "T-1" is -1, "T+5" is 5."""
    m = _AT.match(at.replace(" ", "").replace("\u2212", "-"))
    if not m:
        raise ValueError(f"anchor {at!r} must look like T, T-1 or T+5")
    sign, n = m.groups()
    return 0 if n is None else (-int(n) if sign == "-" else int(n))


def event_rows(
    events: pl.DataFrame,
    calendar: Calendar,
    *,
    at: str = "T-1",
    start_col: str = "start_date",
) -> pl.DataFrame:
    """Event rows anchored `at` sessions from each event's start, in a `date` column.

    The events keep every column they had. An event whose start is not a session is anchored
    on the last session before it.
    """
    offset = anchor_offset(at)

    def anchor(d: date) -> date:
        return calendar.shift(calendar.on_or_before(d), offset)

    return events.with_columns(
        pl.col(start_col).map_elements(anchor, return_dtype=pl.Date).alias("date")
    )


def build(
    rows: pl.DataFrame,
    *,
    features: Mapping[str, pl.DataFrame],
    labels: Mapping[str, pl.DataFrame] | None = None,
    leaky: Sequence[str] = (),
    recipe: Mapping[str, Any] | None = None,
) -> Dataset:
    """Join each feature and label frame (listing_id, date, value) onto the rows.

    `features` and `labels` map a key such as "rsi_14@1" to the frame with its values; the
    value column must carry the same name. `leaky` names keys known to use future data (labels
    from `cagr.fs.label`); asking for one as a feature raises.
    """
    labels = labels or {}
    for key in features:
        if key in leaky:
            raise LeakError(f"{key} uses future data and can only be a label")
    overlap = set(features) & set(labels)
    if overlap:
        raise LeakError(f"{', '.join(sorted(overlap))} can't be both a feature and a label")
    missing = [k for k in KEYS if k not in rows.columns]
    if missing:
        raise ValueError(f"rows need columns {missing}")
    frame = rows
    for key, values in {**features, **labels}.items():
        if key not in values.columns:
            raise ValueError(f"the frame for {key} has no column named {key}")
        if values.select(KEYS).is_duplicated().any():
            raise ValueError(f"{key} has more than one value for a (listing_id, date)")
        frame = frame.join(values.select(*KEYS, key), on=list(KEYS), how="left")
    return Dataset(
        frame=frame,
        features=tuple(features),
        labels=tuple(labels),
        recipe={"features": sorted(features), "labels": sorted(labels), **(recipe or {})},
    )


def asof(
    rows: pl.DataFrame, values: pl.DataFrame, column: str, *, max_days: int | None = None
) -> pl.DataFrame:
    """Join the latest value on or before each row's date, per listing.

    For slow-moving inputs (shares outstanding, a quarterly figure). A value older than
    max_days calendar days is treated as missing; None allows any age.
    """
    left = rows.sort("date")
    right = values.select(*KEYS, column).sort("date").rename({"date": "_value_date"})
    out = left.join_asof(
        right,
        left_on="date",
        right_on="_value_date",
        by="listing_id",
        strategy="backward",
        check_sortedness=False,
    )
    if max_days is not None:
        too_old = (pl.col("date") - pl.col("_value_date")).dt.total_days() > max_days
        out = out.with_columns(pl.when(too_old).then(None).otherwise(pl.col(column)).alias(column))
    return out.drop("_value_date")
