"""Dagster resources: the database, the raw file storage and the NSE client."""

from pathlib import Path
from types import TracebackType
from typing import Any

import dagster as dg
import psycopg

from cagr_data.core.db import Connection, connect
from cagr_data.core.storage import LocalStorage
from cagr_data.vendors.http import VendorClient


class _KeptConnection(psycopg.Connection[tuple[Any, ...]]):
    """A connection whose `with` block commits or rolls back but leaves it open."""

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self.closed:
            return
        if exc_type is None:
            self.commit()
        elif not self.broken:
            self.rollback()


_KEPT: dict[str, _KeptConnection] = {}


class PlatformDatabase(dg.ConfigurableResource["PlatformDatabase"]):
    """The platform database, connected as the pipeline role (cagr_writer).

    With reuse, every asset in this process shares one connection, opened again only after it
    broke. A backfill sets it: over the laptop's tunnel a new connection costs about half a
    second, and a backfill day opened several.
    """

    url: str
    reuse: bool = False

    def connect(self) -> Connection:
        # Inside a Dagster run the EnvVar is resolved to its value; a direct call (the backfill's
        # reachability probe) sees the EnvVar itself, whose str is the variable's name.
        url = self.url.get_value() if isinstance(self.url, dg.EnvVar) else self.url
        if url is None:
            raise RuntimeError(f"{self.url} is not set")
        if not self.reuse:
            return connect(url)
        conn = _KEPT.get(url)
        if conn is None or conn.closed or conn.broken:
            if conn is not None:
                conn.close()
            conn = _KEPT[url] = _KeptConnection.connect(url)
        return conn


class RawStorage(dg.ConfigurableResource["RawStorage"]):
    """Where original files are kept: a local folder now, Azure Blob once deployed."""

    root: str

    def storage(self) -> LocalStorage:
        return LocalStorage(Path(self.root))


class NseClient(dg.ConfigurableResource["NseClient"]):
    """One polite client per run: 1 request a second by default (spec 0001, section 4)."""

    rate_limit_per_s: float = 1.0

    def client(self) -> VendorClient:
        return VendorClient(self.rate_limit_per_s)
