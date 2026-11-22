"""Fetch concurrency: AIMD gate, host ceilings, call_max, abort of siblings."""

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
from tilearray.fetch_presets import ea_dsp_fetch_defaults
from tilearray.types import TileRequest, TileResponse

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("retry_waits")]


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _tile_request(**overrides: Any) -> TileRequest:
    defaults: dict[str, Any] = {
        "url": "https://example.com/tile",
        "params": {"layer": "a"},
        "retries": 2,
    }
    defaults.update(overrides)
    return TileRequest(**defaults)


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
@pytest.mark.slow
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
    assert fetcher.stats.current_limit >= 3


@respx.mock
def test_aimd_never_exceeds_max_concurrent() -> None:
    """F10: AIMD must clamp at max_concurrent (not max+1)."""

    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    maximum = 4
    policy = FetchPolicy(
        max_concurrent=maximum,
        initial_concurrent=maximum - 1,
        min_concurrent=1,
        additive_increase=2,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)
    for _ in range(8):
        assert fetcher.fetch(request).success is True
        assert fetcher.stats.current_limit <= maximum
    assert fetcher.stats.peak_concurrency_limit <= maximum
    assert fetcher.stats.current_limit == maximum


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        pytest.param(
            403,
            {
                "Retry-After": "0",
                "Server": "Microsoft-Azure-Application-Gateway/v2",
            },
            id="403",
        ),
        pytest.param(429, {"Retry-After": "0"}, id="429"),
    ],
)
@respx.mock
def test_aimd_decreases_limit_on_pressure_status(
    status: int, headers: dict[str, str]
) -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(status, headers=headers),
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
    assert fetcher.stats.current_limit <= 4


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
    assert response.success is False
    assert fetcher.stats.concurrency_decreases >= 1
    assert fetcher.stats.current_limit <= 2


@respx.mock
def test_aimd_ea_preset_starts_at_eight() -> None:
    defaults = ea_dsp_fetch_defaults()
    policy = FetchPolicy(
        max_concurrent=defaults["max_concurrent_requests"],
        initial_concurrent=defaults["initial_concurrent_requests"],
        min_concurrent=defaults["min_concurrent_requests"],
        adaptive_concurrency=defaults["adaptive_concurrency"],
        rate_limit_per_second=defaults["rate_limit_per_second"],
    )
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(200, content=b"x")
    )
    fetcher = TileFetcher.for_policy(policy)
    assert fetcher.fetch(_tile_request(retries=0)).success is True
    # First success increases from the EA seed of 8; peak must have seen ≥8.
    assert fetcher.stats.peak_concurrency_limit >= 8
    assert fetcher.stats.current_limit >= 8


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
    policy_a = FetchPolicy(**base, read_timeout=60.0, tile_deadline=60.0)
    policy_b = FetchPolicy(**base, read_timeout=61.0, tile_deadline=61.0)
    request = _tile_request(retries=0)

    fetcher_a = TileFetcher.for_policy(policy_a)
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(fetcher_a.fetch, request) for _ in range(16)]
        for future in futures:
            future.result()

    remembered = fetcher_a.stats.current_limit
    assert remembered >= 14

    fetcher_b = TileFetcher.for_policy(policy_b)
    assert fetcher_b.fetch(request).success is True
    assert fetcher_b.stats.current_limit >= remembered


def test_adaptive_gate_ramps_with_queued_waiters() -> None:
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


def test_adaptive_gate_never_exceeds_maximum() -> None:
    """F10 at the gate: additive increase must clamp at maximum."""

    gate = AdaptiveConcurrencyGate(initial=3, minimum=1, maximum=4, additive_increase=2)
    for _ in range(5):
        gate.record_success("host")
    assert gate.current_limit("host") == 4


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
@pytest.mark.slow
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

    respx.get("https://example.com/fail").mock(
        return_value=httpx.Response(500, text="fail")
    )
    respx.get("https://example.com/ok").mock(
        return_value=httpx.Response(200, content=b"x")
    )

    fail_request = _tile_request(url="https://example.com/fail", retries=0)
    ok_request = _tile_request(url="https://example.com/ok", retries=0)

    fail_response = fetcher.fetch(fail_request, progress=progress)
    assert fail_response.success is False
    progress.abort(fail_response.error_message or "fail")
    assert progress.is_aborted() is True

    with ThreadPoolExecutor(max_workers=2) as pool:
        ok_futures = [
            pool.submit(fetcher.fetch, ok_request, progress=progress) for _ in range(2)
        ]
        for future in ok_futures:
            with pytest.raises(NetworkError):
                future.result(timeout=2)


@respx.mock
def test_eight_threads_default_policy_run_near_parallel() -> None:
    latency_s = 0.05
    n_threads = 8

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(latency_s)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(max_concurrent=8, rate_limit_per_second=None, retries=0)
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(fetcher.fetch, request) for _ in range(n_threads)]
        for future in futures:
            assert future.result().success is True
    elapsed = time.monotonic() - started

    assert elapsed < latency_s * 3
    assert fetcher.stats.max_inflight >= n_threads // 2


@respx.mock
def test_call_max_concurrent_1_does_not_serialise_sibling_sessions() -> None:
    latency_s = 0.05
    n_sessions = 8
    host_ceiling = 8

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(latency_s)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    shared_policy = FetchPolicy(
        max_concurrent=host_ceiling,
        call_max_concurrent=1,
        rate_limit_per_second=None,
        retries=0,
    )
    fetcher = TileFetcher.for_policy(shared_policy)
    assert (
        shared_policy.cache_key()
        == FetchPolicy(
            max_concurrent=host_ceiling, rate_limit_per_second=None, retries=0
        ).cache_key()
    )
    request = _tile_request(retries=0)

    def one_session() -> TileResponse:
        progress = FetchProgress(total=1, call_max_concurrent=1)
        return fetcher.fetch(request, progress=progress)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_sessions) as pool:
        futures = [pool.submit(one_session) for _ in range(n_sessions)]
        for future in futures:
            assert future.result().success is True
    elapsed = time.monotonic() - started

    assert elapsed < latency_s * 3
    assert fetcher.stats.max_inflight >= n_sessions // 2


@respx.mock
def test_shared_gate_max_concurrent_1_warns_with_multiple_waiters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    latency_s = 0.05

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(latency_s)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0)
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    with caplog.at_level("WARNING", logger="tilearray.fetch"):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetcher.fetch, request) for _ in range(4)]
            for future in futures:
                future.result()

    assert any(
        "Shared fetch concurrency is 1" in record.message for record in caplog.records
    )


def test_call_max_concurrent_excluded_from_cache_key() -> None:
    a = FetchPolicy(
        max_concurrent=10, call_max_concurrent=1, rate_limit_per_second=None
    )
    b = FetchPolicy(
        max_concurrent=10, call_max_concurrent=None, rate_limit_per_second=None
    )
    assert a.cache_key() == b.cache_key()
    assert TileFetcher.for_policy(a) is TileFetcher.for_policy(b)


@respx.mock
def test_aimd_shared_gate_max_concurrent_1_warns_with_waiters(
    caplog: pytest.LogCaptureFixture,
) -> None:
    latency_s = 0.05

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(latency_s)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(
        max_concurrent=1,
        initial_concurrent=1,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        retries=0,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    with caplog.at_level("WARNING", logger="tilearray.fetch"):
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetcher.fetch, request) for _ in range(4)]
            for future in futures:
                future.result()

    assert any(
        "Shared fetch concurrency is 1" in record.message for record in caplog.records
    )
