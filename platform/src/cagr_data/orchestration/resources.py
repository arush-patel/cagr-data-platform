"""Dagster resources: the database, the raw file storage and the NSE client."""

from pathlib import Path

import dagster as dg

from cagr_data.core.db import Connection, connect
from cagr_data.core.storage import LocalStorage
from cagr_data.vendors.http import VendorClient


class PlatformDatabase(dg.ConfigurableResource["PlatformDatabase"]):
    """The platform database, connected as the pipeline role (cagr_writer)."""

    url: str

    def connect(self) -> Connection:
        return connect(self.url)


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
