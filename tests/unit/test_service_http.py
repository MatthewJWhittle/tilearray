"""Unit tests for shared capabilities HTTP helpers."""

from __future__ import annotations

import httpx
import pytest
import respx

from tilearray.errors import NetworkError
from tilearray.service.capabilities_memo import clear_capabilities_memo
from tilearray.service.config import WCSConfig
from tilearray.service.http import (
    DEFAULT_HTTP_RETRIES,
    DEFAULT_HTTP_TIMEOUT,
    compose_http_headers,
    compose_http_params,
    default_headers,
    http_get,
    http_get_capabilities_text,
    make_sync_client,
    resolve_retries,
    resolve_timeout,
)
from tilearray.service.wmts import WMTSService
from tilearray.types import CRS

WCS_DESCRIBE = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:CoverageDescription xmlns:wcs="http://www.opengis.net/wcs/2.0"
                         xmlns:gml="http://www.opengis.net/gml/3.2">
    <wcs:CoverageId>c</wcs:CoverageId>
</wcs:CoverageDescription>"""

WMS_CAPS = """<?xml version='1.0' encoding='UTF-8'?>
<WMS_Capabilities version="1.3.0"
                  xmlns="http://www.opengis.net/wms"
                  xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Capability>
    <Layer>
      <Layer queryable="1">
        <Name>layer-a</Name>
        <Title>Layer A</Title>
        <CRS>EPSG:3857</CRS>
      </Layer>
    </Layer>
  </Capability>
</WMS_Capabilities>"""

WMTS_CAPS = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1"
              version="1.0.0">
  <Contents>
    <Layer>
      <ows:Identifier>layer-a</ows:Identifier>
      <ows:Title>Layer A</ows:Title>
      <Format>image/png</Format>
      <TileMatrixSetLink>
        <TileMatrixSet>GoogleMapsCompatible</TileMatrixSet>
      </TileMatrixSetLink>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>GoogleMapsCompatible</ows:Identifier>
      <ows:SupportedCRS>EPSG:3857</ows:SupportedCRS>
    </TileMatrixSet>
  </Contents>
</Capabilities>"""


