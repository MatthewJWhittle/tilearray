"""Unit tests for the TileFetcher engine."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
import respx

from tilearray.errors import NetworkError, ValidationError
from tilearray.fetch import (
    AdaptiveConcurrencyGate,
    FetchPolicy,
    FetchProgress,
    PerHostRateLimiter,
    TileFetcher,
    fetch_tile_with_policy,
    tile_deadline_seconds,
)
from tilearray.service.config import WCSConfig
from tilearray.types import Format, TileRequest, TileResponse

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def test_fetch_policy_rejects_invalid_bounds() -> None:
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        FetchPolicy(max_concurrent=0)
    with pytest.raises(PydanticValidationError):
        FetchPolicy(read_timeout=-1.0)
    with pytest.raises(PydanticValidationError):
        FetchPolicy(connect_timeout=-1.0)
    with pytest.raises(PydanticValidationError):
        FetchPolicy(tile_deadline=-1.0)
    with pytest.raises(PydanticValidationError):
        FetchPolicy(multiplicative_decrease=1.0)
    with pytest.raises(PydanticValidationError):
        FetchPolicy(call_max_concurrent=0)

    policy = FetchPolicy(max_concurrent=4, call_max_concurrent=2)
    copied = policy.model_copy(update={"call_max_concurrent": None})
    assert copied.max_concurrent == 4
    assert copied.call_max_concurrent is None


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
    policy_a = FetchPolicy(**base, read_timeout=60.0, tile_deadline=60.0)
    policy_b = FetchPolicy(**base, read_timeout=61.0, tile_deadline=61.0)
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
    """After progress.abort(), sibling fetches sharing it must raise NetworkError.

    Avoids the old Barrier(3)/sleep race (only two waiters) that flakes under xdist.
    """
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


def test_fetch_progress_abort_blocks_new_fetches() -> None:
    progress = FetchProgress(total=1)
    progress.abort("earlier failure")

    with pytest.raises(NetworkError, match="earlier failure"):
        progress.check_not_aborted()


_OGC_INVALID_SUBSETTING_BODY = b"""<?xml version="1.0" encoding="UTF-8" ?>
<ows:ExceptionReport version="2.0.0" xmlns:ows="http://www.opengis.net/ows/2.0">
  <ows:Exception exceptionCode="InvalidSubsetting" locator="(17360,18640)"/>
</ows:ExceptionReport>
"""

_OGC_INVALID_PARAMETER_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<ExceptionReport xmlns="http://www.opengis.net/ogc">
  <Exception exceptionCode="InvalidParameterValue" locator="SUBSETTINGCRS">
    <ExceptionText>Invalid or unsupported SubsettingCrs</ExceptionText>
  </Exception>
</ExceptionReport>"""

_OGC_NO_SUCH_COVERAGE_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<ows:ExceptionReport version="2.0.0" xmlns:ows="http://www.opengis.net/ows/2.0">
  <ows:Exception exceptionCode="NoSuchCoverage" locator="coverage"/>
