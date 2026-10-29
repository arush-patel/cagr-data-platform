"""Parse NSE's corporate-actions API: the JSON response, and the free-text subject of each row
(spec 0002, sections 7.1 and 7.2).

The response is a JSON array; empty values are the string "-" and dates are DD-Mon-YYYY. Ratios
and amounts appear only in the subject, so the subject is split into parts and each part is
matched by one pattern per kind. A part that no pattern matches is "unparsed", with nothing
guessed. Wording from around 2010 is abbreviated (`Fv Spl Rs.5tors.2`, `Agm/Div-Rs.3.50`) and
has its own alternatives in the same patterns.

What the parsed numbers mean:
- split and consolidation: ratio_old is the old face value, ratio_new the new one;
- bonus and rights a:b: ratio_new = a new shares for every ratio_old = b held;
- rights: issue_price is the face value plus the premium;
- dividend: cash_per_share in rupees (a percentage is of the face value). An amount with a
  leading zero and no decimal point (`Rs.0125`, GENESYS 2019, most likely Rs 0.125) is left
  unparsed rather than guessed; `Rs 09` is read as 9.
"""

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

import polars as pl

from cagr_data.vendors.errors import FileShapeError

Kind = Literal[
    "split", "bonus", "consolidation", "rights", "demerger", "dividend", "buyback", "other"
]

RESPONSE_FIELDS = (
    "symbol", "comp", "series", "isin", "faceVal", "subject", "exDate", "recDate",
    "bcStartDate", "bcEndDate", "ndStartDate", "ndEndDate",
)  # fmt: skip

CORPORATE_ACTION_SCHEMA: dict[str, pl.DataType] = {
    "row_no": pl.Int32(),
    "symbol": pl.String(),
    "series": pl.String(),
    "isin": pl.String(),
    "company": pl.String(),
    "subject": pl.String(),
    "ex_date": pl.Date(),
    "record_date": pl.Date(),
    "face_value": pl.Decimal(18, 4),
    "bc_start_date": pl.Date(),
    "bc_end_date": pl.Date(),
    "nd_start_date": pl.Date(),
    "nd_end_date": pl.Date(),
    "board": pl.String(),
}


def _value(row: dict[str, Any], key: str) -> str | None:
    v = row.get(key)
    if v is None:
        return None
    s = str(v).strip()
    return None if s in ("", "-") else s


def _date(s: str | None) -> date | None:
    if s is None:
        return None
    return datetime.strptime(s, "%d-%b-%Y").date()


def parse_response(body: bytes, board: Literal["main", "sme"]) -> pl.DataFrame:
    """One row per element of the response array, as sent (empty values as null)."""
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise FileShapeError(f"corporate actions: not JSON ({e})") from e
    if not isinstance(data, list):
        raise FileShapeError("corporate actions: expected a JSON array")
    rows = []
    for row_no, item in enumerate(data, start=1):
        if not isinstance(item, dict) or "subject" not in item or "exDate" not in item:
            raise FileShapeError(f"corporate actions: unexpected element {str(item)[:80]}")
        face = _value(item, "faceVal")
        rows.append(
            {
                "row_no": row_no,
                "symbol": _value(item, "symbol"),
                "series": _value(item, "series"),
                "isin": _value(item, "isin"),
                "company": _value(item, "comp"),
                "subject": _value(item, "subject"),
                "ex_date": _date(_value(item, "exDate")),
                "record_date": _date(_value(item, "recDate")),
                "face_value": Decimal(face) if face is not None else None,
                "bc_start_date": _date(_value(item, "bcStartDate")),
                "bc_end_date": _date(_value(item, "bcEndDate")),
                "nd_start_date": _date(_value(item, "ndStartDate")),
                "nd_end_date": _date(_value(item, "ndEndDate")),
                "board": board,
            }
        )
    return pl.DataFrame(rows, schema=CORPORATE_ACTION_SCHEMA)


@dataclass(frozen=True)
class ParsedAction:
    kind: Kind
    ratio_new: Decimal | None = None
    ratio_old: Decimal | None = None
    cash_per_share: Decimal | None = None
    issue_price: Decimal | None = None
    parsed: bool = True
    part: str = ""


