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
        return f"Unexplained jump {X}{_num(row['observed_factor'])}"
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
    candidates = row["evidence"].get("candidates")
    if row["change_kind"] in ("new_listing", "index_rename") and candidates:
        return "Possible rename" if row["change_kind"] == "index_rename" else "Possible merge"
    kind = str(row["change_kind"])
    return CHANGE_TITLES.get(kind, kind)


def change_reason(row: dict[str, Any], candidate_symbols: list[str]) -> str:
    kind, payload, evidence = row["change_kind"], row["payload"], row["evidence"]
    symbol = row["symbol"] or f"listing {row['listing_id']}"
    others = ", ".join(candidate_symbols) or "another listing"
    when = _date(row["date"])
    if kind == "new_listing" and evidence.get("candidates"):
        return (
            f"{symbol} appeared on {when} as a new listing. It may be the same security as"
            f" {others}. Merge it if so; reject this item to keep it separate."
        )
    if kind == "index_rename" or (kind == "new_listing" and row["board"] is None):
        return f"A new index name, {symbol}, appeared on {when}. Is it a rename of {others}?"
    who = "The rules" if row["proposed_by"] == "rules" else row["proposed_by"]
    if kind == "merge":
        return f"{who} proposed merging {symbol} into {others} from {when}."
    if kind in ("symbol_change", "isin_change"):
        what = "symbol" if kind == "symbol_change" else "ISIN"
        return (
            f"{who} proposed changing the {what} of {symbol} from {payload.get('old', '?')}"
            f" to {payload.get('new', '?')} on {when}."
        )
    return f"{who} proposed a {kind.replace('_', ' ')} on {symbol} from {when}."


def _date(d: date) -> str:
    return d.strftime("%d %b %Y")
