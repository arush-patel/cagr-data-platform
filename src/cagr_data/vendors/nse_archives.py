"""Files on nsearchives.nseindia.com: bhavcopy (UDiFF and legacy), listing masters, symbol changes.

Fetching and parsing are separate so that parsing is tested on saved files without the network.
Parsers return plain DataFrames with our column names; they never touch the database.
"""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from cagr_data.vendors.http import Fetched, Fetcher

BASE = "https://nsearchives.nseindia.com"

# NSE switched the cash-market bhavcopy to UDiFF on 8 Jul 2024; the legacy file ends on 5 Jul 2024.
UDIFF_FROM = date(2024, 7, 8)

BHAV_COLUMNS = [
    "trade_date", "isin", "symbol", "series", "name", "open", "high", "low", "close", "last",
    "prev_close", "volume", "turnover", "trades",
]  # fmt: skip


class FileShapeError(ValueError):
    """A file does not have the columns its format promises. The adapter needs an update."""


@dataclass(frozen=True)
class BhavcopyFile:
    session_date: date
    fmt: str  # 'udiff' | 'legacy'
    url: str
    cache_key: str


def bhavcopy_file(d: date) -> BhavcopyFile:
    if d >= UDIFF_FROM:
        name = f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
        return BhavcopyFile(d, "udiff", f"{BASE}/content/cm/{name}", f"bhavcopy/{d:%Y}/{name}")
    mon = d.strftime("%b").upper()
    name = f"cm{d:%d}{mon}{d:%Y}bhav.csv.zip"
    return BhavcopyFile(
        d, "legacy", f"{BASE}/content/historical/EQUITIES/{d:%Y}/{mon}/{name}", f"bhavcopy/{d:%Y}/{name}"
    )


def fetch_bhavcopy(fetcher: Fetcher, d: date) -> tuple[BhavcopyFile, Fetched]:
    f = bhavcopy_file(d)
    return f, fetcher.get(f.url, f.cache_key)


def _read_csv_from(content: bytes) -> pd.DataFrame:
    if content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if len(names) != 1:
                raise FileShapeError(f"expected one CSV in the zip, found {zf.namelist()}")
            content = zf.read(names[0])
    df = pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False)
    df.columns = [c.strip() for c in df.columns]
    return df


def _require(df: pd.DataFrame, cols: list[str], what: str) -> None:
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise FileShapeError(f"{what}: missing columns {missing}; has {list(df.columns)}")


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.str.strip().replace({"": None, "-": None}), errors="coerce")


def parse_bhavcopy(content: bytes, fmt: str, expected_date: date | None = None) -> pd.DataFrame:
    """Every cash-market row of one session, in our column names. Equity filtering happens later."""
    df = _read_csv_from(content)
    if fmt == "udiff":
        _require(
            df,
            ["TradDt", "ISIN", "TckrSymb", "SctySrs", "FinInstrmNm", "OpnPric", "HghPric", "LwPric", "ClsPric",
             "LastPric", "PrvsClsgPric", "TtlTradgVol", "TtlTrfVal", "TtlNbOfTxsExctd"],
            "UDiFF bhavcopy",
        )  # fmt: skip
        out = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(df["TradDt"].str.strip(), format="%Y-%m-%d").dt.date,
                "isin": df["ISIN"].str.strip(),
                "symbol": df["TckrSymb"].str.strip(),
                "series": df["SctySrs"].str.strip(),
                "name": df["FinInstrmNm"].str.strip(),
                "open": _num(df["OpnPric"]),
                "high": _num(df["HghPric"]),
                "low": _num(df["LwPric"]),
                "close": _num(df["ClsPric"]),
                "last": _num(df["LastPric"]),
                "prev_close": _num(df["PrvsClsgPric"]),
                "volume": _num(df["TtlTradgVol"]),
                "turnover": _num(df["TtlTrfVal"]),
                "trades": _num(df["TtlNbOfTxsExctd"]),
            }
        )
    elif fmt == "legacy":
        _require(
            df,
            ["SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "TOTTRDQTY", "TOTTRDVAL",
             "TIMESTAMP"],
            "legacy bhavcopy",
        )  # fmt: skip
        out = pd.DataFrame(
            {
                "trade_date": pd.to_datetime(df["TIMESTAMP"].str.strip(), format="%d-%b-%Y").dt.date,
                # Files before 2011 carry no ISIN; those rows are mapped through the ticker history.
                "isin": df["ISIN"].str.strip() if "ISIN" in df.columns else "",
                "symbol": df["SYMBOL"].str.strip(),
                "series": df["SERIES"].str.strip(),
                "name": "",
                "open": _num(df["OPEN"]),
                "high": _num(df["HIGH"]),
                "low": _num(df["LOW"]),
                "close": _num(df["CLOSE"]),
                "last": _num(df["LAST"]),
                "prev_close": _num(df["PREVCLOSE"]),
                "volume": _num(df["TOTTRDQTY"]),
                "turnover": _num(df["TOTTRDVAL"]),
                "trades": _num(df["TOTALTRADES"]) if "TOTALTRADES" in df.columns else None,
            }
        )
    else:
        raise ValueError(f"unknown bhavcopy format {fmt}")
    out = out[out["symbol"] != ""].reset_index(drop=True)
    if expected_date is not None:
        dates = set(out["trade_date"])
        if dates != {expected_date}:
            raise FileShapeError(f"bhavcopy for {expected_date} carries trade dates {sorted(dates)}")
    return out[BHAV_COLUMNS]


