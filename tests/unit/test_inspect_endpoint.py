"""Offline tests for endpoint inspection / config building (#96)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from tilearray import (
    EndpointInspection,
    WCSConfig,
    WMSConfig,
    WMTSConfig,
    XYZConfig,
    inspect_capabilities,
    inspect_endpoint,
)
from tilearray.errors import NetworkError, ParseError, ValidationError
from tilearray.types import CRS, Format, ServiceTypeEnum

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "inspect_fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_inspect_wcs_capabilities_lists_coverages_and_builds_config() -> None:
    inspection = inspect_capabilities(
        _read("wcs_get_capabilities.xml"),
        service_type=ServiceTypeEnum.WCS,
        base_url="https://example.com/wcs?key=super-secret",
        params={"key": "super-secret"},
    )

    assert isinstance(inspection, EndpointInspection)
    assert [item.identifier for item in inspection.coverages] == [
        "demo__Elevation_DTM",
        "demo__Hillshade",
    ]
    assert inspection.coverages[0].title == "Elevation DTM"
    assert Format.GEOTIFF.value in inspection.formats
    text = inspection.summary()
    assert "super-secret" not in text
    assert "***" in text
    assert "demo__Elevation_DTM" in text

    with pytest.raises(ValidationError, match="coverage_id is required"):
        inspection.build_config()

    config = inspection.build_config(coverage_id="demo__Elevation_DTM")
    assert isinstance(config, WCSConfig)
    assert config.coverage_id == "demo__Elevation_DTM"
    assert config.params == {"key": "super-secret"}
    assert config.output_format == Format.GEOTIFF
    assert config.crs == CRS.EPSG_27700

    snippet = inspection.example_snippet(coverage_id="demo__Elevation_DTM")
    assert "WCSConfig.from_url" in snippet
    assert "super-secret" not in snippet
    assert "create_array" in snippet


def test_inspect_wcs_unknown_coverage_is_loud() -> None:
    inspection = inspect_capabilities(
        _read("wcs_get_capabilities.xml"),
        service_type="WCS",
        base_url="https://example.com/wcs",
    )
    with pytest.raises(ValidationError, match="Unknown coverage"):
        inspection.build_config(coverage_id="not-a-real-id")


def test_inspect_wmts_capabilities_and_unambiguous_layer() -> None:
    inspection = inspect_capabilities(
        _read("wmts_get_capabilities.xml"),
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    assert {item.identifier for item in inspection.layers} == {
        "blue_marble",
        "other_layer",
    }
    assert "GoogleMapsCompatible" in {
        item.identifier for item in inspection.matrix_sets
    }

    with pytest.raises(ValidationError, match="layer is required"):
        inspection.build_config(tile_matrix_set="GoogleMapsCompatible", tile_matrix=0)

    config = inspection.build_config(
        layer="blue_marble",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=1,
    )
    assert isinstance(config, WMTSConfig)
    assert config.layer == "blue_marble"
    assert config.tile_matrix_set == "GoogleMapsCompatible"
    assert config.tile_matrix == 1
    assert config.url_template is not None
    assert config.output_format == Format.JPEG
    assert config.crs == CRS.EPSG_3857

    with pytest.raises(ValidationError, match="tile_matrix_set is required"):
        inspection.build_config(layer="other_layer", tile_matrix=5)

    with pytest.raises(ValidationError, match="Unknown layer"):
        inspection.build_config(
            layer="missing",
            tile_matrix_set="GoogleMapsCompatible",
            tile_matrix=0,
        )

    with pytest.raises(ValidationError, match="Unknown tile_matrix"):
        inspection.build_config(
            layer="blue_marble",
            tile_matrix_set="GoogleMapsCompatible",
            tile_matrix=99,
        )


def test_inspect_wms_capabilities_lists_layers() -> None:
    inspection = inspect_capabilities(
        _read("wms_get_capabilities.xml"),
        service_type=ServiceTypeEnum.WMS,
        base_url="https://example.com/wms",
    )
    names = [item.identifier for item in inspection.layers]
    assert "Vegetation_Object_Model_2022" in names
    assert "Vegetation_Object_Model_2021" in names
    assert "image/png" in inspection.formats

    config = inspection.build_config(layers="Vegetation_Object_Model_2022")
    assert isinstance(config, WMSConfig)
    assert config.layers == "Vegetation_Object_Model_2022"
    assert config.crs == CRS.EPSG_27700
    assert config.output_format is None or config.output_format in {
        Format.PNG,
        Format.JPEG,
    }


def test_inspect_malformed_xml_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="Invalid XML"):
        inspect_capabilities(
            "<not-closed",
            service_type=ServiceTypeEnum.WCS,
            base_url="https://example.com/wcs",
        )


def test_inspect_html_login_response_raises_parse_error() -> None:
    with pytest.raises(ParseError, match="HTML"):
        inspect_capabilities(
            "<!DOCTYPE html><html><body>Login</body></html>",
            service_type=ServiceTypeEnum.WMTS,
            base_url="https://example.com/wmts?token=abc",
        )


def test_inspect_xyz_template_explains_no_capabilities() -> None:
    inspection = inspect_endpoint(
        "https://tile.example.com/{z}/{x}/{y}.png",
        headers={"Authorization": "Bearer secret-token"},
    )
    assert inspection.service_type is ServiceTypeEnum.XYZ
    text = str(inspection)
    assert "no GetCapabilities" in text or "no OGC GetCapabilities" in text
    assert "secret-token" not in text

    with pytest.raises(ValidationError, match="zoom"):
        inspection.build_config()

    config = inspection.build_config(zoom=10)
    assert isinstance(config, XYZConfig)
    assert config.zoom == 10
    assert config.headers["Authorization"] == "Bearer secret-token"


@respx.mock
def test_inspect_endpoint_fetches_wcs_capabilities() -> None:
    url = "https://example.com/spatialdata/demo/wcs"
    route = respx.get(url).mock(
        return_value=httpx.Response(200, text=_read("wcs_get_capabilities.xml"))
    )
    inspection = inspect_endpoint(url, params={"key": "k"})
    assert route.called
    request = route.calls.last.request
    assert "GetCapabilities" in str(request.url)
    assert "key=k" in str(request.url)
    assert inspection.service_type is ServiceTypeEnum.WCS
    assert inspection.coverages[0].identifier.startswith("demo__")


@respx.mock
def test_inspect_endpoint_network_error_is_actionable() -> None:
    url = "https://example.com/missing/wcs"
    respx.get(url).mock(return_value=httpx.Response(404, text="missing"))
    with pytest.raises(NetworkError, match="GetCapabilities"):
        inspect_endpoint(url)


def test_build_config_does_not_silently_change_format() -> None:
    inspection = inspect_capabilities(
        _read("wcs_get_capabilities.xml"),
        service_type=ServiceTypeEnum.WCS,
        base_url="https://example.com/wcs",
    )
    with pytest.raises(ValidationError, match="output_format"):
        inspection.build_config(
            coverage_id="demo__Elevation_DTM",
            output_format=Format.PNG,
        )
