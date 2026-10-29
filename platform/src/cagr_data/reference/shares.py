"""Shares outstanding per listing, in periods (spec 0007, sections 5.2 and 5.3).

    uv run python -m cagr_data.reference.shares

Reported counts come from NSE's mcap file (from 1 Feb 2024) and the index files' ISSUE_CAP
(2010 to 2013). Runs of the same reported count become periods. Every day without a report is
filled by walking from the nearest report through the listing's applied corporate actions:
backwards from the next later report, or forwards from the last one when none comes later.
Splits, consolidations and bonuses change the count by their ratio, rights by their ratio as if
fully taken up, and an applied clean-ratio jump with no action by its factor. Across a gap of a
quarter or more between two reports (2013 to Feb 2024), the change the actions don't explain (a
fresh issue, a buyback) is spread evenly in log terms, a step each quarter, as source `spread`.
The periods are rebuilt as a whole each run, in the caller's transaction.
"""

import sys
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from psycopg.types.json import Jsonb

from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings
from cagr_data.quality.checks import SAMPLE, Outcome
from cagr_data.reference.listings import LISTING_SERIES

STRUCTURAL = frozenset({"split", "consolidation", "bonus"})
DAY = timedelta(days=1)
SPREAD_FROM = timedelta(days=90)  # a shorter gap between reports keeps the walk-back


@dataclass(frozen=True)
class Run:
    """Consecutive reports of the same count."""

    start: date
    end: date
    shares: int
    source: str  # nse_mcap or index_file


@dataclass(frozen=True)
class Event:
    """An applied change of the share basis: shares after = shares before times multiplier."""

    ex_date: date
    multiplier: float


@dataclass(frozen=True)
class Period:
    valid_from: date
    valid_to: date | None
    shares: int
    source: str
    evidence: dict[str, Any] = field(default_factory=dict)


def multiplier(kind: str | None, ratio_new: Decimal | None, ratio_old: Decimal | None,
               factor: float) -> float | None:  # fmt: skip
    """How an action changes the share count, or None when it doesn't."""
    if kind is None:  # a clean-ratio jump applied without an action (Q5)
        return 1 / factor
    if ratio_new is None or ratio_old is None:
        return None
    new, old = float(ratio_new), float(ratio_old)
    if kind in ("split", "consolidation"):  # face value old -> new
        return old / new
    if kind in ("bonus", "rights"):  # a new for every b held
        return (new + old) / old
    return None


def _walk_back(shares: float, events: Sequence[Event]) -> list[tuple[date, int]]:
    """From a count known on and after the last event, the count from each event's ex-date
    back to the previous one, latest first: [(ex_date, shares from ex_date)]."""
    out = []
    for e in sorted(events, key=lambda e: e.ex_date, reverse=True):
        out.append((e.ex_date, round(shares)))
        shares /= e.multiplier
    out.append((date.min, round(shares)))
    return out


