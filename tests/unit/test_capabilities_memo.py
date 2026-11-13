"""Process-local GetCapabilities / DescribeCoverage memo (TTL + keying)."""

from __future__ import annotations

import httpx
import pytest
import respx

from tilearray import inspect_endpoint
from tilearray.service.capabilities_memo import (
    DEFAULT_CAPABILITIES_TTL_SECONDS,
    capabilities_memo_key,
    clear_capabilities_memo,
    set_capabilities_memo_ttl,
)
from tilearray.service.http import http_get_capabilities_text
from tilearray.service.wcs import WCSService
from tilearray.service.wmts import WMTSService

WCS_CAPS = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0"
                  xmlns:ows="http://www.opengis.net/ows/1.1"
                  version="2.0.1">
  <wcs:Contents>
    <wcs:CoverageSummary>
      <wcs:CoverageId>coverage-1</wcs:CoverageId>
    </wcs:CoverageSummary>
  </wcs:Contents>
</wcs:Capabilities>"""

WCS_DESCRIBE = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:CoverageDescription xmlns:wcs="http://www.opengis.net/wcs/2.0"
                         xmlns:gml="http://www.opengis.net/gml/3.2">
    <wcs:CoverageId>coverage-1</wcs:CoverageId>
</wcs:CoverageDescription>"""

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


