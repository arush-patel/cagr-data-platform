"""JSON on www.nseindia.com/api behind bot protection: holidays and corporate actions.

Calls are few (one or two a day), run on a cookie session, and back off on 403 (see http.py).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime

import pandas as pd

from cagr_data.vendors.http import Fetched, Fetcher

API = "https://www.nseindia.com/api"
HOME = "https://www.nseindia.com/"


# The list starts in 2011; earlier years come back empty and need the archive as evidence instead.
FIRST_HOLIDAY_YEAR = 2011


def holidays_url(year: int, today: date | None = None) -> tuple[str, str]:
    """Without `year` the API returns only the current year, so a backfill asks for each year.

    A past year's list no longer changes and is cached once; the current and later years are fetched
    again each day, because NSE adds holidays (elections, special closures) during the year.
    """
    today = today or date.today()
    key = (
        f"holiday-master/trading-{year}.json"
        if year < today.year
        else (f"holiday-master/trading-{year}-{today:%Y%m%d}.json")
    )
    return f"{API}/holiday-master?type=trading&year={year}", key


# The main board and SME Emerge are separate lists; `equities` alone misses every SME split.
ACTION_INDEXES = ("equities", "sme")


def corporate_actions_url(start: date, end: date, index: str = "equities") -> tuple[str, str]:
    url = f"{API}/corporates-corporateActions?index={index}&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}"
    prefix = "" if index == "equities" else f"{index}-"
    return url, f"corporate-actions/{prefix}{start:%Y%m%d}-{end:%Y%m%d}.json"


_ISIN = re.compile(r"IN[A-Z0-9]{9}[0-9]")


def fetch_json(fetcher: Fetcher, url: str, key: str) -> tuple[Fetched, object]:
    f = fetcher.get(url, key)
    if f.content is None:
        raise FileNotFoundError(url)
    return f, json.loads(f.content)


def parse_holidays(payload: object, segment: str = "CM") -> pd.DataFrame:
    """Trading holidays of one segment (CM = cash market): date and description."""
    if not isinstance(payload, dict):
        raise ValueError("holiday payload is not an object")
    if segment not in payload and payload:
        raise ValueError(f"holiday payload has no {segment} segment")
    rows = payload.get(segment) or []
    out = pd.DataFrame(
        {
            "holiday_date": [pd.to_datetime(r["tradingDate"], format="%d-%b-%Y").date() for r in rows],
            "description": [str(r.get("description", "")).strip() for r in rows],
        },
        columns=["holiday_date", "description"],
    )
    return out.drop_duplicates("holiday_date").sort_values("holiday_date").reset_index(drop=True)


def parse_corporate_actions(payload: object) -> pd.DataFrame:
    """One row per announced action: symbol, series, ISIN, subject text, ex-date, record date."""
    if not isinstance(payload, list):
        raise ValueError("corporate actions payload is not a list")

    def d(v: object) -> date | None:
        s = str(v or "").strip()
        if not s or s == "-":
            return None
        return datetime.strptime(s, "%d-%b-%Y").date()

    return pd.DataFrame(
        [
            {
                "symbol": str(r.get("symbol", "")).strip(),
                "series": str(r.get("series", "")).strip(),
                # the SME list puts a numeric scrip code here, not an ISIN: fall back to the symbol
                "isin": isin if _ISIN.fullmatch(isin := str(r.get("isin", "") or "").strip()) else "",
                "subject": " ".join(str(r.get("subject", "")).split()),
                "ex_date": d(r.get("exDate")),
                "record_date": d(r.get("recDate")),
                "face_value": r.get("faceVal"),
            }
            for r in payload
        ],
        columns=["symbol", "series", "isin", "subject", "ex_date", "record_date", "face_value"],
    )
