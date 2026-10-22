"""Synthetic NSE files in the exact formats the adapters read, laid out like the download cache.

Used by the tests and by `cagr demo`, so the whole pipeline runs without reaching NSE.

The story the files tell (Sep–Oct 2026):
  * XNSE trades weekdays; 2 Oct and 20 Oct are holidays; Saturday 10 Oct is a special session.
  * RELIANCE, TCS, INFY trade every session. ABCIND splits 1:5 (Rs 10 → Rs 2) with ex-date 15 Oct,
    announced through the corporate-actions API. As in the real files, NSE's previous close on the
    ex-date is the unadjusted close.
  * XYZOLD is renamed XYZNEW on 14 Oct (same ISIN, listed in symbolchange.csv).
  * NEWCO first appears on 21 Oct and is in no master file.
  * A government bond row (series GS) is in every file and must be ignored.
"""

from __future__ import annotations

import io
import json
import math
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from cagr_data.vendors import nse_api

UDIFF_COLUMNS = [
    "TradDt", "BizDt", "Sgmt", "Src", "FinInstrmTp", "FinInstrmId", "ISIN", "TckrSymb", "SctySrs", "XpryDt",
    "FininstrmActlXpryDt", "StrkPric", "OptnTp", "FinInstrmNm", "OpnPric", "HghPric", "LwPric", "ClsPric",
    "LastPric", "PrvsClsgPric", "UndrlygPric", "SttlmPric", "OpnIntrst", "ChngInOpnIntrst", "TtlTradgVol",
    "TtlTrfVal", "TtlNbOfTxsExctd", "SsnId", "NewBrdLotQty", "Rmks", "Rsvd1", "Rsvd2", "Rsvd3", "Rsvd4",
]  # fmt: skip

START = date(2026, 9, 1)
END = date(2026, 10, 23)
HOLIDAYS = {date(2026, 10, 2): "Mahatma Gandhi Jayanti", date(2026, 10, 20): "Dussehra"}
SPECIAL = date(2026, 10, 10)
SPLIT_EX = date(2026, 10, 15)
RENAME_ON = date(2026, 10, 14)
NEWCO_FROM = date(2026, 10, 21)


@dataclass(frozen=True)
class Stock:
    isin: str
    symbol: str
    name: str
    base: float
    listed: str


STOCKS = [
    Stock("INE002A01018", "RELIANCE", "Reliance Industries Limited", 2800.0, "29-NOV-1995"),
    Stock("INE467B01029", "TCS", "Tata Consultancy Services Limited", 4100.0, "25-AUG-2004"),
    Stock("INE009A01021", "INFY", "Infosys Limited", 1850.0, "08-FEB-1995"),
    Stock("INE123A01016", "ABCIND", "ABC Industries Limited", 1000.0, "12-MAR-2001"),
    Stock("INE999Z01010", "XYZOLD", "XYZ Limited", 300.0, "05-JUN-2010"),
]
NEWCO = Stock("INE555N01015", "NEWCO", "Newco Limited", 120.0, "21-OCT-2026")


def sessions() -> list[date]:
    out, d = [], START
    while d <= END:
        if (d.weekday() < 5 and d not in HOLIDAYS) or d == SPECIAL:
            out.append(d)
        d += timedelta(days=1)
    return out


def raw_close(stock: Stock, i: int, d: date) -> float:
    """Deterministic wiggle; ABCIND trades at a fifth of its price from the ex-date."""
    px = stock.base * (1 + 0.01 * math.sin(i / 3 + len(stock.symbol)))
    if stock.symbol == "ABCIND" and d >= SPLIT_EX:
        px /= 5
    return round(px, 2)