@pytest.fixture(autouse=True)
def _clear_caps_memo() -> None:
    clear_capabilities_memo()
    yield
    clear_capabilities_memo()


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Capture plan-time backoff sleeps without waiting."""

    waits: list[float] = []

    def _sleep(seconds: float) -> None:
        waits.append(float(seconds))

    monkeypatch.setattr("tilearray.service.http.time.sleep", _sleep)
    return waits


@pytest.mark.unit
def test_resolve_timeout_uses_config_then_default() -> None:
    assert resolve_timeout(None) == DEFAULT_HTTP_TIMEOUT
    assert resolve_timeout({}) == DEFAULT_HTTP_TIMEOUT
    assert resolve_timeout({"fetch_timeout": 12.5}) == 12.5
    assert resolve_timeout({"fetch_timeout": 0}) == DEFAULT_HTTP_TIMEOUT
    assert resolve_timeout({"fetch_timeout": "nope"}) == DEFAULT_HTTP_TIMEOUT


@pytest.mark.unit
def test_resolve_retries_uses_config_then_default() -> None:
    assert resolve_retries(None) == DEFAULT_HTTP_RETRIES
    assert resolve_retries({}) == DEFAULT_HTTP_RETRIES
    assert resolve_retries({"fetch_retries": 0}) == 0
    assert resolve_retries({"fetch_retries": 2}) == 2
    assert resolve_retries({"fetch_retries": -1}) == DEFAULT_HTTP_RETRIES
    assert resolve_retries({"fetch_retries": "nope"}) == DEFAULT_HTTP_RETRIES


@pytest.mark.unit
def test_compose_http_headers_overlays_config() -> None:
    headers = compose_http_headers(
        {"headers": {"User-Agent": "override/1.0", "X-Key": "a"}},
        {"X-Peer": "1"},
    )
    assert headers["User-Agent"] == "override/1.0"
    assert headers["X-Key"] == "a"
    assert headers["X-Peer"] == "1"
    assert default_headers()["User-Agent"].startswith("tilearray/")


@pytest.mark.unit
def test_compose_http_params_merges_config() -> None:
    params = compose_http_params(
        {"service": "WCS", "request": "GetCapabilities"},
        {"params": {"username": "u", "password": "p"}},
        {"token": "t"},
    )
    assert params == {
        "service": "WCS",
        "request": "GetCapabilities",
        "username": "u",
        "password": "p",
        "token": "t",
    }


@pytest.mark.unit
@respx.mock
def test_http_get_raises_on_error_status(no_sleep: list[float]) -> None:
    route = respx.get("https://example.com/caps").mock(
        return_value=httpx.Response(503, text="busy")
    )
    with pytest.raises(httpx.HTTPStatusError, match="after 4 attempts"):
        http_get("https://example.com/caps")
    assert route.call_count == DEFAULT_HTTP_RETRIES + 1
    assert len(no_sleep) == DEFAULT_HTTP_RETRIES


@pytest.mark.unit
@respx.mock
def test_http_get_retries_500_then_succeeds(no_sleep: list[float]) -> None:
    route = respx.get("https://example.com/wcs").mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, text=WCS_DESCRIBE),
        ]
    )
    response = http_get("https://example.com/wcs", retries=2)
    assert response.status_code == 200
    assert route.call_count == 2
    assert len(no_sleep) == 1


@pytest.mark.unit
@respx.mock
def test_http_get_retries_read_timeout_then_succeeds(no_sleep: list[float]) -> None:
    route = respx.get("https://example.com/wcs").mock(
        side_effect=[
            httpx.ReadTimeout("timed out"),
            httpx.Response(200, text=WCS_DESCRIBE),
        ]
    )
    response = http_get("https://example.com/wcs", retries=2)
    assert response.status_code == 200
    assert route.call_count == 2
    assert len(no_sleep) == 1


@pytest.mark.unit
@respx.mock
def test_http_get_honours_retry_after_on_429(no_sleep: list[float]) -> None:
    route = respx.get("https://example.com/wcs").mock(
        side_effect=[
            httpx.Response(429, text="slow", headers={"Retry-After": "1"}),
            httpx.Response(200, text=WCS_DESCRIBE),
        ]
    )
    response = http_get("https://example.com/wcs", retries=2)
    assert response.status_code == 200
    assert route.call_count == 2
    assert no_sleep == pytest.approx([1.0])


@pytest.mark.unit
@respx.mock
def test_http_get_exhausted_500_names_attempt_count(no_sleep: list[float]) -> None:
    budget = 2
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(httpx.HTTPStatusError, match="after 3 attempts"):
        http_get("https://example.com/wcs", retries=budget)
    assert route.call_count == budget + 1
    assert len(no_sleep) == budget


@pytest.mark.unit
@pytest.mark.parametrize("status", [401, 404])
@respx.mock
def test_http_get_client_errors_fail_fast(status: int) -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(status, text="nope")
    )
    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        http_get("https://example.com/wcs", retries=3)
    assert route.call_count == 1
    assert "after" not in str(exc_info.value)


@pytest.mark.unit
@respx.mock
def test_http_get_403_fail_fast_not_retried() -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(403, text="forbidden")
    )
    with pytest.raises(httpx.HTTPStatusError):
        http_get("https://example.com/wcs", retries=3)
    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_build_service_retries_describe_coverage_500(
    no_sleep: list[float],
) -> None:
    url = "https://example.com/wcs"
    route = respx.get(url).mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, text=WCS_DESCRIBE),
        ]
    )
    service = WCSConfig.from_url(
        url, coverage_id="c", crs=CRS.EPSG_27700, fetch_retries=2
    ).build_service()
    assert service is not None
    assert route.call_count == 2
    assert len(no_sleep) == 1


@pytest.mark.unit
@respx.mock
def test_build_service_exhausted_retries_name_attempts(
    no_sleep: list[float],
) -> None:
    url = "https://example.com/wcs"
    budget = 2
    route = respx.get(url).mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(NetworkError, match="after 3 attempts") as exc_info:
        WCSConfig.from_url(
            url, coverage_id="c", crs=CRS.EPSG_27700, fetch_retries=budget
        ).build_service()
    assert route.call_count == budget + 1
    assert "Failed to validate coverage" in str(exc_info.value)


@pytest.mark.unit
@respx.mock
def test_build_service_404_fail_fast() -> None:
    url = "https://example.com/wcs"
    route = respx.get(url).mock(return_value=httpx.Response(404, text="missing"))
    with pytest.raises(NetworkError, match="Failed to validate coverage"):
        WCSConfig.from_url(
            url, coverage_id="c", crs=CRS.EPSG_27700, fetch_retries=3
        ).build_service()
    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_wms_and_wmts_get_capabilities_retry_via_shared_helper(
    no_sleep: list[float],
) -> None:
    wms_url = "https://example.com/wms"
    wmts_url = "https://example.com/wmts"
    wms_route = respx.get(wms_url).mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, text=WMS_CAPS),
        ]
    )
    wmts_route = respx.get(wmts_url).mock(
        side_effect=[
            httpx.Response(503, text="busy"),
            httpx.Response(200, text=WMTS_CAPS),
        ]
    )

    # WMS inspect / caps share http_get_capabilities_text (no WMSService GET helper).
    wms_body = http_get_capabilities_text(
        wms_url,
        params={"service": "WMS", "request": "GetCapabilities", "version": "1.3.0"},
        retries=2,
    )
    assert "layer-a" in wms_body
    assert wms_route.call_count == 2

    caps = WMTSService(
        wmts_url,
        layer="layer-a",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix="0",
        fetch_retries=2,
    ).get_capabilities()
    assert caps is not None
    assert wmts_route.call_count == 2
    assert len(no_sleep) == 2


@pytest.mark.unit
@respx.mock
def test_http_get_capabilities_text_passes_retries(no_sleep: list[float]) -> None:
    route = respx.get("https://example.com/wcs").mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, text="<ok/>"),
        ]
    )
    body = http_get_capabilities_text(
        "https://example.com/wcs",
        params={"service": "WCS", "request": "GetCapabilities"},
        retries=2,
    )
    assert body == "<ok/>"
    assert route.call_count == 2


@pytest.mark.unit
def test_make_sync_client_applies_timeout_and_limits() -> None:
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    client = make_sync_client(timeout=12.5, limits=limits)
    try:
        # Single float is the read budget; connect uses the default ceiling (capped).
        assert client.timeout.read == 12.5
        assert client.timeout.connect == 10.0
        # httpx stores Limits on the transport; constructing with limits= is enough.
        assert limits.max_connections == 4
        assert isinstance(client, httpx.Client)
    finally:
        client.close()


@pytest.mark.unit
def test_make_sync_client_splits_connect_and_read() -> None:
    client = make_sync_client(connect_timeout=3.0, read_timeout=22.0)
    try:
        assert client.timeout.connect == 3.0
        assert client.timeout.read == 22.0
        assert client.timeout.write == 22.0
        assert client.timeout.pool == 3.0
    finally:
        client.close()