def _quarter_starts(lo: date, hi: date) -> list[date]:
    """The first days of quarters strictly between lo and hi."""
    out = []
    q = date(lo.year, (lo.month - 1) // 3 * 3 + 1, 1)
    while q < hi:
        if q > lo:
            out.append(q)
        q = date(q.year + q.month // 10, (q.month + 2) % 12 + 1, 1)
    return out


def _spread(run: Run, nxt: Run, gap: Sequence[Event], ratio: float) -> list[Period]:
    """From the end of one report to the next: the known actions, and the change they don't
    explain (ratio) growing evenly in log terms, so the count meets the next report."""
    span = (nxt.start - run.end).days
    bounds = sorted({e.ex_date for e in gap} | set(_quarter_starts(run.end, nxt.start)))
    out = [Period(run.start, bounds[0] if bounds else nxt.start, run.shares, run.source,
                  {"next_report_ratio": ratio})]  # fmt: skip
    for i, b in enumerate(bounds):
        known = 1.0
        for e in gap:
            if e.ex_date <= b:
                known *= e.multiplier
        weight = (b - run.end).days / span
        out.append(Period(b, bounds[i + 1] if i + 1 < len(bounds) else nxt.start,
                          round(run.shares * known * ratio**weight), "spread",
                          {"spread_ratio": round(ratio, 6)}))  # fmt: skip
    return out


def build_periods(first_session: date, runs: Sequence[Run], events: Sequence[Event],
                  pending: Sequence[date] = ()) -> list[Period]:  # fmt: skip
    """A listing's periods from its reported runs and its share events."""
    if not runs:
        return []
    runs = sorted(runs, key=lambda r: r.start)
    events = sorted(events, key=lambda e: e.ex_date)
    out: list[Period] = []

    def between(lo: date, hi: date) -> list[Event]:  # ex-dates in (lo, hi]
        return [e for e in events if lo < e.ex_date <= hi]

    # Before the first report: walk back from it.
    first = runs[0]
    steps = _walk_back(first.shares, between(first_session, first.start))
    bounds = [s[0] for s in steps[:-1]]  # ex-dates, latest first
    starts = [first_session, *reversed(bounds)]
    values = [s[1] for s in reversed(steps)]
    for i, (start, value) in enumerate(zip(starts, values, strict=True)):
        end = starts[i + 1] if i + 1 < len(starts) else first.start
        if start < end:
            out.append(Period(start, end, value, "walk_back"))

    for i, run in enumerate(runs):
        nxt = runs[i + 1] if i + 1 < len(runs) else None
        if nxt is None:
            # After the last report: walk forward.
            shares = float(run.shares)
            start, source = run.start, run.source
            for e in between(run.end, date.max):
                out.append(Period(start, e.ex_date, round(shares), source))
                shares *= e.multiplier
                start, source = e.ex_date, "walk_back"
            out.append(Period(start, None, round(shares), source))
            break
        gap = between(run.end, nxt.start)
        counts = dict(_walk_back(nxt.shares, gap))  # ex-date -> count from it on
        # The count the next report walks back to; a difference is a change no action
        # explains (a fresh issue, a buyback), placed where the gap's first event or the next
        # report begins.
        walked = counts[date.min]
        if walked != run.shares and nxt.start - run.end >= SPREAD_FROM:
            out.extend(_spread(run, nxt, gap, walked / run.shares))
            continue
        evidence = {} if walked == run.shares else {"next_report_ratio": walked / run.shares}
        ex_dates = [e.ex_date for e in gap]
        out.append(Period(run.start, ex_dates[0] if gap else nxt.start, run.shares, run.source,
                          evidence))  # fmt: skip
        for j, ex in enumerate(ex_dates):
            end = ex_dates[j + 1] if j + 1 < len(ex_dates) else nxt.start
            out.append(Period(ex, end, counts[ex], "walk_back"))

    return _mark_pending(_merge(out), pending)


def _merge(periods: Iterable[Period]) -> list[Period]:
    out: list[Period] = []
    for p in periods:
        if p.valid_to is not None and p.valid_to <= p.valid_from:
            continue
        last = out[-1] if out else None
        if last and (last.shares, last.source) == (p.shares, p.source) and not last.evidence:
            out[-1] = Period(last.valid_from, p.valid_to, p.shares, p.source, p.evidence)
        else:
            out.append(p)
    return out


def _mark_pending(periods: list[Period], pending: Sequence[date]) -> list[Period]:
    out = []
    for p in periods:
        inside = [d.isoformat() for d in pending
                  if p.valid_from <= d and (p.valid_to is None or d < p.valid_to)]  # fmt: skip
        if inside and p.source == "walk_back":
            p = Period(p.valid_from, p.valid_to, p.shares, p.source,
                       {**p.evidence, "pending_adjustments": inside})  # fmt: skip
        out.append(p)
    return out


def _runs(conn: Connection) -> dict[int, list[Run]]:
    rows = conn.execute(
        """
        WITH mcap AS (
            SELECT s.listing_id, m.trade_date AS d, max(m.issue_size) AS shares,
                   'nse_mcap' AS source
              FROM raw.nse_mcap_current m
              JOIN ref.listing_symbol s
                ON s.symbol = m.symbol AND s.valid_from <= m.trade_date
               AND (s.valid_to IS NULL OR s.valid_to > m.trade_date)
              JOIN ref.listing l ON l.listing_id = s.listing_id
             WHERE m.series = ANY(%s) AND m.issue_size > 0 AND l.asset_class <> 'index'
             GROUP BY 1, 2),
        ix AS (
            SELECT s.listing_id, i.trade_date AS d,
                   (array_agg(i.issue_cap ORDER BY i.source = 'ffix' DESC, i.issue_cap DESC))[1]
                       AS shares,
                   'index_file' AS source
              FROM raw.nse_index_issue_cap_current i
              JOIN ref.listing_symbol s
                ON s.symbol = i.symbol AND s.valid_from <= i.trade_date
               AND (s.valid_to IS NULL OR s.valid_to > i.trade_date)
              JOIN ref.listing l ON l.listing_id = s.listing_id
             WHERE i.issue_cap > 0 AND l.asset_class <> 'index'
             GROUP BY 1, 2),
        obs AS (
            SELECT * FROM mcap
            UNION ALL
            SELECT * FROM ix WHERE NOT EXISTS (
                SELECT 1 FROM mcap WHERE mcap.listing_id = ix.listing_id AND mcap.d = ix.d)),
        marked AS (
            SELECT *, CASE WHEN (shares, source) IS NOT DISTINCT FROM
                               (lag(shares) OVER w, lag(source) OVER w) THEN 0 ELSE 1 END AS new
              FROM obs WINDOW w AS (PARTITION BY listing_id ORDER BY d)),
        grouped AS (
            SELECT *, sum(new) OVER (PARTITION BY listing_id ORDER BY d) AS grp FROM marked)
        SELECT listing_id, min(d), max(d), shares, source
          FROM grouped GROUP BY listing_id, grp, shares, source
         ORDER BY 1, 2
        """,
        (sorted(LISTING_SERIES),),
    ).fetchall()
    out: dict[int, list[Run]] = defaultdict(list)
    for listing_id, start, end, shares, source in rows:
        out[listing_id].append(Run(start, end, int(shares), source))
    return out


def _events(conn: Connection) -> tuple[dict[int, list[Event]], dict[int, list[date]]]:
    rows = conn.execute(
        """
        SELECT p.listing_id, p.ex_date, p.status, p.factor, c.kind, c.ratio_new, c.ratio_old,
               p.action_id IS NULL
          FROM ref.price_adjustment p
          LEFT JOIN ref.corporate_action c
            ON c.listing_id = p.listing_id AND c.ex_date = p.ex_date
           AND c.kind IN ('split', 'consolidation', 'bonus', 'rights')
           AND c.parse_status = 'parsed'
         WHERE p.status IN ('applied', 'pending')
         ORDER BY 1, 2
        """
    ).fetchall()
    events: dict[int, dict[date, float]] = defaultdict(dict)
    pending: dict[int, list[date]] = defaultdict(list)
    for listing_id, ex_date, status, factor, kind, new, old, no_action in rows:
        if status == "pending":
            if ex_date not in pending[listing_id]:
                pending[listing_id].append(ex_date)
            continue
        if kind is None and not no_action:
            continue  # a dividend or rights-only factor with no share change found
        m = multiplier(kind, new, old, factor)
        if m is not None and m != 1:
            events[listing_id][ex_date] = events[listing_id].get(ex_date, 1.0) * m
    return (
        {k: [Event(d, m) for d, m in v.items()] for k, v in events.items()},
        pending,
    )


@dataclass
class SharesResult:
    listings: int = 0
    periods: int = 0
    by_source: dict[str, int] = field(default_factory=dict)


def build_shares(conn: Connection) -> SharesResult:
    """Rebuild ref.listing_shares for every listing with a reported count."""
    runs = _runs(conn)
    events, pending = _events(conn)
    first: dict[int, date] = dict(conn.execute(
        "SELECT listing_id, first_session FROM ref.listing WHERE status <> 'merged'"
    ).fetchall())  # fmt: skip
    result = SharesResult()
    rows = []
    for listing_id, listing_runs in runs.items():
        if listing_id not in first:
            continue
        periods = build_periods(first[listing_id], listing_runs, events.get(listing_id, []),
                                pending.get(listing_id, []))  # fmt: skip
        result.listings += 1
        for p in periods:
            result.by_source[p.source] = result.by_source.get(p.source, 0) + 1
            rows.append((listing_id, p.valid_from, p.valid_to, p.shares, p.source,
                         Jsonb(p.evidence)))  # fmt: skip
    conn.execute("DELETE FROM ref.listing_shares")
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO ref.listing_shares (listing_id, valid_from, valid_to, shares, source,"
            " evidence) VALUES (%s, %s, %s, %s, %s, %s)",
            rows,
        )
    result.periods = len(rows)
    return result


# Checks (spec 0007, section 5.4).


def _outcome(check_id: str, rows: Sequence[tuple[Any, ...]], what: str) -> Outcome:
    if not rows:
        return Outcome(check_id, True)
    sample = ["/".join(str(v) for v in r) for r in rows[:SAMPLE]]
    return Outcome(check_id, False, {"count": len(rows), "sample": sample}, f"{len(rows)} {what}")


def run_checks(
    conn: Connection, start: date, end: date, params: dict[str, dict[str, Any]]
) -> list[Outcome]:
    out = []
    rows = conn.execute(
        """
        SELECT m.trade_date, m.symbol, m.series FROM raw.nse_mcap_current m
         WHERE m.trade_date BETWEEN %s AND %s AND m.series = ANY(%s)
           AND NOT EXISTS (SELECT 1 FROM ref.listing_symbol s
                            WHERE s.symbol = m.symbol AND s.valid_from <= m.trade_date
                              AND (s.valid_to IS NULL OR s.valid_to > m.trade_date))
         ORDER BY 1, 2
        """,
        (start, end, sorted(LISTING_SERIES)),
    ).fetchall()
    out.append(_outcome("mcap_rows_mapped", rows, "mcap rows with no listing"))

    rows = conn.execute(
        """
        SELECT b.listing_id, max(b.date), max(b.value_traded) FROM clean.bar_1d b
          JOIN ref.listing l ON l.listing_id = b.listing_id AND l.asset_class = 'equity'
         WHERE b.date BETWEEN %s AND %s
           AND NOT EXISTS (SELECT 1 FROM ref.listing_shares s WHERE s.listing_id = b.listing_id)
         GROUP BY 1 ORDER BY 3 DESC NULLS LAST
        """,
        (start, end),
    ).fetchall()
    out.append(_outcome("shares_present", rows, "traded equity listings with no share count"))

    tolerance = params.get("walk_back_vs_index", {}).get("tolerance", 0.05)
    rows = conn.execute(
        """
        SELECT listing_id, valid_from, round((evidence->>'next_report_ratio')::numeric, 3)
          FROM ref.listing_shares
         WHERE source = 'index_file' AND evidence ? 'next_report_ratio'
           AND abs((evidence->>'next_report_ratio')::numeric - 1) > %s
         ORDER BY 1, 2
        """,
        (tolerance,),
    ).fetchall()
    out.append(_outcome("walk_back_vs_index", rows, "index counts the walk-back disagrees with"))
    return out


def mcap_matches_nse(conn: Connection, start: date, end: date, tolerance: float) -> Outcome:
    rows = conn.execute(
        """
        SELECT m.trade_date, m.symbol, round(c.market_cap_inr / m.market_cap_inr, 4)
          FROM raw.nse_mcap_current m
          JOIN ref.listing_symbol s
            ON s.symbol = m.symbol AND s.valid_from <= m.trade_date
           AND (s.valid_to IS NULL OR s.valid_to > m.trade_date)
          JOIN clean.market_cap_1d c ON c.listing_id = s.listing_id AND c.date = m.trade_date
         WHERE m.trade_date BETWEEN %s AND %s AND m.last_trade_date = m.trade_date
           AND m.series = ANY(%s) AND m.market_cap_inr > 0
           AND abs(c.market_cap_inr / m.market_cap_inr - 1) > %s
         ORDER BY 1, 2
        """,
        (start, end, sorted(LISTING_SERIES), tolerance),
    ).fetchall()
    return _outcome("mcap_matches_nse", rows, "market caps that differ from NSE's")


def main(argv: list[str] | None = None) -> int:
    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        result = build_shares(conn)
        conn.commit()
    print(f"{result.listings} listings, {result.periods} share periods: {result.by_source}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
