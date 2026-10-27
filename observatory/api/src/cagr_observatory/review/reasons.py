"""What kind of item it is, its title, and the sentences that say why it is waiting and how to
decide it (spec 0005, section 4.2)."""

from datetime import date
from fractions import Fraction
from typing import Any, Literal

X = "\N{MULTIPLICATION SIGN}"
PRICE_CHECKED = {"split", "bonus", "consolidation"}
CLEAN_RATIOS = [Fraction(n, d) for n, d in [(1, 2), (2, 3), (1, 3), (1, 4), (1, 5), (1, 10)]]
CLEAN_RATIOS += [1 / r for r in CLEAN_RATIOS]

Category = Literal[
    "unexplained_jump",
    "clean_ratio_jump",
    "action_disagrees",
    "missing_ratio",
    "unparsed",
    "demerger",
    "dividend_too_big",
    "possible_merge",
    "possible_rename",
    "other",
]

# In the order the type filter lists them.
CATEGORY_LABELS: dict[Category, str] = {
    "unexplained_jump": "Jump nothing explains",
    "clean_ratio_jump": "Jump near a split ratio",
    "action_disagrees": "Close disagrees with announcement",
    "missing_ratio": "Announcement lacks a ratio",
    "unparsed": "Unreadable announcement",
    "demerger": "Demerger",
    "dividend_too_big": "Dividend at or above the close",
    "possible_merge": "Possible merge",
    "possible_rename": "Possible index rename",
    "other": "Other",
}

# The note the adjustment rules leave on a pending row (cagr_data.reference.adjustments.decide).
_RULE_NOTES: dict[str, Category] = {
    "price jump, no action explains it": "unexplained_jump",
    "price jump, near a clean ratio": "clean_ratio_jump",
    "the close disagrees with the action": "action_disagrees",
    "an action on this date is unparsed": "unparsed",
    "demerger: confirm the factor from prices": "demerger",
    "dividend at or above the previous close": "dividend_too_big",
}


def nearest_clean_ratio(observed: float, tolerance: float = 0.05) -> Fraction | None:
    """The clean ratio (1/2, 2/3, 1/10, 10 and so on) within tolerance of observed, if any."""
    best = min(CLEAN_RATIOS, key=lambda r: abs(observed / float(r) - 1))
    return best if abs(observed / float(best) - 1) <= tolerance else None


def _num(x: float | None) -> str:
    return "?" if x is None else f"{x:.4g}"


def _ratio(r: Fraction) -> str:
    return f"{r.numerator}/{r.denominator}" if r.denominator != 1 else f"{r.numerator}"


def adjustment_category(row: dict[str, Any]) -> Category:
    """The rule's note says it while the item waits. A decision replaces the note with the
    reviewer's, so a decided item's kind is told from its data."""
    note = row["note"] or ""
    if note in _RULE_NOTES:
        return _RULE_NOTES[note]
    if note.endswith(" without a ratio"):
        return "missing_ratio"
    kind = row["action_kind"]
    if kind is None:
        observed = row["observed_factor"]
        clean = observed and nearest_clean_ratio(observed)
        return "clean_ratio_jump" if clean else "unexplained_jump"
    if kind == "demerger":
        return "demerger"
    if row.get("parse_status") not in (None, "parsed"):
        return "unparsed"
    if kind in PRICE_CHECKED | {"rights"} and (
        row["ratio_new"] is None or row["ratio_old"] is None
    ):
        return "missing_ratio"
    if kind in PRICE_CHECKED:
        return "action_disagrees"
    if kind == "dividend":
        return "dividend_too_big"
    return "other"


def adjustment_title(row: dict[str, Any]) -> str:
    kind = row["action_kind"]
    if kind is None:
        observed = row["observed_factor"]
        clean = nearest_clean_ratio(observed) if observed else None
        if clean is not None:
            return f"Jump {X}{_num(observed)}, near {_ratio(clean)}"
        return "Unexplained jump" + (f" {X}{_num(observed)}" if observed else "")
    if row["ratio_new"] is not None and row["ratio_old"] is not None:
        return f"{kind.capitalize()} {row['ratio_new']:g}:{row['ratio_old']:g}"
    return str(kind).capitalize()


