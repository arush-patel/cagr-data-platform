import httpx
import pytest

from cagr_data.vendors.http import RateLimiter, RetryableStatusError, VendorClient


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _client(handler, attempts: int = 3) -> VendorClient:  # type: ignore[no-untyped-def]
    clock = FakeClock()
    return VendorClient(
        2,
        transport=httpx.MockTransport(handler),
        attempts=attempts,
        backoff_s=0,
        limiter=RateLimiter(0.5, clock=clock, sleep=clock.sleep),
    )


def test_rate_limiter_spaces_requests_per_host() -> None:
    clock = FakeClock()
    limiter = RateLimiter(1.0, clock=clock, sleep=clock.sleep)
    limiter.wait("a")
    limiter.wait("a")
    limiter.wait("b")
    limiter.wait("a")
    assert clock.slept == [1.0, 1.0]


def test_200_and_404_are_answers() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404 if "missing" in request.url.path else 200, content=b"x")

    with _client(handler) as client:
        ok = client.get("https://nsearchives.nseindia.com/file")
        missing = client.get("https://nsearchives.nseindia.com/missing")
    assert ok.found and ok.content == b"x"
    assert missing.status == 404 and not missing.found
    assert client.retries == 0


def test_sends_a_browser_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200)

    with _client(handler) as client:
        client.get("https://nsearchives.nseindia.com/file")
    assert seen[0].startswith("Mozilla/5.0")


def test_retries_403_then_succeeds() -> None:
    answers = iter([403, 503, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(next(answers), content=b"ok")

    with _client(handler) as client:
        result = client.get("https://www.nseindia.com/api/holiday-master")
    assert result.found
    assert client.retries == 2


def test_gives_up_after_the_last_attempt() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    with _client(handler, attempts=2) as client, pytest.raises(RetryableStatusError):
        client.get("https://nsearchives.nseindia.com/file")


def test_retries_network_errors() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("reset", request=request)
        return httpx.Response(200)

    with _client(handler) as client:
        assert client.get("https://nsearchives.nseindia.com/file").found