_NUM = r"(\d+(?:\.\d+)?)"
_RS = r"r[se]\.?\s*"  # Rs, Re, Rs., Re.
_FV_CHANGE = re.compile(_RS + _NUM + r"\D*?to\s*" + _RS + _NUM, re.I)
_SPLIT = re.compile(r"split|splt|spl\b|spl\s*" + _RS + r"|sub-?\s*division", re.I)
_CONSOLIDATION = re.compile(r"consolidat", re.I)
_BONUS = re.compile(r"\bbon(?:us)?\b\s*-?\s*(\d+)\s*:\s*(\d+)", re.I)
_BONUS_DEBENTURE = re.compile(r"\bbonus\s*deb", re.I)
_RIGHTS = re.compile(
    r"\brights?\b\s*-?\s*(?:eq\s*)?(\d+)\s*:\s*(\d+)"
    r"(?:\s*@\s*(prem(?:ium)?\s*(?:of\s*)?)?" + _RS + _NUM + r")?",
    re.I,
)
_DIVIDEND = re.compile(r"\bdiv(?:idend)?\b", re.I)
_CASH = re.compile(_RS + _NUM, re.I)
_BARE_CASH = re.compile(r"^\s*-?\s*" + _NUM + r"\b")  # "Div-1.50" in 2010 wording
_LOST_POINT = re.compile(r"0\d{2,}$")  # Rs.0125: a decimal point lost, so not read as 125
_PERCENT = re.compile(_NUM + r"\s*%")
_BUYBACK = re.compile(r"buy\s*-?\s*back", re.I)
_DEMERGER = re.compile(r"de-?merger|scheme of arrangement", re.I)
_DISTRIBUTION = re.compile(r"^\s*distribution\b", re.I)
_OTHER = re.compile(
    r"\b(agm|egm|annual general meetings?|extra\s*-?\s*ordinary general meetings?|"
    r"general meetings?|interest|redemption|distribution|capital reduction|"
    r"reduction of capital|income|amalgamation|merger|warrants?|purpose revised|"
    r"book closure|record date|postal ballot|scheme)\b",
    re.I,
)

# "/" separates actions ("Bon 1:1/Fv Spl Rs.5tors.2"), except in "Rs10/-"; so do "&" and "And".
_SEPARATOR = re.compile(r"/(?!-)|&|\band\b|;", re.I)


def _d(s: str) -> Decimal:
    return Decimal(s)


def _dividends(part: str, face_value: Decimal | None) -> list[ParsedAction]:
    """Every dividend in a part: one per "Dividend" or "Div", with the amount that follows it
    ("Interim Dividend - Rs 9 Per Share Special Dividend - Rs 18 Per Share" is two)."""
    marks = list(_DIVIDEND.finditer(part))
    found = []
    for mark, following in zip(marks, [*marks[1:], None], strict=True):
        tail = part[mark.end() : following.start() if following else None]
        if (m := _CASH.search(tail)) and _LOST_POINT.match(m[1]):
            found.append(ParsedAction("dividend", parsed=False, part=part))
        elif m:
            found.append(ParsedAction("dividend", cash_per_share=_d(m[1]), part=part))
        elif (m := _PERCENT.search(tail)) and face_value is not None:
            cash = (_d(m[1]) * face_value / 100).quantize(Decimal("0.0001"))
            found.append(ParsedAction("dividend", cash_per_share=cash, part=part))
        elif m := _BARE_CASH.search(tail):
            found.append(ParsedAction("dividend", cash_per_share=_d(m[1]), part=part))
        else:
            found.append(ParsedAction("dividend", parsed=False, part=part))
    return found


def _parse_part(part: str, face_value: Decimal | None) -> list[ParsedAction]:
    """The actions one part of a subject describes; none when the part is only words without
    an action (such as "Agm")."""
    if _BONUS_DEBENTURE.search(part):
        return [ParsedAction("other", part=part)]  # debentures, not shares
    if m := _BONUS.search(part):
        return [ParsedAction("bonus", ratio_new=_d(m[1]), ratio_old=_d(m[2]), part=part)]
    if m := _RIGHTS.search(part):
        issue: Decimal | None = None
        if m[4] is not None:
            amount = _d(m[4])
            # A premium is on top of the face value.
            issue = amount if not m[3] else (amount + face_value if face_value else None)
        return [
            ParsedAction(
                "rights", ratio_new=_d(m[1]), ratio_old=_d(m[2]), issue_price=issue,
                parsed=issue is not None, part=part,
            )
        ]  # fmt: skip
    if _SPLIT.search(part) or _CONSOLIDATION.search(part):
        m = _FV_CHANGE.search(part)
        if m is None:
            kind: Kind = "consolidation" if _CONSOLIDATION.search(part) else "split"
            return [ParsedAction(kind, parsed=False, part=part)]
        old, new = _d(m[1]), _d(m[2])
        kind = "consolidation" if new > old else "split"
        return [ParsedAction(kind, ratio_new=new, ratio_old=old, part=part)]
    if _BUYBACK.search(part):
        return [ParsedAction("buyback", part=part)]
    if _DEMERGER.search(part):
        return [ParsedAction("demerger", part=part)]
    if _DIVIDEND.search(part):
        return _dividends(part, face_value)
    if _OTHER.search(part):
        return [ParsedAction("other", part=part)]
    return []


def parse_subject(subject: str, face_value: Decimal | None = None) -> list[ParsedAction]:
    """Every action a subject describes, in order. A subject no pattern recognises gives one
    unparsed "other" action, so nothing is silently dropped."""
    if _DISTRIBUTION.search(subject):
        # A REIT or InvIT distribution lists its parts (interest, dividend, return of
        # capital); none of them is a company's dividend, so it is one recorded action.
        return [ParsedAction("other", part=subject.strip())]
    parts = [p.strip(" -") for p in _SEPARATOR.split(subject) if p.strip(" -")]
    actions = [a for part in parts for a in _parse_part(part, face_value)]
    if not actions:
        return [ParsedAction("other", parsed=False, part=subject.strip())]
    return actions