</ows:ExceptionReport>
"""


@pytest.mark.parametrize(
    "body",
    [
        _OGC_INVALID_SUBSETTING_BODY,
        _OGC_INVALID_PARAMETER_BODY,
        _OGC_NO_SUCH_COVERAGE_BODY,
        b"",
    ],
    ids=[
        "invalid_subsetting",
        "invalid_parameter_value",
        "no_such_coverage",
        "empty_body",
    ],
)
@respx.mock
def test_fetch_does_not_retry_ogc_or_plain_404(body: bytes) -> None:
    route = respx.get("https://example.com/tile")
    route.mock(
        return_value=httpx.Response(
            404,
            content=body,
            headers={"content-type": "application/vnd.ogc.se_xml"},
        )
    )

    response = fetch_tile_with_policy(
        _tile_request(retries=3),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=3),
    )

    assert response.success is False
    assert response.status_code == 404
    assert route.call_count == 1


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
def test_fetch_retries_429_and_503() -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(429, text="slow", headers={"Retry-After": "0"}),
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
def test_policy_retries_zero_ignores_tile_request_default() -> None:
    """fetch_retries=0 must yield one attempt even when TileRequest.retries is 3."""

    route = respx.get("https://example.com/tile")
    route.mock(return_value=httpx.Response(503, text="busy"))

    config = WCSConfig(
        base_url="https://example.com/wcs",
        coverage_id="cov",
        fetch_retries=0,
        rate_limit_per_second=None,
        max_concurrent_requests=1,
    )
    policy = config.fetch_policy()
    assert policy.retries == 0

    response = fetch_tile_with_policy(_tile_request(retries=3), policy)

    assert response.success is False
    assert response.status_code == 503
    assert route.call_count == 1


@respx.mock
def test_tile_request_retries_zero_fail_fast_ceiling() -> None:
    """Explicit TileRequest.retries=0 caps attempts to one under a high policy (#177)."""

    route = respx.get("https://example.com/tile")
    route.mock(return_value=httpx.Response(503, text="busy"))

    policy = FetchPolicy(
        max_concurrent=2,
        rate_limit_per_second=None,
        retries=4,
    )
    response = fetch_tile_with_policy(
        _tile_request(retries=0, params={"scaleSize": "x(64),y(64)"}),
        policy,
    )

    assert response.success is False
    assert response.status_code == 503
    assert route.call_count == 1


@respx.mock
def test_ogc_404_does_not_trigger_aimd_or_retry() -> None:
    route = respx.get("https://example.com/tile")
    route.mock(return_value=httpx.Response(404, content=_OGC_INVALID_SUBSETTING_BODY))
    policy = FetchPolicy(
        max_concurrent=8,
        initial_concurrent=4,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        retries=3,
    )
    fetcher = TileFetcher.for_policy(policy)

    response = fetcher.fetch(_tile_request(retries=3))

    assert response.success is False
    assert response.status_code == 404
    assert route.call_count == 1
    assert fetcher.stats.retry_count == 0
    assert fetcher.stats.concurrency_decreases == 0


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


@respx.mock
def test_eight_threads_default_policy_run_near_parallel() -> None:
    """Host/shared ceiling should allow ~N-way overlap for N single-tile callers."""

    latency_s = 0.05
    n_threads = 8

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(latency_s)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    # Shared host ceiling of 8 (not per-call 1).
    policy = FetchPolicy(max_concurrent=8, rate_limit_per_second=None, retries=0)
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=0)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=n_threads) as pool:
        futures = [pool.submit(fetcher.fetch, request) for _ in range(n_threads)]
        for future in futures:
            assert future.result().success is True
    elapsed = time.monotonic() - started

    # Parallel: ~latency; serial would be ~n_threads * latency.
    assert elapsed < latency_s * 3
    assert fetcher.stats.max_inflight >= n_threads // 2


@respx.mock
def test_call_max_concurrent_1_does_not_serialise_sibling_sessions() -> None:
    """Per-session call_max_concurrent=1 must not shrink the shared gate for peers."""

    latency_s = 0.05
    n_sessions = 8
    host_ceiling = 8

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(latency_s)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    # Same shared ceiling; each logical call asks for local politeness of 1.
    shared_policy = FetchPolicy(
        max_concurrent=host_ceiling,
        call_max_concurrent=1,
        rate_limit_per_second=None,
        retries=0,
    )
    fetcher = TileFetcher.for_policy(shared_policy)
    # call_max is not part of the shared cache key / gate.
    assert fetcher._policy.call_max_concurrent is None
    assert fetcher._policy.max_concurrent == host_ceiling
    assert (
        shared_policy.cache_key()
        == FetchPolicy(
            max_concurrent=host_ceiling, rate_limit_per_second=None, retries=0
        ).cache_key()
    )

    request = _tile_request(retries=0)

    def one_session() -> TileResponse:
        # Fresh progress per call ⇒ local Semaphore(1) does not span siblings.
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


def test_fetch_progress_rejects_non_positive_call_max_concurrent() -> None:
    with pytest.raises(ValidationError, match="call_max_concurrent"):
        FetchProgress(total=1, call_max_concurrent=0)


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


def test_fetch_policy_connect_and_read_timeouts_are_distinct() -> None:
    policy = FetchPolicy(
        connect_timeout=4.0,
        read_timeout=17.0,
        tile_deadline=17.0,
        rate_limit_per_second=None,
    )
    fetcher = TileFetcher(policy)
    try:
        assert fetcher._client.timeout.connect == 4.0
        assert fetcher._client.timeout.read == 17.0
        assert fetcher._client.timeout.write == 17.0
        assert fetcher._client.timeout.pool == 4.0
        assert policy.connect_timeout != policy.read_timeout
    finally:
        fetcher.close()


