"""Apply the repository's SQL migrations (db/migrations) with yoyo.

Run as the owner role (cagr_platform). yoyo's own bookkeeping tables live in schema `yoyo`,
which this creates: on PostgreSQL 15 and later an ordinary role cannot create tables in
`public` (on Azure it is owned by azure_pg_admin), and every migration names its schema anyway.

    uv run python -m cagr_data.core.migrate            # uses CAGR_DATABASE_URL
    uv run python -m cagr_data.core.migrate --list     # show applied and pending
"""

import argparse
import sys
from pathlib import Path

from yoyo import get_backend, read_migrations
from yoyo.backends import DatabaseBackend

from cagr_data.core.settings import Settings

MIGRATIONS_DIR = Path(__file__).resolve().parents[4] / "db" / "migrations"
YOYO_SCHEMA = "yoyo"


def _yoyo_url(database_url: str) -> str:
    """yoyo picks its driver from the URL scheme (we use psycopg 3), and its `schema` parameter
    sets the search_path its own tables are created in."""
    for prefix in ("postgresql://", "postgres://"):
        if database_url.startswith(prefix):
            database_url = "postgresql+psycopg://" + database_url.removeprefix(prefix)
            break
    separator = "&" if "?" in database_url else "?"
    return f"{database_url}{separator}schema={YOYO_SCHEMA}"


def _backend(database_url: str) -> DatabaseBackend:
    backend = get_backend(_yoyo_url(database_url))
    backend.execute(f"CREATE SCHEMA IF NOT EXISTS {YOYO_SCHEMA}")
    backend.commit()
    backend.init_database()  # get_backend made yoyo's lock table before the schema existed
    return backend


def pending(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    backend = _backend(database_url)
    migrations = read_migrations(str(migrations_dir))
    return [m.id for m in backend.to_apply(migrations)]


def apply(database_url: str, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every pending migration in order, each in its own transaction.

    Returns the ids applied, which is empty when the database is already current.
    """
    backend = _backend(database_url)
    migrations = read_migrations(str(migrations_dir))
    with backend.lock():
        to_apply = backend.to_apply(migrations)
        backend.apply_migrations(to_apply)
    return [m.id for m in to_apply]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="list pending migrations only")
    args = parser.parse_args(argv)

    settings = Settings()
    if settings.database_url is None:
        print("CAGR_DATABASE_URL is not set", file=sys.stderr)
        return 2
    url = settings.database_url.get_secret_value()

    ids = pending(url) if args.list else apply(url)
    verb = "pending" if args.list else "applied"
    print(f"{len(ids)} migration(s) {verb}" + "".join(f"\n  {i}" for i in ids))
    return 0


if __name__ == "__main__":
    sys.exit(main())
