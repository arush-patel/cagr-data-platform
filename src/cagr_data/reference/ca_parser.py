"""Turn NSE's free-text corporate-action subjects into typed actions (D3).

The subject is free text: 'Bonus 2:5', 'Face Value Split (Sub-Division) - From Rs 10/- Per Share
To Rs 2/- Per Share', 'Interim Dividend - Rs 5 Per Share', 'Bonus Ncrps 4:1'. One subject can hold
several actions separated by '/'. Anything with a price effect that the rules don't recognise is
returned as needs_review rather than guessed; text with no price effect (meetings, interest) is
ignored.

price_factor multiplies every price BEFORE the ex-date: a 1:5 split is 1/5, a 1:4 bonus 4/5.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction


@dataclass(frozen=True)
class ParsedAction:
    kind: str  # split | bonus | consolidation | rights | demerger | dividend | other
    parse_status: str  # parsed | needs_review
    ratio_new: Decimal | None = None
    ratio_old: Decimal | None = None
    cash_amount: float | None = None

    @property
    def price_factor(self) -> float:
        if self.ratio_new is not None and self.ratio_old is not None:
            return float(self.ratio_old / self.ratio_new)
        return 1.0


_MONEY = r"(?:rs\.?|re\.?|inr|₹)\s*([0-9]+(?:\.[0-9]+)?)"
_RATIO = r"([0-9]+(?:\.[0-9]+)?)\s*:\s*([0-9]+(?:\.[0-9]+)?)"

# Text with no effect on price or share count.
_IGNORE = re.compile(
    r"annual general meeting|\bagm\b|\begm\b|extra ?ordinary general meeting|interest payment|"
    r"redemption|e-?voting|book closure|postal ballot|scheme meeting|"
    r"income distribution|capital distribution \(invit\)",
    re.I,
)

# Words that mean the share count or price basis may change: never ignore these silently.
_PRICE_EFFECT = re.compile(
    r"split|sub-?division|bonus|rights|demerger|arrangement|amalgamation|merger|consolidation|"
    r"reduction|buy ?back|spin|capital",
    re.I,
)


def _ratio(new: Fraction) -> tuple[Decimal, Decimal]:
    return Decimal(new.numerator), Decimal(new.denominator)


def _parse_part(text: str) -> ParsedAction | None:
    t = " ".join(text.split())
    low = t.lower()

    if re.search(r"bonus", low):
        if re.search(r"ncrps|preference|pref\b|debenture|ncd", low):
            # A bonus of preference shares or debentures: no new equity, no price effect.
            return ParsedAction("other", "parsed")
        m = re.search(_RATIO, t)
        if not m:
            return ParsedAction("bonus", "needs_review")
        a, b = Fraction(m.group(1)), Fraction(m.group(2))
        if a <= 0 or b <= 0:
            return ParsedAction("bonus", "needs_review")
        # a new for every b held: b shares become a + b
        new, old = _ratio((a + b) / b)
        return ParsedAction("bonus", "parsed", ratio_new=new, ratio_old=old)

    if re.search(r"split|sub-?division|consolidation", low):
        vals = [Fraction(v) for v in re.findall(_MONEY, low)]
        if len(vals) != 2 or vals[0] <= 0 or vals[1] <= 0 or vals[0] == vals[1]:
            return ParsedAction("split", "needs_review")
        fv_from, fv_to = vals
        if fv_to < fv_from:  # Rs 10 → Rs 2: each share becomes five
            new, old = _ratio(fv_from / fv_to)
            return ParsedAction("split", "parsed", ratio_new=new, ratio_old=old)
        old_n, new_n = _ratio(fv_to / fv_from)  # Rs 1 → Rs 10: ten shares become one
        return ParsedAction("consolidation", "parsed", ratio_new=new_n, ratio_old=old_n)

    if re.search(r"\brights?\b", low):
        # Needs the issue price and the cum-rights close for the theoretical ex-rights price.
        return ParsedAction("rights", "needs_review")

    if re.search(r"demerger|spin ?off", low):
        return ParsedAction("other", "needs_review")

    if re.search(r"dividend|distribution", low) and not _IGNORE.search(low):
        amounts = re.findall(_MONEY, low)
        if not amounts:
            # 'Dividend - 50%' is a percentage of face value; resolve it by hand.
            return ParsedAction("dividend", "needs_review")
        return ParsedAction("dividend", "parsed", cash_amount=float(sum(Fraction(a) for a in amounts)))

    if _IGNORE.search(low) and not _PRICE_EFFECT.search(low):
        return None
    if _PRICE_EFFECT.search(low):
        return ParsedAction("other", "needs_review")
    return None


def parse_subject(subject: str) -> list[ParsedAction]:
    """All actions in one subject. Dividends in the same subject are summed into one."""
    parts = [p for p in re.split(r"\s/\s|/(?=\s*[A-Za-z])", subject) if p.strip()]
    actions = [a for a in (_parse_part(p) for p in parts) if a is not None]
    divs = [a for a in actions if a.kind == "dividend"]
    if len(divs) > 1:
        if all(d.parse_status == "parsed" for d in divs):
            total = sum(d.cash_amount or 0.0 for d in divs)
            merged = ParsedAction("dividend", "parsed", cash_amount=total)
        else:
            merged = ParsedAction("dividend", "needs_review")
        actions = [a for a in actions if a.kind != "dividend"] + [merged]
    # One row per kind per ex-date in clean.corporate_action: two of a kind in one subject is ambiguous.
    seen: dict[str, ParsedAction] = {}
    for a in actions:
        if a.kind in seen:
            seen[a.kind] = ParsedAction(a.kind, "needs_review")
        else:
            seen[a.kind] = a
    return list(seen.values())
