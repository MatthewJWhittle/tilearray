"""Fetch resilience: retries, breaker, deadlines, host pressure, deferred retry."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import numpy as np
import pytest
import respx

from tilearray import NetworkError, compute_mosaic, create_array, fetch_summary
from tilearray.array import _TILE_ERRORS_ATTR, _load_tile_array
from tilearray.errors import ParseError, ValidationError
from tilearray.fetch import (
    AdaptiveConcurrencyGate,
    FetchPolicy,
    FetchProgress,
    TileFetcher,
    deferred_park_cap,
    fetch_tile_with_policy,
    is_deferred_retry_failure,
    set_fetch_clock,
    tile_deadline_seconds,
)
from tilearray.fetch_presets import ea_dsp_fetch_defaults
from tilearray.mosaic_plan import MosaicPlan
from tilearray.pressure import (
    BUILTIN_PRESSURE_SIGNATURES,
    PressureClassifier,
    PressureSignature,
    classify_response,
    reset_pressure_classifier,
)
from tilearray.preview import FetchSummary
from tilearray.service.base import BaseService, TileGeometry
from tilearray.service.config import WCSConfig
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = [pytest.mark.unit, pytest.mark.usefixtures("retry_waits")]


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    set_fetch_clock(None)
    reset_pressure_classifier()
    yield
    TileFetcher.reset_instances()
    set_fetch_clock(None)
    reset_pressure_classifier()


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


# --- Pressure classifier (drives retry / AIMD / breaker decisions) ---


@pytest.mark.parametrize(
    ("status_code", "expected_name", "circuit_breaker"),
    [
        (403, "gateway_forbidden", True),
        (408, "gateway_timeout", False),
        (429, "rate_limit", True),
        (500, "upstream_error", False),
        (503, "upstream_error", False),
    ],
)
def test_classify_gateway_status_codes(
    status_code: int, expected_name: str, circuit_breaker: bool
) -> None:
    result = classify_response(httpx.Response(status_code, text="pressure"))

    assert result.retryable is True
    assert result.aimd_pressure is True
    assert result.circuit_breaker is circuit_breaker
    assert result.signature is not None
    assert result.signature.name == expected_name


@pytest.mark.parametrize(
    ("body", "headers"),
    [
        pytest.param(
            _OGC_INVALID_SUBSETTING_BODY,
            {"content-type": "application/vnd.ogc.se_xml"},
            id="invalid_subsetting",
        ),
        pytest.param(
            _OGC_INVALID_PARAMETER_BODY,
            {"content-type": "application/vnd.ogc.se_xml"},
            id="invalid_parameter_value",
        ),
        pytest.param(
            _OGC_NO_SUCH_COVERAGE_BODY,
            {"content-type": "application/vnd.ogc.se_xml"},
            id="no_such_coverage",
        ),
        pytest.param(
            b"", {"content-type": "application/vnd.ogc.se_xml"}, id="empty_body"
        ),
        pytest.param(b"Not Found", {}, id="plain_text"),
    ],
)
def test_classify_404_not_retryable(body: bytes, headers: dict[str, str]) -> None:
    response = httpx.Response(
        404,
        content=body,
        headers=headers,
        request=httpx.Request("GET", "https://example.com/wcs"),
    )
    result = classify_response(response)

    assert result.retryable is False
    assert result.aimd_pressure is False
    assert result.circuit_breaker is False
    assert result.signature is None


def test_custom_signature_is_pluggable() -> None:
    classifier = PressureClassifier()
    classifier.register(
        PressureSignature(
            name="custom_soft_418",
            status_codes=frozenset({418}),
            circuit_breaker=True,
        )
    )

    result = classifier.classify(httpx.Response(418, text="teapot"))

    assert result.retryable is True
    assert result.circuit_breaker is True
    assert result.signature is not None
    assert result.signature.name == "custom_soft_418"


def test_host_signature_can_bound_404_retries() -> None:
    classifier = PressureClassifier()
    classifier.register(
        PressureSignature(
            name="host_transient_404",
            status_codes=frozenset({404}),
            max_retries=2,
        )
    )

    result = classifier.classify(httpx.Response(404, text="missing"))

    assert result.retryable is True
    assert result.max_retries == 2
    assert result.signature is not None
    assert result.signature.name == "host_transient_404"


def test_body_substrings_require_any_match() -> None:
    classifier = PressureClassifier(
        signatures=(
            PressureSignature(
                name="xml_fault",
                status_codes=frozenset({502}),
                body_substrings=(b"<fault>", b"exceptionreport"),
            ),
        )
    )

    match = classifier.classify(httpx.Response(502, content=b"<fault>upstream</fault>"))
    miss = classifier.classify(httpx.Response(502, content=b"plain error"))

    assert match.retryable is True
    assert miss.retryable is False


def test_builtin_signatures_cover_known_dialects() -> None:
    names = {signature.name for signature in BUILTIN_PRESSURE_SIGNATURES}
    assert "gateway_forbidden" in names
    assert "rate_limit" in names
    assert "upstream_error" in names
    # Permanent 404 (incl. OGC ExceptionReport) is not a built-in retry dialect.
    assert "ogc_transient_404" not in names


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


# --- Deferred retry pass (#197) ---


def _ones_decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    return np.ones((request.height or 1, request.width or 1), dtype=np.float32)


def _grid_service(rows: int, cols: int) -> type[BaseService]:
    class GridService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.GEOTIFF

        def __init__(self) -> None:
            super().__init__("https://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            width, height = chunk_size
            grid_rows, grid_cols = options.get("grid_shape", (rows, cols))
            span_x = (bbox.max_x - bbox.min_x) / grid_cols
            span_y = (bbox.max_y - bbox.min_y) / grid_rows
            tiles: list[TileRequest] = []
            for row in range(grid_rows):
                for col in range(grid_cols):
                    min_x = bbox.min_x + col * span_x
                    max_x = min_x + span_x
                    min_y = bbox.min_y + (grid_rows - 1 - row) * span_y
                    max_y = min_y + span_y
                    tile_bbox = BoundingBox(
                        min_x=min_x,
                        min_y=min_y,
                        max_x=max_x,
                        max_y=max_y,
                        crs=bbox.crs,
                    )
                    idx = row * grid_cols + col
                    tiles.append(
                        TileRequest(
                            url=f"https://example.com/tile/{idx}",
                            params={},
                            output_format=Format.GEOTIFF,
                            crs=bbox.crs,
                            bbox=tile_bbox,
                            width=width,
                            height=height,
                            retries=0,
                        )
                    )
            return MosaicPlan(tiles=tuple(tiles))

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    return GridService


def _patch_flaky_grid(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: int,
    cols: int,
    flaky_idx: int,
    primary_failures: int,
    fail_status: int = 429,
) -> dict[str, Any]:
    """One flaky tile fails ``primary_failures`` times then succeeds; peers always 200.

    Uses ``tile_decoder=`` on create_array (no ``_resolve_decoder`` patch). With
    ``fetch_retries=0``, a second attempt is the deferred pass — peak deferred
    concurrency is counted from attempts with ``n > 1``, not private progress flags.
    """

    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}
    lock = threading.Lock()
    peak_deferred_inflight = {"value": 0}
    deferred_inflight = {"value": 0}

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        with lock:
            n = attempts.get(idx, 0) + 1
            attempts[idx] = n
            in_deferred = n > 1
            if in_deferred:
                deferred_inflight["value"] += 1
                peak_deferred_inflight["value"] = max(
                    peak_deferred_inflight["value"], deferred_inflight["value"]
                )
        try:
            if idx == flaky_idx and n <= primary_failures:
                return TileResponse(
                    data=b"slow",
                    content_type="text/plain",
                    status_code=fail_status,
                    headers={"Retry-After": "0"},
                    url=request.url,
                    success=False,
                    error_message=f"HTTP {fail_status}",
                )
            return TileResponse(
                data=b"ok",
                content_type="image/tiff",
                status_code=200,
                headers={},
                url=request.url,
                success=True,
            )
        finally:
            if n > 1:
                with lock:
                    deferred_inflight["value"] -= 1

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    return {
        "attempts": attempts,
        "peak_deferred_inflight": peak_deferred_inflight,
        "lock": lock,
    }


@pytest.mark.parametrize(
    ("status_code", "eligible"),
    [
        (0, True),
        (408, True),
        (429, True),
        (500, True),
        (502, True),
        (503, True),
        (504, True),
        (401, False),
        (403, False),
        (404, False),
    ],
)
def test_is_deferred_retry_failure_taxonomy(status_code: int, eligible: bool) -> None:
    ok = TileResponse(
        data=b"",
        content_type="",
        status_code=200,
        headers={},
        url="https://example.com/t",
        success=True,
    )
    assert is_deferred_retry_failure(ok) is False

    bad = TileResponse(
        data=b"",
        content_type="",
        status_code=status_code,
        headers={},
        url="https://example.com/t",
        success=False,
    )
    assert is_deferred_retry_failure(bad) is eligible


def test_deferred_park_cap_ceil_fraction() -> None:
    assert deferred_park_cap(16, fraction=0.1) == 2
    assert deferred_park_cap(10, fraction=0.1) == 1
    assert deferred_park_cap(1, fraction=0.1) == 1


def test_flaky_tile_mosaic_completes_with_one_deferred_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = cols = 4
    state = _patch_flaky_grid(
        monkeypatch,
        rows=rows,
        cols=cols,
        flaky_idx=7,
        primary_failures=1,
        fail_status=429,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=8, max_y=8, crs=CRS.EPSG_4326)
    result = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(2, 2),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        band_policy="first_band",
        compute=True,
        tile_decoder=_ones_decoder,
        max_concurrent_requests=4,
        min_concurrent_requests=2,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        fetch_retries=0,
    )
    assert result.shape == (rows * 2, cols * 2)
    assert np.isfinite(result.values).all()
    tile_errors = result.attrs[_TILE_ERRORS_ATTR]
    assert tile_errors["deferred_parked"] == 1
    assert tile_errors["deferred_recovered"] == 1
    assert tile_errors["deferred_failed"] == 0
    assert tile_errors["aborted"] is False
    assert state["attempts"][7] == 2
    assert sum(1 for n in state["attempts"].values() if n >= 1) == rows * cols
    summary = fetch_summary(result)
    assert summary.deferred_parked == 1
    assert summary.deferred_recovered == 1
    assert "deferred retry" in summary.summary()


def test_permanent_404_still_aborts_without_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = cols = 2
    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        attempts[idx] = attempts.get(idx, 0) + 1
        if idx == 0:
            return TileResponse(
                data=b"missing",
                content_type="text/plain",
                status_code=404,
                headers={},
                url=request.url,
                success=False,
                error_message="HTTP 404",
            )
        return TileResponse(
            data=b"ok",
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=4, max_y=4, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError, match="404"):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(2, 2),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            compute=True,
            tile_decoder=_ones_decoder,
            max_concurrent_requests=4,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
    assert attempts.get(0) == 1


@respx.mock
def test_permanent_401_still_aborts_on_first_answer() -> None:
    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0)
    respx.get("https://example.com/auth").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    request = TileRequest(
        url="https://example.com/auth",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
        retries=0,
    )
    with pytest.raises(NetworkError, match="401"):
        _load_tile_array(
            request, None, _ones_decoder, np.dtype("float32"), policy, progress
        )
    assert progress.is_aborted() is True
    assert progress.deferred_parked == 0


@respx.mock
def test_deferred_pass_failure_raises_with_attempt_counts() -> None:
    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=1)
    route = respx.get("https://example.com/rate").mock(
        return_value=httpx.Response(429, text="slow", headers={"Retry-After": "0"})
    )
    request = TileRequest(
        url="https://example.com/rate",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
        retries=1,
    )
    with pytest.raises(NetworkError, match="deferred retry pass failed"):
        _load_tile_array(
            request, None, _ones_decoder, np.dtype("float32"), policy, progress
        )
    assert progress.is_aborted() is True
    assert progress.deferred_parked == 1
    assert progress.deferred_failed == 1
    # primary attempts (retries+1) + deferred attempts (retries+1)
    assert route.call_count == 4


def test_park_cap_exceeded_aborts_without_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, cols = 2, 5  # 10 tiles; cap = ceil(0.1*10) = 1
    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}
    progress_holder: dict[str, FetchProgress] = {}
    real_progress = FetchProgress

    def progress_factory(*args: Any, **kwargs: Any) -> FetchProgress:
        progress = real_progress(*args, **kwargs)
        progress_holder["progress"] = progress
        return progress

    monkeypatch.setattr("tilearray.array.FetchProgress", progress_factory)

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        attempts[idx] = attempts.get(idx, 0) + 1
        if idx in {0, 1}:
            return TileResponse(
                data=b"slow",
                content_type="text/plain",
                status_code=500,
                headers={"Retry-After": "0"},
                url=request.url,
                success=False,
                error_message="HTTP 500",
            )
        return TileResponse(
            data=b"ok",
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=4, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(2, 2),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            compute=True,
            tile_decoder=_ones_decoder,
            max_concurrent_requests=4,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
    assert attempts.get(0, 0) == 1
    assert attempts.get(1, 0) == 1
    assert sum(1 for n in attempts.values() if n > 1) == 0
    progress = progress_holder["progress"]
    assert progress.deferred_parked == 1
    assert progress.deferred_recovered == 0
    assert progress.deferred_failed == 0
    assert progress.is_aborted() is True


@respx.mock
def test_hard_decode_failure_aborts_before_deferred_pass() -> None:
    """Successful HTTP then hard decode must abort so parked waiters do not fetch."""

    progress = FetchProgress(total=2, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0)
    respx.get("https://example.com/ok-bytes").mock(
        return_value=httpx.Response(200, content=b"not-a-tiff")
    )
    request = TileRequest(
        url="https://example.com/ok-bytes",
        params={},
        output_format=Format.GEOTIFF,
        width=2,
        height=2,
        retries=0,
    )

    def boom(_response: TileResponse, _request: TileRequest) -> np.ndarray:
        raise ValueError("corrupt tile body")

    assert progress.try_park_for_deferred_retry() is True
    with pytest.raises(ParseError, match="corrupt tile body"):
        _load_tile_array(request, None, boom, np.dtype("float32"), policy, progress)
    assert progress.is_aborted() is True
    with pytest.raises(NetworkError):
        progress.wait_for_deferred_pass(retry_after=0.0)


def test_deferred_cooldown_abort_is_interruptible() -> None:
    """Abort during Retry-After cool-down must wake waiters without full sleep."""

    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    # Parking the only tile opens the deferred pass via the public API.
    assert progress.try_park_for_deferred_retry() is True

    started = threading.Event()
    errors: list[BaseException] = []

    def waiter() -> None:
        started.set()
        try:
            progress.wait_for_deferred_pass(retry_after=30.0)
        except BaseException as exc:  # noqa: BLE001 — capture for assertion
            errors.append(exc)

    thread = threading.Thread(target=waiter)
    thread.start()
    assert started.wait(timeout=1.0)
    time.sleep(0.05)
    progress.abort("stop during cooldown")
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert errors and isinstance(errors[0], NetworkError)


def test_fetch_summary_deferred_fields_append_after_circuit_breaker() -> None:
    """FetchSummary positional slots through circuit_breaker_frozen stay stable."""

    summary = FetchSummary(
        1,  # total
        1,  # done
        0,  # soft_failed
        False,  # aborted
        (),  # errors
        None,  # request_count
        None,  # retry_count
        None,  # max_inflight
        None,  # current_limit
        None,  # pressure_event_count
        None,  # circuit_breaker_trips
        False,  # circuit_breaker_frozen
        2,  # deferred_parked
        1,  # deferred_recovered
        0,  # deferred_failed
        (),  # unknowns
    )
    assert summary.errors == ()
    assert summary.circuit_breaker_frozen is False
    assert summary.deferred_parked == 2
    assert summary.deferred_recovered == 1
    assert summary.deferred_failed == 0


def test_deferred_pass_concurrency_at_most_aimd_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = cols = 3
    floor = 2
    real_progress = FetchProgress

    def progress_factory(*args: Any, **kwargs: Any) -> FetchProgress:
        kwargs.setdefault("deferred_park_fraction", 0.5)
        kwargs.setdefault("deferred_max_concurrent", floor)
        return real_progress(*args, **kwargs)

    monkeypatch.setattr("tilearray.array.FetchProgress", progress_factory)

    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}
    lock = threading.Lock()
    peak = {"value": 0}
    inflight = {"value": 0}

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        with lock:
            n = attempts.get(idx, 0) + 1
            attempts[idx] = n
            in_deferred = n > 1
            if in_deferred:
                inflight["value"] += 1
                peak["value"] = max(peak["value"], inflight["value"])
        try:
            if idx in {1, 2, 3} and n <= 1:
                return TileResponse(
                    data=b"slow",
                    content_type="text/plain",
                    status_code=429,
                    headers={"Retry-After": "0"},
                    url=request.url,
                    success=False,
                    error_message="HTTP 429",
                )
            return TileResponse(
                data=b"ok",
                content_type="image/tiff",
                status_code=200,
                headers={},
                url=request.url,
                success=True,
            )
        finally:
            if n > 1:
                with lock:
                    inflight["value"] -= 1

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=6, max_y=6, crs=CRS.EPSG_4326)
    lazy = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(2, 2),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        band_policy="first_band",
        compute=False,
        tile_decoder=_ones_decoder,
        max_concurrent_requests=8,
        min_concurrent_requests=floor,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        fetch_retries=0,
    )
    result = compute_mosaic(lazy)
    assert np.isfinite(result.values).all()
    assert result.attrs[_TILE_ERRORS_ATTR]["deferred_parked"] == 3
    assert peak["value"] <= floor
