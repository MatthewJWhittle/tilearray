"""Unit tests for service detection and registry helpers."""

from __future__ import annotations

import pytest

from tilearray.service.base import detect_service_type, get_service
from tilearray.service.wcs import WCSService
from tilearray.service.wms import WMSService
from tilearray.service.wmts import WMTSService
from tilearray.types import ServiceTypeEnum

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/wcs?service=WCS", ServiceTypeEnum.WCS),
        ("https://example.com/path/wcs", ServiceTypeEnum.WCS),
        ("https://example.com/wms", ServiceTypeEnum.WMS),
        ("https://example.com/wmts", ServiceTypeEnum.WMTS),
        ("https://tiles.example/{z}/{x}/{y}.png", ServiceTypeEnum.XYZ),
    ],
)
def test_detect_service_type(url: str, expected: ServiceTypeEnum) -> None:
    assert detect_service_type(url) == expected


def test_detect_service_type_uses_fallback() -> None:
    assert (
        detect_service_type("https://example.com/data", fallback=ServiceTypeEnum.WCS)
        == ServiceTypeEnum.WCS
    )


def test_detect_service_type_raises_when_unknown() -> None:
    with pytest.raises(ValueError, match="Unable to detect service type"):
        detect_service_type("https://example.com/unknown-endpoint")


def test_get_service_builds_registered_implementation() -> None:
    service = get_service("https://example.com/wcs", coverage_id="cov-1")
    assert isinstance(service, WCSService)
    assert service.coverage_id == "cov-1"


def test_get_service_builds_wms_implementation() -> None:
    service = get_service("https://example.com/wms", layers="roads")
    assert isinstance(service, WMSService)
    assert service.layers == "roads"


def test_get_service_builds_wmts_implementation() -> None:
    service = get_service(
        "https://example.com/wmts",
        layer="roads",
        tile_matrix_set="EPSG4326",
        tile_matrix=10,
    )
    assert isinstance(service, WMTSService)
    assert service.layer == "roads"
    assert service.tile_matrix == 10
