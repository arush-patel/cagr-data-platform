"""Every shared lab feature passes the toolkit's no-future check and fills after its warm-up."""

import math
import random
from datetime import date, timedelta

import polars as pl
import pytest
from features import price

from cagr import fs


@pytest.fixture(scope="module")
def bars() -> pl.DataFrame:
    rng = random.Random(7)
    rows = []
    for listing_id in (1, 2):
        log_price = math.log(100)
        for i in range(320):
            log_price += rng.gauss(0, 0.02)
            close = math.exp(log_price)
            spread = close * rng.uniform(0.005, 0.03)
            rows.append({
                "listing_id": listing_id,
                "date": date(2023, 1, 2) + timedelta(days=i),
                "open": close,
                "high": close + spread,
                "low": close - spread,
                "close": close,
                "volume": float(rng.randint(1_000, 50_000)),
            })  # fmt: skip
    return pl.DataFrame(rows)


def test_keys_are_unique() -> None:
    keys = [f.key for f in price.ALL]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("feature", price.ALL, ids=lambda f: f.key)
def test_no_future_and_warm_up(feature: fs.Feature, bars: pl.DataFrame) -> None:
    fs.check_no_future(feature, bars)
    values = fs.compute(feature, bars).filter(pl.col("listing_id") == 1)[feature.key]
    assert values[: feature.min_bars - 1].is_null().all()
    assert values[feature.min_bars - 1 :].is_not_null().all()
