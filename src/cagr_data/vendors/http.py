"""HTTP for vendor adapters: one client per vendor, a rate limit, retries, and a file cache.

Every successful download is written under RAW_FILE_ROOT before anything parses it, so a rerun
reads the saved file instead of downloading again. Adapters are the only code that talks to the
internet.
"""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx

from cagr_data.settings import get_settings


@dataclass(frozen=True)
class Fetched:
    url: str
    status: int
    content: bytes | None  # None when the server says the file does not exist (404)
    local_path: Path | None
    from_cache: bool

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content or b"").hexdigest()

    @property
    def missing(self) -> bool:
        return self.content is None


class Fetcher(Protocol):
    def get(self, url: str, cache_key: str) -> Fetched: ...


class RateLimiter:
    def __init__(self, per_minute: int) -> None:
        self.interval = 60.0 / per_minute
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            if now < self._next:
                time.sleep(self._next - now)
            self._next = max(now, self._next) + self.interval


class HttpFetcher:
    """Downloads with a browser user agent, backs off on 403/429/5xx, and caches to disk."""

    RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})

    def __init__(
        self,
        cache_root: Path,
        *,
        rate_limit_per_min: int = 30,
        warmup_url: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        s = get_settings()
        self.cache_root = cache_root
        self.limiter = RateLimiter(rate_limit_per_min)
        self.max_retries = s.http_max_retries
        self.warmup_url = warmup_url
        self._warmed = False
        self.client = httpx.Client(
            timeout=s.http_timeout_s,
            follow_redirects=True,
            headers={"User-Agent": s.http_user_agent, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"}
            | (headers or {}),
        )

    def _warm(self) -> None:
        # The NSE API host sets its cookies on the home page; the archive host needs none.
        if self.warmup_url and not self._warmed:
            self.limiter.wait()
            self.client.get(self.warmup_url)
            self._warmed = True

    def get(self, url: str, cache_key: str) -> Fetched:
        path = self.cache_root / cache_key
        if path.exists():
            return Fetched(url, 200, path.read_bytes(), path, from_cache=True)
        self._warm()
        delay = 2.0
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.client.get(url)
            except httpx.TransportError:
                if attempt == self.max_retries:
                    raise
            else:
                if resp.status_code == 200:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix(path.suffix + ".part")
                    tmp.write_bytes(resp.content)
                    tmp.replace(path)
                    return Fetched(url, 200, resp.content, path, from_cache=False)
                if resp.status_code == 404:
                    return Fetched(url, 404, None, None, from_cache=False)
                if resp.status_code not in self.RETRY_STATUSES or attempt == self.max_retries:
                    resp.raise_for_status()
                if resp.status_code == 403:
                    self._warmed = False
                    self._warm()
            time.sleep(delay)
            delay *= 2
        raise RuntimeError("unreachable")

    def close(self) -> None:
        self.client.close()


class DirectoryFetcher:
    """Serves files from a local folder laid out like the cache. Used by tests and offline replays."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get(self, url: str, cache_key: str) -> Fetched:
        path = self.root / cache_key
        if path.exists():
            return Fetched(url, 200, path.read_bytes(), path, from_cache=True)
        return Fetched(url, 404, None, None, from_cache=False)
