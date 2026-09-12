"""Unit tests for the TileFetcher engine."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
import respx

from tilearray.fetch import (
    AdaptiveConcurrencyGate,
    FetchPolicy,
    FetchProgress,
    PerHostRateLimiter,
    TileFetcher,
    fetch_tile_with_policy,
)
from tilearray.types import Format, TileRequest, TileResponse

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _tile_request(**overrides: Any) -> TileRequest:
    defaults = {
        "url": "https://example.com/tile",
        "params": {"layer": "a"},
        "retries": 2,
    }
    defaults.update(overrides)
    return TileRequest(**defaults)


@respx.mock
def test_fetch_tile_success() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(
            200,
            content=b"tile-bytes",
            headers={"content-type": "image/tiff"},
        )
    )
    request = _tile_request(output_format=Format.GEOTIFF)

    response = fetch_tile_with_policy(
        request,
        FetchPolicy(max_concurrent=2, rate_limit_per_second=None),
    )

    assert response.success is True
    assert response.data == b"tile-bytes"


@respx.mock
def test_fetch_tile_retries_transient_503() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(503, text="busy"),
        httpx.Response(503, text="busy"),
        httpx.Response(200, content=b"recovered"),
    ]

    response = fetch_tile_with_policy(
        _tile_request(retries=2),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=2),
    )

    assert response.success is True
    assert response.data == b"recovered"
    assert route.call_count == 3


@respx.mock
def test_fetch_tile_honours_retry_after_on_429() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, content=b"ok"),
    ]

    started = time.monotonic()
    response = fetch_tile_with_policy(
        _tile_request(retries=1),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=1),
    )
    elapsed = time.monotonic() - started

    assert response.success is True
    assert route.call_count == 2
    assert elapsed >= 0.0


@respx.mock
def test_fetch_tile_returns_error_after_exhausted_retries() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(500, text="fail")
    )

    response = fetch_tile_with_policy(
        _tile_request(retries=0),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0),
    )

    assert response.success is False
    assert response.status_code == 500


@respx.mock
def test_fetch_tile_network_error() -> None:
    respx.get("https://example.com/tile").mock(
        side_effect=httpx.ConnectError("offline")
    )

    response = fetch_tile_with_policy(
        _tile_request(retries=0),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0),
    )

    assert response.success is False
    assert "Network error" in (response.error_message or "")


@respx.mock
def test_fetch_tile_bounded_concurrency() -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(max_concurrent=2, rate_limit_per_second=None)
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(fetcher.fetch, request) for _ in range(6)]
        results = [future.result() for future in futures]

    assert all(result.success for result in results)
    assert max_active <= 2


def test_custom_rate_limiter_hook_is_invoked() -> None:
    class RecordingLimiter:
        def __init__(self) -> None:
            self.hosts: list[str] = []

        def before_request(self, host: str) -> None:
            self.hosts.append(host)

    limiter = RecordingLimiter()
    policy = FetchPolicy(
        max_concurrent=1,
        rate_limit_per_second=None,
        rate_limiter=limiter,
    )

    with respx.mock:
        respx.get("https://example.com/tile").mock(
            return_value=httpx.Response(200, content=b"x")
        )
        fetch_tile_with_policy(_tile_request(retries=0), policy)

    assert limiter.hosts == ["example.com"]


@respx.mock
def test_builtin_rate_limiter_delays_requests() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    limiter = PerHostRateLimiter(10.0)
    fetcher = TileFetcher(
        FetchPolicy(max_concurrent=4, rate_limit_per_second=None, rate_limiter=limiter)
    )

    started = time.monotonic()
    fetcher.fetch(_tile_request(retries=0))
    fetcher.fetch(_tile_request(retries=0))
    elapsed = time.monotonic() - started

    assert elapsed >= 0.05


@respx.mock
def test_token_bucket_allows_initial_burst() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    policy = FetchPolicy(max_concurrent=2, rate_limit_per_second=1.0)
    fetcher = TileFetcher.for_policy(policy)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(fetcher.fetch, _tile_request(retries=0)) for _ in range(2)
        ]
        for future in futures:
            future.result()
    elapsed = time.monotonic() - started

    assert elapsed < 0.5


@respx.mock
def test_aimd_increases_limit_after_successes() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    policy = FetchPolicy(
        max_concurrent=4,
        initial_concurrent=1,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    for _ in range(3):
        response = fetcher.fetch(request)
        assert response.success is True

    assert fetcher.stats.peak_concurrency_limit >= 3


@respx.mock
def test_aimd_decreases_limit_on_429() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, content=b"ok"),
    ]
    policy = FetchPolicy(
        max_concurrent=8,
        initial_concurrent=4,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        retries=1,
    )
    fetcher = TileFetcher.for_policy(policy)

    response = fetcher.fetch(_tile_request(retries=1))
    gate = fetcher._adaptive_gate
    assert gate is not None

    assert response.success is True
    assert fetcher.stats.concurrency_decreases >= 1


@respx.mock
def test_aimd_decreases_limit_on_timeout() -> None:
    respx.get("https://example.com/tile").mock(
        side_effect=httpx.ReadTimeout("timed out")
    )
    policy = FetchPolicy(
        max_concurrent=8,
        initial_concurrent=4,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        retries=0,
    )
    fetcher = TileFetcher.for_policy(policy)

    response = fetcher.fetch(_tile_request(retries=0))
    gate = fetcher._adaptive_gate
    assert gate is not None

    assert response.success is False
    assert fetcher.stats.concurrency_decreases >= 1
    assert gate.current_limit("example.com") <= 2


def test_adaptive_gate_additive_and_multiplicative() -> None:
    gate = AdaptiveConcurrencyGate(initial=2, minimum=1, maximum=8)

    gate.record_success("host")
    assert gate.current_limit("host") == 3

    gate.record_pressure("host")
    assert gate.current_limit("host") == 1
    assert gate.decrease_count == 1


@respx.mock
def test_aimd_healthy_burst_reaches_high_inflight() -> None:
    active = 0
    max_active = 0
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(
        max_concurrent=8,
        initial_concurrent=2,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetcher.fetch, request) for _ in range(16)]
        for future in futures:
            future.result()

    assert max_active >= 4
    assert fetcher.stats.peak_concurrency_limit >= 4


def test_fetch_progress_callback_records_failures() -> None:
    events: list[tuple[int, int, bool]] = []

    def on_progress(
        done: int,
        total: int,
        request: TileRequest,
        response: TileResponse,
    ) -> None:
        events.append((done, total, response.success))

    progress = FetchProgress(total=2, on_progress=on_progress)
    ok = TileRequest(url="https://example.com/a", params={})
    bad = TileRequest(url="https://example.com/b", params={})

    progress.tick(
        ok,
        TileResponse(
            data=b"x",
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=ok.url,
            success=True,
        ),
    )
    progress.tick(
        bad,
        TileResponse(
            data=b"",
            content_type="",
            status_code=500,
            headers={},
            url=bad.url,
            success=False,
            error_message="HTTP 500",
        ),
    )

    assert events == [(1, 2, True), (2, 2, False)]
    assert len(progress.errors) == 1