def _moved(row: dict[str, Any]) -> str:
    before, on = row.get("close_before"), row.get("close_on")
    text = f"the close moved {X}{_num(row['observed_factor'])}"
    if before is not None and on is not None:
        text += f", from {_num(before)} to {_num(on)}"
    return text


def _isin(row: dict[str, Any]) -> str:
    if row["isin_before"] and row["isin_on"] and row["isin_before"] != row["isin_on"]:
        return " The ISIN changed the same day, as it does on most splits and consolidations."
    return " The ISIN didn't change."


def adjustment_reason(row: dict[str, Any]) -> str:
    category = adjustment_category(row)
    observed, expected = row["observed_factor"], row["expected_factor"]
    subject = f"NSE announced “{row['subject']}”" if row["subject"] else "NSE announced something"
    if category == "unexplained_jump":
        return f"On the ex-date {_moved(row)}, and NSE announced nothing for it." + _isin(row)
    if category == "clean_ratio_jump":
        ratio = nearest_clean_ratio(observed) if observed else None
        near = f", within 5% of {_ratio(ratio)}" if ratio else ""
        return (
            f"On the ex-date {_moved(row)}{near}, the move a split, bonus or consolidation of"
            " that ratio makes, but NSE announced nothing for it." + _isin(row)
        )
    if category == "action_disagrees":
        apart = abs(observed / expected - 1) if observed and expected else None
        return (
            f"{subject}. That predicts a factor of {_num(expected)}, but {_moved(row)}"
            + (f", {apart:.0%} apart" if apart is not None else "")
            + ". The rules apply it only within 20%, so they stopped."
        )
    if category == "missing_ratio":
        return (
            f"{subject}, but no ratio could be read from it, so the rules can't work out a"
            f" factor. On the ex-date {_moved(row)}."
        )
    if category == "unparsed":
        return (
            f"{subject} for this date, and the parser couldn't read it, so the rules don't know"
            f" whether it changes the price. On the ex-date {_moved(row)}."
        )
    if category == "demerger":
        return (
            f"{subject}. A demerger moves part of the company's value elsewhere, and only the"
            f" prices can say how much: {_moved(row)}. A person always confirms it."
        )
    if category == "dividend_too_big":
        return (
            f"{subject}, a dividend at or above the previous close of {_num(row['close_before'])}."
            f" A dividend that size can't be adjusted for, so the amount was most likely misread."
            f" On the ex-date {_moved(row)}."
        )
    return f"{subject}. The rules couldn't confirm a factor from the prices."


ADJUSTMENT_GUIDES: dict[Category, str] = {
    "unexplained_jump": (
        "Look at what the price does after the jump. If it holds the new level, and the ISIN"
        " changed, it is most likely a split, bonus or consolidation NSE didn't announce:"
        " approve. If it comes back within days, it was a bad print; if trading had stopped for"
        " a long time before it, the move is likely real: reject."
    ),
    "clean_ratio_jump": (
        "A move this close to a clean ratio is usually a split or bonus missing from NSE's"
        " announcements. Approve if the price holds the new level; reject if the company did"
        " nothing that day."
    ),
    "action_disagrees": (
        "The chart adjusts by the announced factor, so the line still steps by the difference"
        " on the ex-date. A step the size of an ordinary day's news means the announcement is"
        " right: approve. A step that looks like another ratio means it was misread: reject and"
        " correct it from the command line."
    ),
    "missing_ratio": (
        "Read the announcement. If it changed the share count, approve and the observed move"
        " becomes the factor. Reject if it didn't change the price basis."
    ),
    "unparsed": (
        "Read the announcement. If it changed the share count or paid an extraordinary amount,"
        " approve and the observed move becomes the factor. Reject if it changed nothing."
    ),
    "demerger": (
        "Approve to adjust by the observed move, which is how a demerger is adjusted. Reject"
        " only if the move on the ex-date isn't the demerger."
    ),
    "dividend_too_big": (
        "If the close didn't fall by about the dividend, the amount was misread, for example"
        " paise as rupees: reject. Approve only if the price really fell that much."
    ),
    "other": "The rules have no advice for this one.",
}


