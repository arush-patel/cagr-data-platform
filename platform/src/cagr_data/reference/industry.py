"""Seed cagr_industry from the old master's export (spec 0006, section 5.1).

    uv run python -m cagr_data.reference.industry db/seeds/group3_2026-10-27.csv

The seed is `symbol,group_3`, read once from stockdb's script_master. Each symbol is matched to
the listing that held it on the export date, or, for a stock that has since been delisted or
renamed, to the last listing that held it. A value that is not one of the industries in
ref.industry (the coworker's coarser groups, typos) is not loaded: if the stock enters the
universe it becomes a review item like any unclassified stock. Symbols without a listing are
reported, not guessed. A listing that already has an industry is left alone, so a rerun adds
nothing.
"""

import csv
import io
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from psycopg.types.json import Jsonb

from cagr_data.core.db import Connection, connect
from cagr_data.core.settings import Settings
from cagr_data.quality.checks import Outcome

EXPORTED = date(2026, 10, 27)
SOURCE = "stockdb.script_master.group_3"


@dataclass
class SeedResult:
    applied: int = 0
    already: int = 0
    by_last_holder: int = 0
    not_an_industry: dict[str, list[str]] = field(default_factory=dict)  # value -> symbols
    no_listing: list[str] = field(default_factory=list)


def read_seed(content: str) -> list[tuple[str, str]]:
    """(symbol, industry) pairs; the header must be symbol,group_3."""
    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)
    if header != ["symbol", "group_3"]:
        raise ValueError(f"expected the header symbol,group_3, found {header}")
    rows = [(r[0].strip(), r[1].strip()) for r in reader if r]
    symbols = [s for s, _ in rows]
    if len(set(symbols)) != len(symbols):
        raise ValueError("a symbol appears twice in the seed")
    return rows


def _listing(conn: Connection, symbol: str, as_of: date) -> tuple[int, bool] | None:
    """(listing_id, matched on as_of) for symbol, falling back to its last holder."""
    row = conn.execute(
        """
        SELECT s.listing_id, s.valid_from <= %(d)s AND (s.valid_to IS NULL OR s.valid_to > %(d)s)
          FROM ref.listing_symbol s JOIN ref.listing l USING (listing_id)
         WHERE s.symbol = %(s)s AND l.status <> 'merged' AND l.asset_class = 'equity'
         ORDER BY 2 DESC, s.valid_from DESC
         LIMIT 1
        """,
        {"s": symbol, "d": as_of},
    ).fetchone()
    return (int(row[0]), bool(row[1])) if row else None


def load_seed(
    conn: Connection, rows: list[tuple[str, str]], *, file: str, as_of: date = EXPORTED
) -> SeedResult:
    industries: dict[str, int] = dict(
        conn.execute("SELECT name, industry_id FROM ref.industry WHERE active").fetchall()
    )
    result = SeedResult()
    for symbol, value in rows:
        industry_id = industries.get(value)
        if industry_id is None:
            result.not_an_industry.setdefault(value, []).append(symbol)
            continue
        found = _listing(conn, symbol, as_of)
        if found is None:
            result.no_listing.append(symbol)
            continue
        listing_id, current = found
        has = conn.execute(
            "SELECT 1 FROM ref.listing_industry WHERE listing_id = %s", (listing_id,)
        ).fetchone()
        if has:
            result.already += 1
            continue
        evidence = {"source": SOURCE, "file": file, "symbol": symbol,
                    "matched": "on_export_date" if current else "last_holder"}  # fmt: skip
        change = conn.execute(
            """
            INSERT INTO ref.industry_change (listing_id, industry_id, effective_date, status,
                                             proposed_by, evidence)
            SELECT listing_id, %s, first_session, 'applied', 'seed', %s
              FROM ref.listing WHERE listing_id = %s
            RETURNING change_id
            """,
            (industry_id, Jsonb(evidence), listing_id),
        ).fetchone()
        assert change is not None
        conn.execute(
            "INSERT INTO ref.listing_industry (listing_id, industry_id, change_id)"
            " VALUES (%s, %s, %s)",
            (listing_id, industry_id, change[0]),
        )
        result.applied += 1
        result.by_last_holder += not current
    return result


def industry_known(conn: Connection) -> Outcome:
    """Every listing's industry is an active ref.industry row (spec 0006, section 6)."""
    rows = conn.execute(
        """
        SELECT li.listing_id, i.name FROM ref.listing_industry li
          JOIN ref.industry i USING (industry_id)
         WHERE NOT i.active ORDER BY 1
        """
    ).fetchall()
    if not rows:
        return Outcome("industry_known", True)
    sample = [f"{lid}/{name}" for lid, name in rows[:20]]
    return Outcome("industry_known", False, {"count": len(rows), "sample": sample},
                   f"{len(rows)} listings on an inactive industry")  # fmt: skip


def main(argv: list[str] | None = None) -> int:
    if not argv or len(argv) != 1:
        print("usage: python -m cagr_data.reference.industry <seed.csv>", file=sys.stderr)
        return 2
    path = Path(argv[0])
    rows = read_seed(path.read_text(encoding="utf-8"))
    secret = Settings().pipeline_database_url
    if secret is None:
        print("CAGR_PIPELINE_DATABASE_URL is not set", file=sys.stderr)
        return 2
    with connect(secret.get_secret_value()) as conn:
        result = load_seed(conn, rows, file=path.name)
        conn.commit()
    skipped = sum(len(v) for v in result.not_an_industry.values())
    print(f"{result.applied} industries applied ({result.by_last_holder} to a listing that has"
          f" since changed symbol or delisted), {result.already} already set, {skipped} not one"
          f" of the industries, {len(result.no_listing)} with no listing")  # fmt: skip
    for value, symbols in sorted(result.not_an_industry.items(), key=lambda kv: -len(kv[1])):
        print(f"  not an industry: {value!r} on {len(symbols)} symbols")
    if result.no_listing:
        print(f"  no listing: {', '.join(result.no_listing)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
