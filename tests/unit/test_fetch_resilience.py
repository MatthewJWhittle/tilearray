"""Fetch resilience: retries, Retry-After, circuit breaker, deadlines, host pressure."""

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
    TileFetcher,
    fetch_tile_with_policy,
    set_fetch_clock,
    tile_deadline_seconds,
)
from tilearray.fetch_presets import ea_dsp_fetch_defaults
from tilearray.pressure import PressureClassifier, PressureSignature
from tilearray.service.config import WCSConfig
from tilearray.types import Format, TileRequest, TileResponse

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("retry_waits")]


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    set_fetch_clock(None)
    yield
    TileFetcher.reset_instances()
    set_fetch_clock(None)


def _tile_request(**overrides: Any) -> TileRequest:
    defaults: dict[str, Any] = {
        "url": "https://example.com/tile",
        "params": {"layer": "a"},
        "retries": 2,
    }
    defaults.update(overrides)
    return TileRequest(**defaults)


def _start_tile_http_server(handler_cls: type) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/tile"


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


@respx.mock
def test_fetch_tile_success() -> None:
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(
            200,
            content=b"tile-bytes",
            headers={"content-type": "image/tiff"},
        )
    )
    response = fetch_tile_with_policy(
        _tile_request(output_format=Format.GEOTIFF),
        FetchPolicy(max_concurrent=2, rate_limit_per_second=None),
    )
    assert response.success is True
    assert response.data == b"tile-bytes"


@pytest.mark.parametrize(
    ("statuses", "retries", "expected_calls"),
    [
        pytest.param(
            [
                httpx.Response(
                    403,
                    text="Forbidden",
                    headers={"Server": "Microsoft-Azure-Application-Gateway/v2"},
                ),
                httpx.Response(403, text="Forbidden"),
                httpx.Response(200, content=b"recovered"),
            ],
            2,
            3,
            id="transient_403",
        ),
        pytest.param(
            [
                httpx.Response(503, text="busy"),
                httpx.Response(503, text="busy"),
                httpx.Response(200, content=b"recovered"),
            ],
            2,
            3,
            id="transient_503",
        ),
        pytest.param(
            [
                httpx.Response(429, text="slow", headers={"Retry-After": "0"}),
                httpx.Response(503, text="busy"),
                httpx.Response(200, content=b"recovered"),
            ],
            2,
            3,
            id="429_then_503",
        ),
    ],
)
@respx.mock
def test_fetch_retries_transient_pressure(
    statuses: list[httpx.Response],
    retries: int,
    expected_calls: int,
) -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = statuses
    response = fetch_tile_with_policy(
        _tile_request(retries=retries),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=retries),
    )
    assert response.success is True
    assert response.data == b"recovered"
    assert route.call_count == expected_calls


@respx.mock
def test_fetch_tile_honours_retry_after_on_403(retry_waits: list[float]) -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(403, headers={"Retry-After": "2"}),
        httpx.Response(200, content=b"ok"),
    ]
    response = fetch_tile_with_policy(
        _tile_request(retries=1),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=1),
    )
    assert response.success is True
    assert route.call_count == 2
    assert retry_waits == pytest.approx([2.0])


@respx.mock
def test_fetch_tile_honours_retry_after_on_429(retry_waits: list[float]) -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "2"}),
        httpx.Response(200, content=b"ok"),
    ]
    response = fetch_tile_with_policy(
        _tile_request(retries=1),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=1),
    )
    assert response.success is True
    assert route.call_count == 2
    assert retry_waits == pytest.approx([2.0])


@respx.mock
def test_fetch_retries_429_and_503_record_waits(retry_waits: list[float]) -> None:
    route = respx.get("https://example.com/tile")
    route.side_effect = [
        httpx.Response(429, text="slow", headers={"Retry-After": "2"}),
        httpx.Response(503, text="busy"),
        httpx.Response(200, content=b"recovered"),
    ]
    response = fetch_tile_with_policy(
        _tile_request(retries=2),
        FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=2),
    )
    assert response.success is True
    assert route.call_count == 3
    assert retry_waits[0] == pytest.approx(2.0)
    assert len(retry_waits) == 2


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
    assert response.success is True
    assert response.data == b"recovered"
    assert route.call_count == 2
    assert fetcher.stats.concurrency_decreases >= 1
    assert fetcher.stats.retry_count >= 1