def adjustment_effects(row: dict[str, Any], factor: float | None) -> tuple[str, str]:
    """What approve and what reject do, in words."""
    when = _date(row["date"])
    approve = (
        f"Every close before {when} is multiplied by {_num(factor)}, so the history lines up"
        " with the price after it. The bars are rebuilt on the next run."
        if factor
        else "There is no factor to apply, so approving will be refused."
    )
    reject = (
        "The history stays as it traded, so the jump stays in the adjusted prices as a real"
        " move. A note saying why is needed."
    )
    return approve, reject


CHANGE_TITLES = {
    "new_listing": "New listing",
    "symbol_change": "Symbol change",
    "isin_change": "ISIN change",
    "merge": "Merge",
    "index_rename": "Index rename",
}


def change_category(row: dict[str, Any]) -> Category:
    kind = row["change_kind"]
    if kind == "merge":
        return "possible_merge"
    if kind == "index_rename":
        return "possible_rename"
    return "other"


def change_title(row: dict[str, Any]) -> str:
    kind = str(row["change_kind"])
    if row["status"] == "pending" and kind == "merge" and row["proposed_by"] == "rules":
        return "Possible merge"
    if row["status"] == "pending" and kind == "index_rename":
        return "Possible rename"
    return CHANGE_TITLES.get(kind, kind)


def change_reason(row: dict[str, Any], candidate_symbols: list[str]) -> str:
    """Why a listing change waits. The rules raise two kinds (spec 0002, 5.2.2 and 5.5): a
    symbol back with a new ISIN after a long silence, and a new index name."""
    kind, payload = row["change_kind"], row["payload"]
    symbol = row["symbol"] or f"listing {row['listing_id']}"
    others = ", ".join(candidate_symbols) or "another listing"
    when = _date(row["date"])
    if kind == "merge" and row["proposed_by"] == "rules":
        return (
            f"{symbol} came back on {when} with a new ISIN after more than 20 sessions without a"
            f" trade, so it was made a new listing. Approve to merge it into {others} if it is"
            " the same security; reject to keep the two apart."
        )
    if kind == "merge":
        return (
            f"{row['proposed_by']} proposed merging {symbol} into {others}. Approve to merge;"
            " reject to keep the two apart."
        )
    if kind == "index_rename":
        name = payload.get("new_name") or symbol
        return (
            f"A new index name, {name}, appeared on {when} as {others} stopped. If it is a"
            " rename, approve it with the index it renames; otherwise reject it to keep it as a"
            " new index."
        )
    who = "The rules" if row["proposed_by"] == "rules" else row["proposed_by"]
    return f"{who} proposed a {kind.replace('_', ' ')} on {symbol} from {when}."


CHANGE_GUIDES: dict[Category, str] = {
    "possible_merge": (
        "Compare the two lines on the chart. If the new listing starts near where the old one"
        " stopped, and the names match, it is the same security under a new ISIN: approve. A"
        " different company that reused the symbol starts at an unrelated price: reject."
    ),
    "possible_rename": (
        "An index that was renamed carries on at the level the old one stopped at. Pick the old"
        " index whose line the new one continues, and approve. If none continues it, reject:"
        " it is a new index."
    ),
    "other": "The rules have no advice for this one.",
}


def change_effects(row: dict[str, Any], others: list[str]) -> tuple[str, str]:
    symbol = row["symbol"] or f"listing {row['listing_id']}"
    target = ", ".join(others) or "the other listing"
    if row["change_kind"] == "index_rename":
        approve = (
            f"{symbol} is merged into the index you pick, so its history carries on under the"
            " new name."
        )
        reject = f"{symbol} stays a new index, with its own history from {_date(row['date'])}."
    else:
        approve = (
            f"{symbol} is merged into {target}: its symbol and ISIN periods move across, and one"
            " listing carries the whole history."
        )
        reject = f"{symbol} and {target} stay two listings, each with its own history."
    return approve, reject


def _date(d: date) -> str:
    return d.strftime("%d %b %Y")
