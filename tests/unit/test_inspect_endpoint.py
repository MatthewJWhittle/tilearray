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


def test_wmts_summary_and_example_snippet_cover_matrix_details() -> None:
    inspection = inspect_capabilities(
        _read("wmts_get_capabilities.xml"),
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts?token=secret",
        headers={"X-API-Key": "secret-key"},
    )
    text = inspection.summary()
    assert "Tile matrix sets" in text
    assert "GoogleMapsCompatible" in text
    assert "secret" not in text
    assert "***" in text

    # Single matrix in EPSG4326 → tile_matrix auto-fills
    config = inspection.build_config(
        layer="other_layer",
        tile_matrix_set="EPSG4326",
    )
    assert config.tile_matrix == 5
    snippet = inspection.example_snippet(
        layer="blue_marble",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=0,
    )
    assert "WMTSConfig.from_url" in snippet
    assert "url_template=" in snippet
    assert "secret" not in snippet


def test_wms_example_snippet_and_empty_part_handling() -> None:
    inspection = inspect_capabilities(
        _read("wms_get_capabilities.xml"),
        service_type=ServiceTypeEnum.WMS,
        base_url="https://example.com/wms",
    )
    snippet = inspection.example_snippet(layers="Vegetation_Object_Model_2022")
    assert "WMSConfig.from_url" in snippet
    assert "Vegetation_Object_Model_2022" in snippet

    with pytest.raises(ValidationError, match="Unknown layer"):
        inspection.build_config(layers="Vegetation_Object_Model_2022,missing_layer")


def test_xyz_example_snippet_and_placeholder_warning() -> None:
    inspection = inspect_endpoint(
        "https://tiles.example.com/map/0/0/0.png",
        service_type=ServiceTypeEnum.XYZ,
    )
    assert inspection.warnings
    assert any("placeholders" in warning for warning in inspection.warnings)
    text = str(inspection)
    assert "Discovery:" in text or "no GetCapabilities" in text or "no OGC" in text

    ok = inspect_endpoint("https://tile.example.com/{z}/{x}/{y}.png")
    snippet = ok.example_snippet(zoom=8)
    assert "XYZConfig.from_url" in snippet
    assert "zoom=8" in snippet


def test_single_coverage_auto_fills_and_empty_caps_warn() -> None:
    single = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0"
                  xmlns:ows="http://www.opengis.net/ows/1.1"
                  version="2.0.1">
    <ows:ServiceIdentification><ows:Title>One</ows:Title></ows:ServiceIdentification>
    <wcs:Contents>
        <wcs:CoverageSummary>
            <wcs:CoverageId>only-one</wcs:CoverageId>
        </wcs:CoverageSummary>
    </wcs:Contents>
    <wcs:SupportedFormat>image/tiff</wcs:SupportedFormat>
    <wcs:SupportedCRS>EPSG:4326</wcs:SupportedCRS>
</wcs:Capabilities>"""
    inspection = inspect_capabilities(
        single, service_type="WCS", base_url="https://example.com/wcs"
    )
    config = inspection.build_config()
    assert config.coverage_id == "only-one"
    assert config.crs == CRS.EPSG_4326

    empty = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0"
                  xmlns:ows="http://www.opengis.net/ows/1.1"
                  version="2.0.1">
    <ows:ServiceIdentification><ows:Title>Empty</ows:Title></ows:ServiceIdentification>
    <wcs:Contents/>
</wcs:Capabilities>"""
    empty_insp = inspect_capabilities(
        empty, service_type=ServiceTypeEnum.WCS, base_url="https://example.com/wcs"
    )
    assert empty_insp.warnings
    text = empty_insp.summary()
    assert "none" in text.lower()
    with pytest.raises(ValidationError, match="coverage_id"):
        empty_insp.build_config()


def test_inspect_rejects_unknown_service_type_string() -> None:
    with pytest.raises(ValidationError, match="service_type"):
        inspect_endpoint(
            "https://example.com/mystery",
            service_type="NOT_A_SERVICE",
        )


def test_build_config_rejects_bad_crs_and_format_strings() -> None:
    inspection = inspect_capabilities(
        _read("wcs_get_capabilities.xml"),
        service_type=ServiceTypeEnum.WCS,
        base_url="https://example.com/wcs",
    )
    with pytest.raises(ValidationError, match="Unsupported output_format"):
        inspection.build_config(
            coverage_id="demo__Elevation_DTM",
            output_format="image/not-a-real-mime",
        )
    with pytest.raises(ValidationError, match="Unsupported crs"):
        inspection.build_config(
            coverage_id="demo__Elevation_DTM",
            crs="not-a-crs",
        )
    with pytest.raises(ValidationError, match="advertised CRS"):
        inspection.build_config(
            coverage_id="demo__Elevation_DTM",
            crs=CRS.EPSG_4326,
        )
    # Integer / digit-string CRS that is advertised still works
    config = inspection.build_config(
        coverage_id="demo__Elevation_DTM",
        crs=27700,
    )
    assert config.crs == CRS.EPSG_27700
    config2 = inspection.build_config(
        coverage_id="demo__Elevation_DTM",
        crs="27700",
        output_format=Format.GEOTIFF,
    )
    assert config2.crs == CRS.EPSG_27700