def test_circuit_breaker_trips_at_exact_threshold() -> None:
    """F1: clustered pressure at forbidden_threshold must trip (not threshold+1)."""

    threshold = 3
    gate = AdaptiveConcurrencyGate(
        initial=8,
        minimum=4,
        maximum=10,
        forbidden_window_seconds=5.0,
        forbidden_threshold=threshold,
        forbidden_cooldown_seconds=15.0,
    )
    assert gate.record_circuit_breaker_pressure("host") is False
    assert gate.record_circuit_breaker_pressure("host") is False
    assert gate.record_circuit_breaker_pressure("host") is True
    assert gate.circuit_breaker_trips == 1
    assert gate.circuit_breaker_frozen("host") is True
    assert gate.current_limit("host") == 4


def test_circuit_breaker_ignores_sparse_events_outside_window() -> None:
    """F2: events spaced beyond the window must not accumulate into a trip."""

    clock = {"now": 1_000.0}
    set_fetch_clock(lambda: clock["now"])
    window = 1.0
    threshold = 3
    gate = AdaptiveConcurrencyGate(
        initial=4,
        minimum=4,
        maximum=8,
        forbidden_window_seconds=window,
        forbidden_threshold=threshold,
        forbidden_cooldown_seconds=30.0,
    )
    for _ in range(threshold):
        assert gate.record_circuit_breaker_pressure("host") is False
        clock["now"] += window + 0.1

    assert gate.circuit_breaker_trips == 0
    assert gate.circuit_breaker_frozen("host") is False


def test_circuit_breaker_freezes_until_cooldown_elapses() -> None:
    """F3: a trip must freeze until the injectable cooldown clock advances."""

    clock = {"now": 5_000.0}
    set_fetch_clock(lambda: clock["now"])
    cooldown = 2.0
    gate = AdaptiveConcurrencyGate(
        initial=4,
        minimum=2,
        maximum=8,
        forbidden_window_seconds=10.0,
        forbidden_threshold=3,
        forbidden_cooldown_seconds=cooldown,
    )
    assert gate.record_circuit_breaker_pressure("host") is False
    assert gate.record_circuit_breaker_pressure("host") is False
    assert gate.record_circuit_breaker_pressure("host") is True
    assert gate.circuit_breaker_frozen("host") is True

    clock["now"] += cooldown + 0.01
    assert gate.circuit_breaker_frozen("host") is False


@respx.mock
def test_circuit_breaker_trips_on_clustered_403s_via_fetch() -> None:
    """Fetch path: clustered 403s trip the breaker and floor current_limit."""

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
        forbidden_cooldown_seconds=0.5,
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
        forbidden_cooldown_seconds=0.5,
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


def test_tile_deadline_scales_with_request_pixel_size_and_caps() -> None:
    """F7: large tiles scale the deadline but must not exceed the 4× cap."""

    policy = FetchPolicy(tile_deadline=10.0)
    small = _tile_request(width=256, height=256)
    large = _tile_request(width=1024, height=1024)
    huge = _tile_request(width=10_000, height=10_000)
    assert tile_deadline_seconds(policy, small) == pytest.approx(10.0)
    assert tile_deadline_seconds(policy, large) == pytest.approx(40.0)
    assert tile_deadline_seconds(policy, huge) == pytest.approx(40.0)
    assert tile_deadline_seconds(policy, _tile_request()) == pytest.approx(10.0)


