"""Unit tests for the TileFetcher engine."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import pytest
import respx

from tilearray.errors import NetworkError
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
def test_fetch_tile_retries_transient_403() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(
            403,
            text="Forbidden",
            headers={"Server": "Microsoft-Azure-Application-Gateway/v2"},
        ),
        httpx.Response(403, text="Forbidden"),
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
def test_fetch_tile_honours_retry_after_on_403() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(403, headers={"Retry-After": "0"}),
        httpx.Response(200, content=b"ok"),
    ]

    response = fetch_tile_with_policy(
        _tile_request(retries=1),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=1),
    )

    assert response.success is True
    assert route.call_count == 2


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
def test_fetch_tile_retries_transient_500_with_aimd_pressure() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(
            500,
            text='{"statusCode":500,"code":"internal_error"}',
        ),
        httpx.Response(200, content=b"recovered"),
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
    assert response.data == b"recovered"
    assert route.call_count == 2
    assert fetcher.stats.concurrency_decreases >= 1
    assert fetcher.stats.retry_count >= 1


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
def test_aimd_decreases_limit_on_403() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(
            403,
            headers={
                "Retry-After": "0",
                "Server": "Microsoft-Azure-Application-Gateway/v2",
            },
        ),
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
    assert fetcher.stats.retry_count >= 1


@respx.mock
def test_aimd_ea_preset_starts_at_eight() -> None:
    from tilearray.fetch_presets import ea_dsp_fetch_defaults

    defaults = ea_dsp_fetch_defaults()
    policy = FetchPolicy(
        max_concurrent=defaults["max_concurrent_requests"],
        initial_concurrent=defaults["initial_concurrent_requests"],
        min_concurrent=defaults["min_concurrent_requests"],
        adaptive_concurrency=defaults["adaptive_concurrency"],
        rate_limit_per_second=defaults["rate_limit_per_second"],
    )
    fetcher = TileFetcher.for_policy(policy)
    gate = fetcher._adaptive_gate
    assert gate is not None
    assert gate.current_limit("example.com") == 8


@respx.mock
def test_aimd_remembers_limit_across_fetch_batches() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    base = {
        "max_concurrent": 16,
        "initial_concurrent": 8,
        "min_concurrent": 1,
        "adaptive_concurrency": True,
        "rate_limit_per_second": None,
    }
    policy_a = FetchPolicy(**base, timeout=60.0)
    policy_b = FetchPolicy(**base, timeout=61.0)
    request = _tile_request(retries=0)

    fetcher_a = TileFetcher.for_policy(policy_a)
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetcher_a.fetch, request) for _ in range(16)]
        for future in futures:
            future.result()

    gate = fetcher_a._adaptive_gate
    assert gate is not None
    remembered = gate.current_limit("example.com")
    assert remembered >= 14

    fetcher_b = TileFetcher.for_policy(policy_b)
    assert fetcher_b._adaptive_gate is gate
    assert gate.current_limit("example.com") == remembered

    with gate._map_lock:
        del gate._hosts["example.com"]
    assert gate.current_limit("example.com") == remembered


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


def test_adaptive_gate_ramps_with_queued_waiters() -> None:
    """Per-success increase is required: queued tiles keep inflight > 0 mid-batch."""

    gate = AdaptiveConcurrencyGate(initial=2, minimum=1, maximum=16)
    release = threading.Event()

    def worker() -> None:
        gate.acquire("host")
        release.wait(timeout=1)
        time.sleep(0.01)
        gate.finish_success("host")

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(worker) for _ in range(3)]
        time.sleep(0.05)
        release.set()
        for future in futures:
            future.result()

    assert gate.current_limit("host") >= 3


def test_adaptive_gate_slow_start_then_additive() -> None:
    gate = AdaptiveConcurrencyGate(initial=2, minimum=1, maximum=16)

    gate.record_success("host")
    assert gate.current_limit("host") == 4

    gate.record_success("host")
    assert gate.current_limit("host") == 6

    gate.record_pressure("host")
    assert gate.current_limit("host") == 3
    assert gate.decrease_count == 1


def test_adaptive_gate_ea_multiplicative_decrease_factor() -> None:
    gate = AdaptiveConcurrencyGate(
        initial=8, minimum=4, maximum=32, multiplicative_decrease=0.75
    )
    assert gate.current_limit("host") == 8

    gate.record_pressure("host")
    assert gate.current_limit("host") == 6
    assert gate.decrease_count == 1


def test_adaptive_gate_ea_floor_enforced_after_pressure() -> None:
    gate = AdaptiveConcurrencyGate(
        initial=8, minimum=4, maximum=32, multiplicative_decrease=0.75
    )
    for _ in range(5):
        gate.record_pressure("host")

    assert gate.current_limit("host") == 4
    assert gate.decrease_count == 2


@respx.mock
def test_aimd_realistic_latency_skipton_scale() -> None:
    tile_latency_s = 0.08

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(tile_latency_s)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(
        max_concurrent=16,
        initial_concurrent=2,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    fixed_policy = FetchPolicy(
        max_concurrent=2,
        adaptive_concurrency=False,
        rate_limit_per_second=None,
    )
    fixed_fetcher = TileFetcher.for_policy(fixed_policy)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetcher.fetch, request) for _ in range(16)]
        for future in futures:
            future.result()
    aimd_elapsed = time.monotonic() - started

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fixed_fetcher.fetch, request) for _ in range(16)]
        for future in futures:
            future.result()
    fixed_elapsed = time.monotonic() - started

    assert fetcher.stats.peak_concurrency_limit >= 8
    assert fetcher.stats.max_inflight >= 8
    assert aimd_elapsed < fixed_elapsed * 0.75


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


@respx.mock
def test_forbidden_circuit_breaker_trips_on_clustered_403s() -> None:
    route = respx.get("https://example.com/tile")
    route.mock(
        return_value=httpx.Response(
            403,
            text="Forbidden",
            headers={"Server": "Microsoft-Azure-Application-Gateway/v2"},
        )
    )
    policy = FetchPolicy(
        max_concurrent=10,
        initial_concurrent=8,
        min_concurrent=4,
        adaptive_concurrency=True,
        forbidden_circuit_breaker=True,
        forbidden_window_seconds=5.0,
        forbidden_threshold=6,
        forbidden_cooldown_seconds=15.0,
        rate_limit_per_second=None,
        retries=0,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetcher.fetch, request) for _ in range(8)]
        for future in futures:
            future.result()

    assert fetcher.stats.pressure_event_count >= 6
    assert fetcher.stats.circuit_breaker_trips >= 1
    assert fetcher.stats.current_limit == 4


@respx.mock
def test_forbidden_circuit_breaker_ignores_sparse_403s() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(403, text="Forbidden"),
        httpx.Response(200, content=b"ok"),
    ]
    policy = FetchPolicy(
        max_concurrent=8,
        initial_concurrent=4,
        min_concurrent=4,
        adaptive_concurrency=True,
        forbidden_circuit_breaker=True,
        forbidden_window_seconds=5.0,
        forbidden_threshold=6,
        forbidden_cooldown_seconds=15.0,
        rate_limit_per_second=None,
        retries=1,
    )
    fetcher = TileFetcher.for_policy(policy)

    response = fetcher.fetch(_tile_request(retries=1))

    assert response.success is True
    assert fetcher.stats.circuit_breaker_trips == 0
    assert fetcher.stats.circuit_breaker_frozen is False


@respx.mock
def test_fetch_stats_expose_live_aimd_limit() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    policy = FetchPolicy(
        max_concurrent=10,
        initial_concurrent=8,
        min_concurrent=4,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
    )
    fetcher = TileFetcher.for_policy(policy)

    fetcher.fetch(_tile_request(retries=0))

    assert fetcher.stats.current_limit == 10
    assert fetcher.stats.peak_concurrency_limit >= 8


@respx.mock
def test_fetch_abort_stops_sibling_tile_requests() -> None:
    progress = FetchProgress(total=3)
    policy = FetchPolicy(max_concurrent=3, rate_limit_per_second=None, retries=0)
    fetcher = TileFetcher.for_policy(policy)

    fail_route = respx.get("https://example.com/fail")
    fail_route.mock(return_value=httpx.Response(500, text="fail"))
    ok_calls = {"count": 0}
    start_gate = threading.Barrier(3)

    def ok_handler(request: httpx.Request) -> httpx.Response:
        start_gate.wait(timeout=1)
        ok_calls["count"] += 1
        time.sleep(0.05)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/ok").mock(side_effect=ok_handler)

    fail_request = _tile_request(url="https://example.com/fail", retries=0)
    ok_request = _tile_request(url="https://example.com/ok", retries=0)

    def fail_and_abort() -> TileResponse:
        response = fetcher.fetch(fail_request, progress=progress)
        if not response.success:
            progress.abort(response.error_message or "fail")
        return response

    with ThreadPoolExecutor(max_workers=3) as pool:
        fail_future = pool.submit(fail_and_abort)
        ok_futures = [
            pool.submit(fetcher.fetch, ok_request, progress=progress) for _ in range(2)
        ]
        fail_response = fail_future.result()
        aborted = 0
        for future in ok_futures:
            try:
                future.result()
            except NetworkError:
                aborted += 1

    assert fail_response.success is False
    assert progress.is_aborted() is True
    assert aborted >= 1
    assert ok_calls["count"] <= 1


def test_fetch_progress_abort_blocks_new_fetches() -> None:
    progress = FetchProgress(total=1)
    progress.abort("earlier failure")

    with pytest.raises(NetworkError, match="earlier failure"):
        progress.check_not_aborted()


_OGC_404_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<ExceptionReport xmlns="http://www.opengis.net/ogc">
  <Exception exceptionCode="InvalidParameterValue" locator="SUBSETTINGCRS">
    <ExceptionText>Invalid or unsupported SubsettingCrs</ExceptionText>
  </Exception>
</ExceptionReport>"""