def test_wmts_non_integer_matrix_and_missing_tms() -> None:
    from tilearray.inspect import _coerce_tile_matrix

    with pytest.raises(ValidationError, match="Unknown tile_matrix"):
        _coerce_tile_matrix("LevelZero", ("0", "1"))
    with pytest.raises(ValidationError, match="not an integer"):
        _coerce_tile_matrix("EPSG:4326:5", ("EPSG:4326:5",))


def test_wmts_empty_matrix_set_links_require_explicit_tms() -> None:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>orphan</ows:Identifier>
    </Layer>
  </Contents>
</Capabilities>"""
    inspection = inspect_capabilities(
        xml, service_type=ServiceTypeEnum.WMTS, base_url="https://example.com/wmts"
    )
    with pytest.raises(ValidationError, match="tile_matrix_set"):
        inspection.build_config(layer="orphan")


def test_wms_parser_malformed_and_namespace_fallback() -> None:
    from tilearray.errors import ParseError
    from tilearray.service.wms import WMSParser

    with pytest.raises(ParseError, match="Invalid XML"):
        WMSParser().parse_get_capabilities("<WMS_Capabilities>")

    # Custom-namespace Capability so default xpaths miss and the GetMap iter
    # fallback collects formats.
    weird = """<?xml version='1.0' encoding='UTF-8'?>
<WMS_Capabilities xmlns:m="http://example.com/wms-custom" version="1.1.1">
  <Service><Title>Legacy</Title></Service>
  <m:Capability>
    <m:Request>
      <m:GetMap>
        <m:Format>image/png</m:Format>
        <m:Format></m:Format>
        <m:Other>ignore</m:Other>
      </m:GetMap>
    </m:Request>
    <Layer>
      <Name>only</Name>
      <Title>Only</Title>
      <SRS>EPSG:4326</SRS>
      <SRS>EPSG:4326</SRS>
      <Style><Name></Name></Style>
    </Layer>
  </m:Capability>
</WMS_Capabilities>"""
    caps = WMSParser().parse_get_capabilities(weird)
    assert caps.title == "Legacy"
    assert "image/png" in caps.formats
    assert caps.layers[0].name == "only"
    assert caps.layers[0].crs == ["EPSG:4326"]

    inspection = inspect_capabilities(
        weird, service_type=ServiceTypeEnum.WMS, base_url="https://example.com/wms"
    )
    # Single named layer → layers= can auto-fill
    config = inspection.build_config()
    assert config.layers == "only"
    assert config.crs == CRS.EPSG_4326


def test_inspect_capabilities_xyz_passthrough() -> None:
    inspection = inspect_capabilities(
        "",
        service_type=ServiceTypeEnum.XYZ,
        base_url="https://tile.example.com/{z}/{x}/{y}.png",
        params={"key": "abc"},
    )
    assert inspection.service_type is ServiceTypeEnum.XYZ
    config = inspection.build_config(zoom=3, params={"key": "override"})
    assert config.params == {"key": "override"}


@respx.mock
def test_inspect_endpoint_fetches_wms_and_wmts() -> None:
    wms_url = "https://example.com/spatialdata/demo/wms"
    respx.get(wms_url).mock(
        return_value=httpx.Response(200, text=_read("wms_get_capabilities.xml"))
    )
    wms = inspect_endpoint(wms_url, service_type=ServiceTypeEnum.WMS)
    assert wms.layers

    wmts_url = "https://example.com/path/wmts"
    respx.get(wmts_url).mock(
        return_value=httpx.Response(200, text=_read("wmts_get_capabilities.xml"))
    )
    wmts = inspect_endpoint(wmts_url)
    assert wmts.matrix_sets


def test_redact_mapping_rewrites_embedded_urls() -> None:
    from tilearray.inspect import _redact_mapping

    out = _redact_mapping(
        {
            "callback": "https://example.com/hook?token=secret",
            "plain": "ok",
            "password": "nope",
        }
    )
    assert out["password"] == "***"
    assert "secret" not in out["callback"]
    assert out["plain"] == "ok"


def test_unique_format_outside_library_enum_stays_none() -> None:
    from tilearray.inspect import _resolve_format

    assert (
        _resolve_format(
            None, advertised=("application/x-custom",), default_when_unique=True
        )
        is None
    )


def test_crs_urn_matching_and_crs84_normalize() -> None:
    from tilearray.inspect import _normalize_crs_token, _resolve_crs

    assert _normalize_crs_token("urn:ogc:def:crs:EPSG::3857") == "EPSG:3857"
    assert _normalize_crs_token("urn:ogc:def:crs:OGC:1.3:CRS84") == "CRS:84"
    crs = _resolve_crs(
        None,
        advertised=("urn:ogc:def:crs:EPSG::4326",),
        default_when_unique=True,
    )
    assert crs == CRS.EPSG_4326