def test_tile_deadline_aborts_hung_tile_before_full_read_timeout() -> None:
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
    assert stall_elapsed < max(2.0 * healthy_elapsed, healthy_elapsed + 1.5)
    assert stall_elapsed < 5.0


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
def test_policy_retries_zero_ignores_tile_request_default() -> None:
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
def test_ea_preset_retries_one_off_404_then_succeeds() -> None:
    route = respx.get("https://environment.data.gov.uk/spatialdata/example/wcs")
    route.side_effect = [
        httpx.Response(
            404,
            content=_OGC_INVALID_SUBSETTING_BODY,
            headers={"content-type": "application/vnd.ogc.se_xml"},
        ),
        httpx.Response(200, content=b"recovered"),
    ]
    policy = WCSConfig.from_url(
        "https://environment.data.gov.uk/spatialdata/example/wcs",
        coverage_id="cov",
        **ea_dsp_fetch_defaults(),
    ).fetch_policy()
    assert policy.pressure_classifier is not None
    ea_404 = next(
        s for s in policy.pressure_classifier.signatures if s.name == "ea_transient_404"
    )
    assert ea_404.aimd_pressure is False
    fetcher = TileFetcher.for_policy(policy)
    response = fetcher.fetch(
        _tile_request(
            url="https://environment.data.gov.uk/spatialdata/example/wcs",
            retries=policy.retries,
        )
    )
    assert response.success is True
    assert response.data == b"recovered"
    assert route.call_count == 2
    assert fetcher.stats.concurrency_decreases == 0


@respx.mock
def test_ea_preset_permanent_404_stops_within_bounded_retries() -> None:
    route = respx.get("https://environment.data.gov.uk/spatialdata/example/wcs")
    route.mock(
        return_value=httpx.Response(
            404,
            content=_OGC_NO_SUCH_COVERAGE_BODY,
            headers={"content-type": "application/vnd.ogc.se_xml"},
        )
    )
    policy = WCSConfig.from_url(
        "https://environment.data.gov.uk/spatialdata/example/wcs",
        coverage_id="cov",
        **ea_dsp_fetch_defaults(),
    ).fetch_policy()
    assert policy.retries == 4
    fetcher = TileFetcher.for_policy(policy)
    response = fetcher.fetch(
        _tile_request(
            url="https://environment.data.gov.uk/spatialdata/example/wcs",
            retries=policy.retries,
        )
    )
    assert response.success is False
    assert response.status_code == 404
    assert route.call_count == 3
    assert fetcher.stats.concurrency_decreases == 0


@respx.mock
def test_bounded_pressure_signature_exhaust_applies_pressure_not_success() -> None:
    classifier = PressureClassifier(
        signatures=(
            PressureSignature(
                name="bounded_503",
                status_codes=frozenset({503}),
                retryable=True,
                aimd_pressure=True,
                max_retries=1,
            ),
        )
    )
    route = respx.get("https://example.com/tile")
    route.mock(return_value=httpx.Response(503, text="busy"))
    policy = FetchPolicy(
        max_concurrent=8,
        initial_concurrent=4,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        retries=4,
        pressure_classifier=classifier,
    )
    fetcher = TileFetcher.for_policy(policy)
    response = fetcher.fetch(_tile_request(retries=4))
    assert response.success is False
    assert response.status_code == 503
    assert route.call_count == 2
    assert fetcher.stats.concurrency_decreases >= 1
    assert fetcher.stats.current_limit <= 4


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


def test_fetch_progress_abort_blocks_new_fetches() -> None:
    progress = FetchProgress(total=1)
    progress.abort("earlier failure")
    with pytest.raises(NetworkError, match="earlier failure"):
        progress.check_not_aborted()


def test_fetch_progress_rejects_non_positive_call_max_concurrent() -> None:
    with pytest.raises(ValidationError, match="call_max_concurrent"):
        FetchProgress(total=1, call_max_concurrent=0)


@respx.mock
@pytest.mark.parametrize("encoding", ["gzip", "deflate"])
def test_fetch_tile_decodes_content_encoded_body_once(encoding: str) -> None:
    import gzip
    import zlib

    body = b"compressed tile bytes" * 50
    payload = gzip.compress(body) if encoding == "gzip" else zlib.compress(body)
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(
            200,
            content=payload,
            headers={"content-type": "image/tiff", "content-encoding": encoding},
        )
    )
    response = fetch_tile_with_policy(
        _tile_request(output_format=Format.GEOTIFF),
        FetchPolicy(max_concurrent=2, rate_limit_per_second=None),
    )
    assert response.success is True
    assert response.data == body


def test_fetch_policy_connect_and_read_timeouts_are_distinct() -> None:
    policy = FetchPolicy(
        connect_timeout=4.0,
        read_timeout=17.0,
        tile_deadline=17.0,
        rate_limit_per_second=None,
    )
    assert policy.connect_timeout == 4.0
    assert policy.read_timeout == 17.0
    assert policy.connect_timeout != policy.read_timeout