@respx.mock
def test_fetch_retries_ogc_404_invalid_parameter_value() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(
            404,
            content=_OGC_404_BODY,
            headers={"content-type": "application/xml"},
        ),
        httpx.Response(200, content=b"recovered"),
    ]

    response = fetch_tile_with_policy(
        _tile_request(retries=1),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=1),
    )

    assert response.success is True
    assert response.data == b"recovered"
    assert route.call_count == 2


@respx.mock
def test_fetch_does_not_retry_plain_404() -> None:
    route = respx.get("https://example.com/tile")
    route.mock(return_value=httpx.Response(404, text="Not Found"))

    response = fetch_tile_with_policy(
        _tile_request(retries=2),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=2),
    )

    assert response.success is False
    assert response.status_code == 404
    assert route.call_count == 1


@respx.mock
def test_aimd_decreases_limit_on_ogc_404() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(404, content=_OGC_404_BODY),
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

    assert response.success is True
    assert fetcher.stats.concurrency_decreases >= 1
    assert fetcher.stats.retry_count >= 1
    assert fetcher.stats.pressure_event_count == 0


@respx.mock
def test_circuit_breaker_trips_on_clustered_429s() -> None:
    route = respx.get("https://example.com/tile")
    route.mock(return_value=httpx.Response(429, text="Too Many Requests"))
    policy = FetchPolicy(
        max_concurrent=10,
        initial_concurrent=8,
        min_concurrent=4,
        adaptive_concurrency=True,
        forbidden_circuit_breaker=True,
        forbidden_window_seconds=5.0,
        forbidden_threshold=6,
        forbidden_cooldown_seconds=15.0,
        rate_limit_per_second=None,
        retries=0,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(fetcher.fetch, request) for _ in range(8)]
        for future in futures:
            future.result()

    assert fetcher.stats.pressure_event_count >= 6
    assert fetcher.stats.circuit_breaker_trips >= 1
    assert fetcher.stats.current_limit == 4
