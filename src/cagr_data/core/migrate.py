"""A small migration runner: numbered SQL files applied in order, each in one transaction.

An applied file is recorded with its checksum; editing it afterwards is refused, so a change
to the schema is always a new file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"

_BOOKKEEPING = """
CREATE SCHEMA IF NOT EXISTS ops;
CREATE TABLE IF NOT EXISTS ops.schema_migration (
    version     text PRIMARY KEY,
    filename    text NOT NULL,
    sha256      text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
"""


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text()

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.path.read_bytes()).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    found = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    versions = [p.name[:4] for p in found]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"duplicate migration numbers in {directory}")
    return [Migration(p.name[:4], p) for p in found]


def applied(conn: psycopg.Connection[Any]) -> dict[str, str]:
    conn.execute(_BOOKKEEPING)
    rows = conn.execute("SELECT version, sha256 FROM ops.schema_migration").fetchall()
    return {str(version): str(sha) for version, sha in rows}


def migrate(url: str, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration. Returns the versions applied."""
    done: list[str] = []
    with psycopg.connect(url) as conn:
        conn.execute("SELECT pg_advisory_lock(hashtext('cagr_data.migrate'))")
        have = applied(conn)
        conn.commit()
        for m in discover(directory):
            if m.version in have:
                if have[m.version] != m.sha256:
                    raise RuntimeError(
                        f"migration {m.path.name} changed after it was applied; add a new file"
                    )
                continue
            with conn.transaction():
                conn.execute(m.sql)  # a whole file of statements, no parameters
                conn.execute(
                    "INSERT INTO ops.schema_migration (version, filename, sha256) VALUES (%s, %s, %s)",
                    (m.version, m.path.name, m.sha256),
                )
            done.append(m.path.name)
        conn.execute("SELECT pg_advisory_unlock(hashtext('cagr_data.migrate'))")
    return done


def reset(url: str) -> None:
    """Drop everything this repo creates. For dev and test databases only."""
    with psycopg.connect(url, autocommit=True) as conn:
        dbname = conn.info.dbname
        if not (dbname.endswith("_dev") or dbname.endswith("_test")):
            raise RuntimeError(f"refusing to reset database {dbname!r}: only *_dev and *_test")
        conn.execute(
            "DROP SCHEMA IF EXISTS ops, ref, raw, clean, fb, ind, feat, sig, lbl CASCADE; "
            "DROP TABLE IF EXISTS public.run_log CASCADE"
        )