@pytest.fixture
def frozen_clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Controllable monotonic clock for TTL tests."""

    import tilearray.service.capabilities_memo as memo

    now = [1000.0]
    monkeypatch.setattr(memo, "_CLOCK", lambda: now[0])
    return now


@pytest.mark.unit
def test_default_ttl_is_one_hour() -> None:
    assert DEFAULT_CAPABILITIES_TTL_SECONDS == 3600.0


@pytest.mark.unit
def test_memo_key_includes_url_params_and_auth_not_user_agent() -> None:
    key_a = capabilities_memo_key(
        "https://example.com/wcs?map=/a.map",
        {"request": "GetCapabilities", "service": "WCS"},
        {"Authorization": "Bearer a", "User-Agent": "ua-1"},
    )
    key_b = capabilities_memo_key(
        "https://example.com/wcs",
        {"map": "/a.map", "request": "GetCapabilities", "service": "WCS"},
        {"Authorization": "Bearer a", "User-Agent": "ua-2"},
    )
    key_slash = capabilities_memo_key(
        "https://example.com/wcs/",
        {"map": "/a.map", "request": "GetCapabilities", "service": "WCS"},
        {"Authorization": "Bearer a"},
    )
    key_other_auth = capabilities_memo_key(
        "https://example.com/wcs",
        {"map": "/a.map", "request": "GetCapabilities", "service": "WCS"},
        {"Authorization": "Bearer b", "User-Agent": "ua-1"},
    )
    key_other_params = capabilities_memo_key(
        "https://example.com/wcs",
        {"map": "/b.map", "request": "GetCapabilities", "service": "WCS"},
        {"Authorization": "Bearer a"},
    )
    assert key_a == key_b == key_slash
    assert key_a != key_other_auth
    assert key_a != key_other_params


@pytest.mark.unit
@respx.mock
def test_http_get_capabilities_text_hits_memo_within_ttl(
    frozen_clock: list[float],
) -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(200, text=WCS_CAPS)
    )
    params = {"service": "WCS", "request": "GetCapabilities", "version": "2.0.1"}
    first = http_get_capabilities_text("https://example.com/wcs", params=params)
    second = http_get_capabilities_text("https://example.com/wcs", params=params)
    assert first == second == WCS_CAPS
    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_http_get_capabilities_text_refetches_after_ttl(
    frozen_clock: list[float],
) -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(200, text=WCS_CAPS)
    )
    params = {"service": "WCS", "request": "GetCapabilities", "version": "2.0.1"}
    http_get_capabilities_text("https://example.com/wcs", params=params)
    frozen_clock[0] += DEFAULT_CAPABILITIES_TTL_SECONDS + 1.0
    http_get_capabilities_text("https://example.com/wcs", params=params)
    assert route.call_count == 2


@pytest.mark.unit
@respx.mock
def test_http_get_capabilities_text_refresh_bypasses_memo(
    frozen_clock: list[float],
) -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(200, text=WCS_CAPS)
    )
    params = {"service": "WCS", "request": "GetCapabilities", "version": "2.0.1"}
    http_get_capabilities_text("https://example.com/wcs", params=params)
    http_get_capabilities_text("https://example.com/wcs", params=params, refresh=True)
    assert route.call_count == 2


@pytest.mark.unit
@respx.mock
def test_different_auth_headers_do_not_share_memo() -> None:
    route = respx.get("https://private.example/wcs").mock(
        return_value=httpx.Response(200, text=WCS_CAPS)
    )
    params = {"service": "WCS", "request": "GetCapabilities", "version": "2.0.1"}
    http_get_capabilities_text(
        "https://private.example/wcs",
        params=params,
        headers={"Authorization": "Bearer one"},
    )
    http_get_capabilities_text(
        "https://private.example/wcs",
        params=params,
        headers={"Authorization": "Bearer two"},
    )
    assert route.call_count == 2


@pytest.mark.unit
@respx.mock
def test_wcs_get_capabilities_shares_memo_across_service_instances() -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(200, text=WCS_CAPS)
    )
    WCSService("https://example.com/wcs").get_capabilities()
    WCSService("https://example.com/wcs").get_capabilities()
    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_wcs_describe_coverage_hits_memo_and_refresh_refetches() -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(200, text=WCS_DESCRIBE)
    )
    service = WCSService("https://example.com/wcs", coverage_id="coverage-1")
    first = service.describe_coverage()
    second = WCSService(
        "https://example.com/wcs", coverage_id="coverage-1"
    ).describe_coverage()
    assert first.identifier == second.identifier == "coverage-1"
    assert route.call_count == 1

    service.describe_coverage(refresh=True)
    assert route.call_count == 2


@pytest.mark.unit
@respx.mock
def test_wmts_get_capabilities_hits_memo() -> None:
    route = respx.get("https://example.com/wmts").mock(
        return_value=httpx.Response(200, text=WMTS_CAPS)
    )
    WMTSService(
        "https://example.com/wmts",
        layer="layer-a",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix="0",
    ).get_capabilities()
    WMTSService(
        "https://example.com/wmts",
        layer="layer-a",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix="0",
    ).get_capabilities()
    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_inspect_endpoint_reuses_get_capabilities_memo() -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(200, text=WCS_CAPS)
    )
    inspect_endpoint("https://example.com/wcs", service_type="wcs")
    # Trailing slash is normalised in the memo key to match BaseService.base_url.
    inspect_endpoint("https://example.com/wcs/", service_type="wcs")
    WCSService("https://example.com/wcs").get_capabilities()
    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_html_capabilities_body_is_not_memoised() -> None:
    html = "<!DOCTYPE html><html><body>Login</body></html>"
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(200, text=html)
    )
    params = {"service": "WCS", "request": "GetCapabilities", "version": "2.0.1"}
    assert http_get_capabilities_text("https://example.com/wcs", params=params) == html
    route.mock(return_value=httpx.Response(200, text=WCS_CAPS))
    body = http_get_capabilities_text("https://example.com/wcs", params=params)
    assert body == WCS_CAPS
    assert route.call_count == 2


@pytest.mark.unit
@respx.mock
def test_failed_capabilities_response_is_not_memoised() -> None:
    route = respx.get("https://example.com/wcs").mock(
        return_value=httpx.Response(503, text="busy")
    )
    with pytest.raises(httpx.HTTPStatusError):
        http_get_capabilities_text(
            "https://example.com/wcs",
            params={"service": "WCS", "request": "GetCapabilities"},
        )
    route.mock(return_value=httpx.Response(200, text=WCS_CAPS))
    body = http_get_capabilities_text(
        "https://example.com/wcs",
        params={"service": "WCS", "request": "GetCapabilities"},
    )
    assert body == WCS_CAPS
    assert route.call_count == 2


@pytest.mark.unit
def test_set_capabilities_memo_ttl_restores_default() -> None:
    assert set_capabilities_memo_ttl(12.0) == 12.0
    assert set_capabilities_memo_ttl(None) == DEFAULT_CAPABILITIES_TTL_SECONDS
    clear_capabilities_memo()