def bhavcopy_rows(
    d: date, i: int, prev: dict[str, float], overrides: dict[str, float] | None = None
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    stocks = [*STOCKS, NEWCO] if d >= NEWCO_FROM else STOCKS
    for s in stocks:
        symbol = "XYZNEW" if s.symbol == "XYZOLD" and d >= RENAME_ON else s.symbol
        close = (overrides or {}).get(s.symbol, raw_close(s, i, d))
        p = prev.get(s.isin, close)
        o, h, lo = round(close * 0.995, 2), round(close * 1.01, 2), round(close * 0.985, 2)
        vol = 100_000 + 1_000 * i
        if s.symbol == "ABCIND" and d >= SPLIT_EX:
            vol *= 5
        rows.append(
            {
                "TradDt": d.isoformat(),
                "BizDt": d.isoformat(),
                "Sgmt": "CM",
                "Src": "NSE",
                "FinInstrmTp": "STK",
                "FinInstrmId": str(1000 + len(rows)),
                "ISIN": s.isin,
                "TckrSymb": symbol,
                "SctySrs": "EQ",
                "FinInstrmNm": s.name.upper(),
                "OpnPric": f"{o:.2f}",
                "HghPric": f"{h:.2f}",
                "LwPric": f"{lo:.2f}",
                "ClsPric": f"{close:.2f}",
                "LastPric": f"{close:.2f}",
                "PrvsClsgPric": f"{p:.2f}",
                "TtlTradgVol": str(vol),
                "TtlTrfVal": f"{vol * close:.2f}",
                "TtlNbOfTxsExctd": str(5000 + i),
                "SsnId": "F1",
                "NewBrdLotQty": "1",
            }  # fmt: skip
        )
        prev[s.isin] = close
    rows.append(
        {
            "TradDt": d.isoformat(),
            "BizDt": d.isoformat(),
            "Sgmt": "CM",
            "Src": "NSE",
            "FinInstrmTp": "STK",
            "FinInstrmId": "9999",
            "ISIN": "IN0020230085",
            "TckrSymb": "718GS2033",
            "SctySrs": "GS",
            "FinInstrmNm": "GOI LOAN 7.18% 2033",
            "OpnPric": "100.10",
            "HghPric": "100.20",
            "LwPric": "100.00",
            "ClsPric": "100.15",
            "LastPric": "100.15",
            "PrvsClsgPric": "100.05",
            "TtlTradgVol": "50",
            "TtlTrfVal": "5007.50",
            "TtlNbOfTxsExctd": "3",
            "SsnId": "F1",
            "NewBrdLotQty": "1",
        }  # fmt: skip
    )
    return rows


def udiff_zip(d: date, rows: list[dict[str, str]]) -> bytes:
    buf = io.StringIO()
    buf.write(",".join(UDIFF_COLUMNS) + "\n")
    for r in rows:
        buf.write(",".join(r.get(c, "") for c in UDIFF_COLUMNS) + "\n")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv", buf.getvalue())
    return out.getvalue()


def bhav_path(root: Path, d: date) -> Path:
    return root / "nse_archives" / "bhavcopy" / f"{d:%Y}" / f"BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"


def write_bhavcopy(
    root: Path, d: date, i: int, prev: dict[str, float], overrides: dict[str, float] | None = None
) -> None:
    p = bhav_path(root, d)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(udiff_zip(d, bhavcopy_rows(d, i, prev, overrides)))


def write_all(root: Path, *, master_as_of: date = END) -> None:
    prev: dict[str, float] = {}
    for i, d in enumerate(sessions()):
        write_bhavcopy(root, d, i, prev)

    masters = root / "nse_archives" / "masters" / f"{master_as_of:%Y%m%d}"
    masters.mkdir(parents=True, exist_ok=True)
    lines = [
        "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE"
    ]
    for s in STOCKS:
        sym = "XYZNEW" if s.symbol == "XYZOLD" else s.symbol
        fv = 2 if s.symbol == "ABCIND" else 10
        lines.append(f"{sym},{s.name},EQ,{s.listed},{fv},1,{s.isin},{fv}")
    (masters / "EQUITY_L.csv").write_text("\n".join(lines) + "\n")
    (masters / "symbolchange.csv").write_text(
        "XYZ Limited,XYZOLD,XYZNEW,14-OCT-2026\nOld Co,OLDCO,NEWCO2,01-JAN-2015\n"
    )

    for year in range(START.year, END.year + 2):  # the backfill asks for the year after too
        hol = sorted((d, n) for d, n in HOLIDAYS.items() if d.year == year)
        p = root / "nse_api" / nse_api.holidays_url(year)[1]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            json.dumps(
                {
                    "CM": [
                        {
                            "tradingDate": f"{d:%d-%b-%Y}",
                            "weekDay": f"{d:%A}",
                            "description": n,
                            "Sr_no": k + 1,
                        }
                        for k, (d, n) in enumerate(hol)
                    ],
                    "FO": [],
                }  # fmt: skip
            )
        )
    write_corporate_actions(root, START, END)


def write_corporate_actions(
    root: Path, start: date, end: date, extra: list[dict[str, str]] | None = None
) -> None:
    payload = [
        {"symbol": "ABCIND", "series": "EQ", "ind": "-", "faceVal": "10",
         "subject": "Face Value Split (Sub-Division) - From Rs 10/- Per Share To Rs 2/- Per Share",
         "exDate": f"{SPLIT_EX:%d-%b-%Y}", "recDate": f"{SPLIT_EX:%d-%b-%Y}", "isin": "INE123A01016"},
        {"symbol": "TCS", "series": "EQ", "ind": "-", "faceVal": "1",
         "subject": "Interim Dividend - Rs 11 Per Share", "exDate": "16-Oct-2026", "recDate": "16-Oct-2026",
         "isin": "INE467B01029"},
        {"symbol": "INFY", "series": "EQ", "ind": "-", "faceVal": "5", "subject": "Annual General Meeting",
         "exDate": "18-Sep-2026", "recDate": "-", "isin": "INE009A01021"},
    ] + (extra or [])  # fmt: skip
    p = root / "nse_api" / "corporate-actions" / f"{start:%Y%m%d}-{end:%Y%m%d}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload))
