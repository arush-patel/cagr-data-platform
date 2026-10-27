"""The one HTTP client every vendor adapter uses.

It is polite by construction: one client per vendor, a minimum interval between requests to the
same host, a browser user agent, and retries with exponential back-off on network errors, 5xx,
429 and 403 (NSE's bot protection answers 403 when it is unhappy). A 404 is an answer, not an
error: it is returned at once, because on NSE's archive it means "no file for that day".
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
RETRY_STATUSES = frozenset({403, 429, 500, 502, 503, 504})


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content: bytes
    elapsed_ms: int

    @property
    def found(self) -> bool:
        return self.status == 200


class RetryableStatusError(Exception):
    def __init__(self, url: str, status: int) -> None:
        super().__init__(f"HTTP {status} from {url}")
        self.status = status


class RateLimiter:
    """At most one request per `min_interval_s` to each host, across threads."""

    def __init__(
        self,
        min_interval_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.min_interval_s = min_interval_s
        self._clock, self._sleep = clock, sleep
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:
            now = self._clock()
            start = max(now, self._next.get(host, now))
            self._next[host] = start + self.min_interval_s
        if start > now:
            self._sleep(start - now)


class VendorClient:
    def __init__(
        self,
        rate_limit_per_s: float,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_s: float = 30.0,
        attempts: int = 5,
        backoff_s: float = 2.0,
        limiter: RateLimiter | None = None,
    ) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            timeout=timeout_s,
            follow_redirects=True,
            transport=transport,
        )
        self.limiter = limiter or RateLimiter(1.0 / rate_limit_per_s)
        self._get = retry(
            retry=retry_if_exception_type((httpx.TransportError, RetryableStatusError)),
            stop=stop_after_attempt(attempts),
            wait=wait_exponential(multiplier=backoff_s, max=60),
            reraise=True,
            before_sleep=self._before_retry,
        )(self._get_once)
        self.retries = 0

    def _before_retry(self, state: RetryCallState) -> None:
        self.retries += 1

    def _get_once(self, url: str, headers: dict[str, str] | None) -> FetchResult:
        self.limiter.wait(urlsplit(url).netloc)
        started = time.monotonic()
        response = self._client.get(url, headers=headers)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if response.status_code in RETRY_STATUSES:
            raise RetryableStatusError(url, response.status_code)
        return FetchResult(url, response.status_code, response.content, elapsed_ms)

    def get(self, url: str, headers: dict[str, str] | None = None) -> FetchResult:
        """GET with retries. Returns 200s and 404s; raises after the last failed attempt."""
        result: FetchResult = self._get(url, headers)
        return result

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "VendorClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