def test_tile_deadline_scales_with_request_pixel_size() -> None:
    policy = FetchPolicy(tile_deadline=10.0)
    small = _tile_request(width=256, height=256)
    large = _tile_request(width=1024, height=1024)
    assert tile_deadline_seconds(policy, small) == pytest.approx(10.0)
    # 1024² / 256² → sqrt scale 4 → 4× base (at the scale cap).
    assert tile_deadline_seconds(policy, large) == pytest.approx(40.0)
    assert tile_deadline_seconds(policy, _tile_request()) == pytest.approx(10.0)


def _start_tile_http_server(handler_cls: type) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/tile"


def test_tile_deadline_aborts_hung_tile_before_full_read_timeout() -> None:
    """Wall-clock deadline must win over a long httpx idle-read timeout."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            time.sleep(30.0)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"too-late")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server, url = _start_tile_http_server(Handler)
    try:
        policy = FetchPolicy(
            max_concurrent=1,
            rate_limit_per_second=None,
            retries=0,
            connect_timeout=5.0,
            read_timeout=30.0,
            tile_deadline=0.4,
        )
        started = time.perf_counter()
        response = fetch_tile_with_policy(
            TileRequest(url=url, params={}, retries=0),
            policy,
        )
        elapsed = time.perf_counter() - started
    finally:
        server.shutdown()
        server.server_close()

    assert response.success is False
    assert "Network error" in (response.error_message or "")
    assert elapsed < 2.0
    assert elapsed < policy.read_timeout / 2


def test_stalled_tile_batch_finishes_within_about_2x_healthy_wall_time() -> None:
    """One-in-four stalls must not stretch a batch toward the old full read timeout."""

    healthy_latency_s = 0.05
    n_tiles = 8
    stall_every = 4
    max_concurrent = 4
    call_i = {"n": 0}
    lock = threading.Lock()

    class StallHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            with lock:
                idx = call_i["n"]
                call_i["n"] += 1
            if idx % stall_every == 0:
                time.sleep(30.0)
            else:
                time.sleep(healthy_latency_s)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    class HealthyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            time.sleep(healthy_latency_s)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    healthy_server, healthy_url = _start_tile_http_server(HealthyHandler)
    try:
        healthy_policy = FetchPolicy(
            max_concurrent=max_concurrent,
            rate_limit_per_second=None,
            retries=0,
            connect_timeout=2.0,
            read_timeout=60.0,
            tile_deadline=5.0,
        )
        request = TileRequest(url=healthy_url, params={}, retries=0)
        healthy_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
            futures = [
                pool.submit(fetch_tile_with_policy, request, healthy_policy)
                for _ in range(n_tiles)
            ]
            healthy_results = [future.result() for future in futures]
        healthy_elapsed = time.perf_counter() - healthy_started
    finally:
        healthy_server.shutdown()
        healthy_server.server_close()

    assert all(result.success for result in healthy_results)

    TileFetcher.reset_instances()
    stall_server, stall_url = _start_tile_http_server(StallHandler)
    try:
        stall_policy = FetchPolicy(
            max_concurrent=max_concurrent,
            rate_limit_per_second=None,
            retries=0,
            connect_timeout=2.0,
            read_timeout=60.0,
            tile_deadline=0.35,
        )
        request = TileRequest(url=stall_url, params={}, retries=0)
        stall_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=max_concurrent) as pool:
            futures = [
                pool.submit(fetch_tile_with_policy, request, stall_policy)
                for _ in range(n_tiles)
            ]
            stall_results = [future.result() for future in futures]
        stall_elapsed = time.perf_counter() - stall_started
    finally:
        stall_server.shutdown()
        stall_server.server_close()

    successes = sum(1 for result in stall_results if result.success)
    failures = sum(1 for result in stall_results if not result.success)
    assert successes == 6
    assert failures == 2
    # ~2× healthy wall under deadline behaviour (not ~60 s read-timeout tails).
    assert stall_elapsed < max(2.0 * healthy_elapsed, healthy_elapsed + 1.5)
    assert stall_elapsed < 5.0
