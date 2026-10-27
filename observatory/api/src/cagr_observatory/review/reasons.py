"""The title and the one sentence that say why an item is waiting (spec 0005, section 4.2)."""

from datetime import date
from fractions import Fraction
from typing import Any

X = "\N{MULTIPLICATION SIGN}"
PRICE_CHECKED = {"split", "bonus", "consolidation"}
CLEAN_RATIOS = [Fraction(n, d) for n, d in [(1, 2), (2, 3), (1, 3), (1, 4), (1, 5), (1, 10)]]
CLEAN_RATIOS += [1 / r for r in CLEAN_RATIOS]


def nearest_clean_ratio(observed: float, tolerance: float = 0.05) -> Fraction | None:
    """The clean ratio (1/2, 2/3, 1/10, 10 and so on) within tolerance of observed, if any."""
    best = min(CLEAN_RATIOS, key=lambda r: abs(observed / float(r) - 1))
    return best if abs(observed / float(best) - 1) <= tolerance else None


def _num(x: float | None) -> str:
    return "?" if x is None else f"{x:.4g}"


def adjustment_title(row: dict[str, Any]) -> str:
    kind = row["action_kind"]
    if kind is None:
        observed = row["observed_factor"]
        return "Unexplained jump" + (f" {X}{_num(observed)}" if observed else "")
    if row["ratio_new"] is not None and row["ratio_old"] is not None:
        return f"{kind.capitalize()} {row['ratio_new']:g}:{row['ratio_old']:g}"
    return str(kind).capitalize()


def adjustment_reason(row: dict[str, Any]) -> str:
    kind = row["action_kind"]
    observed, expected = row["observed_factor"], row["expected_factor"]
    if kind is None:
        text = f"The close moved {X}{_num(observed)} and no NSE announcement explains it."
        ratio = nearest_clean_ratio(observed) if observed else None
        if ratio is not None:
            text += f" That is close to {ratio.numerator}/{ratio.denominator}."
        if row["isin_before"] and row["isin_on"] and row["isin_before"] != row["isin_on"]:
            text += " The ISIN changed the same day, as it does on most splits."
        else:
            text += " The ISIN didn't change, which often means a re-listing or a series move."
        return text
    subject = f"NSE announced “{row['subject']}”."
    if kind in PRICE_CHECKED and observed and expected:
        apart = abs(observed / expected - 1)
        return (
            f"{subject} That predicts a factor of {_num(expected)}, but the close moved"
            f" {X}{_num(observed)}, {apart:.0%} apart, so the rules stopped."
        )
    if kind == "demerger" or kind == "other":
        return (
            f"{subject} A {kind} is always confirmed by a person. The factor"
            f" {_num(row['factor'])} comes from prices: the ex-date's price over the close"
            " before it."
        )
    return f"{subject} The rules couldn't confirm a factor from the prices."


CHANGE_TITLES = {
    "new_listing": "New listing",
    "symbol_change": "Symbol change",
    "isin_change": "ISIN change",
    "merge": "Merge",
    "index_rename": "Index rename",
}


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


def _date(d: date) -> str:
    return d.strftime("%d %b %Y")