def is_rights_entitlement(isin: str) -> bool:
    return len(isin) == 12 and isin.startswith("INE") and isin[7:9] == "20"


def equity_rows(bhav: pd.DataFrame, series_preference: tuple[str, ...]) -> pd.DataFrame:
    """One row per ISIN: equity series only, the most preferred series when an ISIN has several.

    Rights entitlements (SOBHA-RE, MGEL-RE…) trade for a few days in the BE and ST series. Their ISIN
    has security code 20 where shares have 01; they are not shares and never become listings.
    """
    rank = {s: i for i, s in enumerate(series_preference)}
    eq = bhav[bhav["series"].isin(rank) & ~bhav["isin"].map(is_rights_entitlement)].copy()
    eq["_rank"] = eq["series"].map(rank)
    key = eq["isin"].where(eq["isin"] != "", "SYM:" + eq["symbol"])
    eq = eq.assign(_key=key).sort_values(["_key", "_rank"]).drop_duplicates("_key", keep="first")
    return eq.drop(columns=["_rank", "_key"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# Listing masters and symbol changes
# ---------------------------------------------------------------------------------------------

EQUITY_L_URL = f"{BASE}/content/equities/EQUITY_L.csv"
SME_EQUITY_L_URL = f"{BASE}/emerge/corporates/content/SME_EQUITY_L.csv"
SYMBOL_CHANGE_URL = f"{BASE}/content/equities/symbolchange.csv"


def _pick(df: pd.DataFrame, *candidates: str) -> pd.Series:
    norm = {c.upper().replace(" ", "").replace("_", ""): c for c in df.columns}
    for cand in candidates:
        key = cand.upper().replace(" ", "").replace("_", "")
        if key in norm:
            return df[norm[key]].astype(str).str.strip()
    raise FileShapeError(f"none of {candidates} in {list(df.columns)}")


def _parse_nse_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.str.strip(), format="mixed", dayfirst=True, errors="coerce").dt.date


def parse_listing_master(content: bytes) -> pd.DataFrame:
    """EQUITY_L.csv / SME_EQUITY_L.csv: symbol, name, series, listing date, ISIN, face value."""
    df = _read_csv_from(content)
    out = pd.DataFrame(
        {
            "symbol": _pick(df, "SYMBOL"),
            "name": _pick(df, "NAME OF COMPANY", "NAME_OF_COMPANY"),
            "series": _pick(df, "SERIES"),
            "listed_on": _parse_nse_date(_pick(df, "DATE OF LISTING", "DATE_OF_LISTING")),
            "isin": _pick(df, "ISIN NUMBER", "ISIN_NUMBER", "ISIN"),
            "face_value": pd.to_numeric(_pick(df, "FACE VALUE", "FACE_VALUE"), errors="coerce"),
        }
    )
    return out[out["isin"] != ""].reset_index(drop=True)


def parse_symbol_changes(content: bytes) -> pd.DataFrame:
    """symbolchange.csv: company, old symbol, new symbol, date of change. The file has no header row."""
    raw = pd.read_csv(io.BytesIO(content), header=None, dtype=str, keep_default_na=False, encoding="latin-1")
    if raw.shape[1] < 4:
        raise FileShapeError(f"symbolchange.csv has {raw.shape[1]} columns, expected 4")
    raw = raw.iloc[:, :4]
    raw.columns = ["name", "old_symbol", "new_symbol", "changed_on"]
    for c in raw.columns:
        raw[c] = raw[c].str.strip()
    # Some copies of the file carry a header row; drop it.
    raw = raw[raw["old_symbol"].str.upper() != "SM_KEY_SYMBOL"]
    raw = raw[~raw["changed_on"].str.upper().str.contains("DATE")]
    raw["changed_on"] = _parse_nse_date(raw["changed_on"])
    return raw.dropna(subset=["changed_on"]).reset_index(drop=True)


def parse_date(s: str) -> date:
    return datetime.strptime(s.strip(), "%d-%b-%Y").date()
