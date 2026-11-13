"""Contract tests: public-boundary failures use TileArrayError taxonomy (#72)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tilearray.errors import (
    ConfigurationError,
    NetworkError,
    ParseError,
    TileArrayError,
    ValidationError,
)
from tilearray.fetch import FetchProgress
from tilearray.service.base import TileGeometry, detect_service_type, get_service
from tilearray.service.config import WCSConfig
from tilearray.service.wcs import WCSParser, WCSService
from tilearray.service.wmts import WMTSService
from tilearray.types import CRS, BoundingBox, Format

pytestmark = pytest.mark.unit


def test_detect_service_type_raises_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="Unable to detect") as exc_info:
        detect_service_type("https://example.com/unknown-endpoint")
    assert isinstance(exc_info.value, TileArrayError)


def test_wcs_missing_coverage_id_raises_validation_error() -> None:
    service = WCSService("http://example.com/wcs", coverage_id=None)
    geometry = TileGeometry(
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=16,
        height=16,
        crs=CRS.EPSG_4326,
    )

    with pytest.raises(ValidationError, match="coverage_id") as exc_info:
        service.build_tile_request(geometry)
    assert isinstance(exc_info.value, TileArrayError)


def test_wcs_unsupported_format_raises_validation_error() -> None:
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="cov-1",
        output_format=Format.GEOTIFF,
    )
    with pytest.raises(ValidationError, match="Unsupported WCS format"):
        service._coerce_format("image/unknown")


def test_wcs_parser_invalid_xml_raises_parse_error() -> None:
    parser = WCSParser("http://example.com/wcs")
    with pytest.raises(ParseError, match="Invalid XML") as exc_info:
        parser.parse_get_capabilities("not-xml<<<")
    assert isinstance(exc_info.value, TileArrayError)
    assert exc_info.value.cause is not None


def test_wcs_describe_coverage_http_failure_raises_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WCSService("http://example.com/wcs", coverage_id="cov-1")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("tilearray.service.wcs.http_get_capabilities_text", boom)

    with pytest.raises(NetworkError, match="DescribeCoverage") as exc_info:
        service.describe_coverage()
    assert isinstance(exc_info.value.cause, httpx.ConnectError)


def test_wcs_get_capabilities_http_failure_raises_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WCSService("http://example.com/wcs", coverage_id="cov-1")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise httpx.HTTPStatusError(
            "500",
            request=httpx.Request("GET", "http://example.com/wcs"),
            response=httpx.Response(500),
        )

    monkeypatch.setattr("tilearray.service.wcs.http_get_capabilities_text", boom)

    with pytest.raises(NetworkError, match="GetCapabilities"):
        service.get_capabilities()


def test_wmts_get_capabilities_http_failure_raises_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WMTSService(
        "http://example.com/wmts",
        layer="layer-1",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=0,
    )

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr("tilearray.service.wmts.http_get_capabilities_text", boom)

    with pytest.raises(NetworkError, match="WMTS GetCapabilities"):
        service.get_capabilities()


def test_wcs_config_build_service_wraps_http_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WCSConfig.from_url("http://example.com/wcs", coverage_id="missing")

    def fake_describe(
        self: WCSService, coverage_id: str | None = None, **params: Any
    ) -> Any:
        raise httpx.HTTPError("not found")

    monkeypatch.setattr(WCSService, "describe_coverage", fake_describe)

    with pytest.raises(NetworkError, match="Failed to validate coverage"):
        config.build_service()


def test_xyz_from_url_requires_zoom() -> None:
    with pytest.raises(ValidationError, match="zoom"):
        get_service("https://tiles.example/{z}/{x}/{y}.png")


def test_fetch_progress_call_max_concurrent_validation() -> None:
    with pytest.raises(ValidationError, match="call_max_concurrent"):
        FetchProgress(total=1, call_max_concurrent=0)
