"""JSON on www.nseindia.com/api behind bot protection: holidays and corporate actions.

Calls are few (one or two a day), run on a cookie session, and back off on 403 (see http.py).
"""

from __future__ import annotations

import json
from datetime import date, datetime

import pandas as pd

from cagr_data.vendors.http import Fetched, Fetcher

API = "https://www.nseindia.com/api"
HOME = "https://www.nseindia.com/"


def holidays_url() -> tuple[str, str]:
    return f"{API}/holiday-master?type=trading", f"holiday-master/trading-{date.today():%Y%m%d}.json"


def corporate_actions_url(start: date, end: date) -> tuple[str, str]:
    url = (
        f"{API}/corporates-corporateActions?index=equities&from_date={start:%d-%m-%Y}&to_date={end:%d-%m-%Y}"
    )
    return url, f"corporate-actions/{start:%Y%m%d}-{end:%Y%m%d}.json"


def fetch_json(fetcher: Fetcher, url: str, key: str) -> tuple[Fetched, object]:
    f = fetcher.get(url, key)
    if f.content is None:
        raise FileNotFoundError(url)
    return f, json.loads(f.content)


def parse_holidays(payload: object, segment: str = "CM") -> pd.DataFrame:
    """Trading holidays of one segment (CM = cash market): date and description."""
    if not isinstance(payload, dict) or segment not in payload:
        raise ValueError(f"holiday payload has no {segment} segment")
    rows = payload[segment]
    out = pd.DataFrame(
        {
            "holiday_date": [pd.to_datetime(r["tradingDate"], format="%d-%b-%Y").date() for r in rows],
            "description": [str(r.get("description", "")).strip() for r in rows],
        }
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
                "isin": str(r.get("isin", "") or "").strip(),
                "subject": " ".join(str(r.get("subject", "")).split()),
                "ex_date": d(r.get("exDate")),
                "record_date": d(r.get("recDate")),
                "face_value": r.get("faceVal"),
            }
            for r in payload
        ],
        columns=["symbol", "series", "isin", "subject", "ex_date", "record_date", "face_value"],
    )
