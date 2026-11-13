from pathlib import Path

import httpx
import pytest
import respx
from hypothesis import given, settings
from hypothesis import strategies as st

from tilearray.errors import NetworkError, ValidationError
from tilearray.service.base import TileGeometry
from tilearray.service.config import WCSConfig, XYZConfig
from tilearray.service.http import DEFAULT_HTTP_TIMEOUT, default_headers
from tilearray.service.wcs import WCSParser, WCSService, normalize_crs_reference
from tilearray.service.xyz import XYZService
from tilearray.types import CRS, BoundingBox, CoverageDescription, Format

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "wcs_fixtures"


def test_wcs_parser_parses_capabilities_example():
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0"
                  xmlns:ows="http://www.opengis.net/ows/1.1"
                  version="2.0.1">
    <ows:ServiceIdentification>
        <ows:Title>Example Service</ows:Title>
        <ows:Abstract>Sample abstract</ows:Abstract>
    </ows:ServiceIdentification>
    <wcs:Contents>
        <wcs:CoverageSummary>
            <wcs:CoverageId>coverage-1</wcs:CoverageId>
        </wcs:CoverageSummary>
    </wcs:Contents>
    <wcs:SupportedFormat>image/tiff</wcs:SupportedFormat>
    <wcs:SupportedCRS>EPSG:4326</wcs:SupportedCRS>
</wcs:Capabilities>"""

    parser = WCSParser("http://example.com/wcs")
    capabilities = parser.parse_get_capabilities(xml)

    assert capabilities.service_title == "Example Service"
    assert capabilities.supported_formats == [Format.GEOTIFF]
    assert capabilities.supported_crs == [CRS.EPSG_4326]
    assert capabilities.coverages[0].identifier == "coverage-1"


def test_wcs_service_build_tile_request():
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )

    geometry = TileGeometry(
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
    )

    request = service.build_tile_request(geometry)

    assert request.params["coverageId"] == "coverage-1"
    assert request.params["width"] == "256"
    assert request.params["height"] == "256"
    subset_params = request.params["subset"]
    assert isinstance(subset_params, list)
    assert any(part.startswith("Long(") for part in subset_params)
    assert any(part.startswith("Lat(") for part in subset_params)
    assert request.output_format == Format.GEOTIFF
    assert request.crs == CRS.EPSG_4326
    assert request.bbox == geometry.bbox


def test_wcs_service_build_tile_request_includes_headers():
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        headers={"User-Agent": "tilearray-test/1.0"},
    )
    geometry = TileGeometry(
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
    )

    request = service.build_tile_request(
        geometry,
        headers={"X-Peer-Test": "enabled"},
    )

    assert request.headers == {
        "User-Agent": "tilearray-test/1.0",
        "X-Peer-Test": "enabled",
    }


def test_wcs_service_requires_coverage_id():
    service = WCSService("http://example.com/wcs")
    geometry = TileGeometry(
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=16,
        height=16,
        crs=CRS.EPSG_4326,
    )

    with pytest.raises(ValidationError):
        service.build_tile_request(geometry)


def test_wcs_plan_tiles_with_resolution():
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1000, max_y=1000, crs=CRS.EPSG_4326)

    tiles = list(service.plan_tiles(bbox, (500, 500), resolution=(1.0, 1.0)))
    assert len(tiles) == 4
    assert {tile.width for tile in tiles} == {500}
    assert {tile.height for tile in tiles} == {500}


def test_wcs_plan_tiles_fractional_leftover_is_not_subpixel():
    """Fractional span rounds to whole pixels; every request stays ≥ 1 native pixel."""
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1024.1, max_y=1024.1, crs=CRS.EPSG_27700)
    res = (1.0, 1.0)
    tiles = list(service.plan_tiles(bbox, (1024, 1024), resolution=res))

    assert tiles
    # round(1024.1) → 1024; count-preserving snap does not invent a 1025th pixel.
    assert max(tile.bbox.max_x for tile in tiles) == pytest.approx(1024.0)
    assert max(tile.bbox.max_y for tile in tiles) == pytest.approx(1024.0)
    for tile in tiles:
        assert tile.bbox.max_x - tile.bbox.min_x >= res[0] - 1e-9
        assert tile.bbox.max_y - tile.bbox.min_y >= res[1] - 1e-9
        assert tile.width >= 1
        assert tile.height >= 1


def test_wcs_plan_tiles_epsilon_boundary_stays_whole_pixels():
    """Sub-resolution leftovers round away; requests never go sub-pixel."""
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
    )
    for leftover in (0.1, 0.09, 0.05):
        bbox = BoundingBox(
            min_x=0,
            min_y=0,
            max_x=1024.0 + leftover,
            max_y=512.0 + leftover,
            crs=CRS.EPSG_27700,
        )
        tiles = list(service.plan_tiles(bbox, (1024, 512), resolution=(1.0, 1.0)))
        assert max(tile.bbox.max_x for tile in tiles) == pytest.approx(1024.0)
        assert max(tile.bbox.max_y for tile in tiles) == pytest.approx(512.0)
        for tile in tiles:
            assert tile.bbox.max_x - tile.bbox.min_x >= 1.0 - 1e-9
            assert tile.bbox.max_y - tile.bbox.min_y >= 1.0 - 1e-9


def test_wcs_plan_tiles_already_on_grid_unchanged():
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1024, max_y=1024, crs=CRS.EPSG_4326)
    tiles = list(service.plan_tiles(bbox, (1024, 1024), resolution=(1.0, 1.0)))

    assert len(tiles) == 1
    tile = tiles[0]
    assert tile.bbox.min_x == bbox.min_x
    assert tile.bbox.min_y == bbox.min_y
    assert tile.bbox.max_x == bbox.max_x
    assert tile.bbox.max_y == bbox.max_y
    assert tile.width == 1024
    assert tile.height == 1024


def test_wcs_plan_tiles_rejects_non_positive_resolution():
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=10, crs=CRS.EPSG_4326)
    with pytest.raises(ValidationError, match="resolution"):
        list(service.plan_tiles(bbox, (8, 8), resolution=(0.0, 1.0)))


def test_wcs_plan_tiles_rejects_invalid_origin():
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=10, crs=CRS.EPSG_4326)
    with pytest.raises(ValidationError, match="origin"):
        list(service.plan_tiles(bbox, (8, 8), resolution=(1.0, 1.0), origin="bad"))


def test_wcs_plan_tiles_respects_custom_origin():
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
    )
    bbox = BoundingBox(
        min_x=105.2, min_y=205.2, max_x=115.2, max_y=215.2, crs=CRS.EPSG_27700
    )
    tiles = list(
        service.plan_tiles(bbox, (10, 10), resolution=(1.0, 1.0), origin=(100.0, 200.0))
    )
    assert tiles
    # 10-unit span → 10 px; min floors onto origin+(5,5)*res.
    assert min(tile.bbox.min_x for tile in tiles) == 105.0
    assert min(tile.bbox.min_y for tile in tiles) == 205.0
    assert max(tile.bbox.max_x for tile in tiles) == 115.0
    assert max(tile.bbox.max_y for tile in tiles) == 215.0


@given(
    min_x=st.floats(min_value=-1_000.0, max_value=1_000.0, allow_nan=False),
    min_y=st.floats(min_value=-1_000.0, max_value=1_000.0, allow_nan=False),
    width=st.floats(min_value=0.01, max_value=3_000.0, allow_nan=False),
    height=st.floats(min_value=0.01, max_value=3_000.0, allow_nan=False),
    chunk=st.integers(min_value=1, max_value=1024),
    res=st.floats(min_value=0.25, max_value=4.0, allow_nan=False),
)
@settings(max_examples=40, deadline=None)
def test_wcs_plan_tiles_never_emits_subpixel_requests_property(
    min_x: float,
    min_y: float,
    width: float,
    height: float,
    chunk: int,
    res: float,
) -> None:
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
    )
    bbox = BoundingBox(
        min_x=min_x,
        min_y=min_y,
        max_x=min_x + width,
        max_y=min_y + height,
        crs=CRS.EPSG_27700,
    )
    tiles = list(service.plan_tiles(bbox, (chunk, chunk), resolution=(res, res)))
    assert tiles
    n_px = max(1, int(round(width / res)))
    n_py = max(1, int(round(height / res)))
    mosaic_w = max(tile.bbox.max_x for tile in tiles) - min(
        tile.bbox.min_x for tile in tiles
    )
    mosaic_h = max(tile.bbox.max_y for tile in tiles) - min(
        tile.bbox.min_y for tile in tiles
    )
    assert mosaic_w == pytest.approx(n_px * res)
    assert mosaic_h == pytest.approx(n_py * res)
    for tile in tiles:
        assert tile.bbox.max_x - tile.bbox.min_x >= res - 1e-9
        assert tile.bbox.max_y - tile.bbox.min_y >= res - 1e-9
        assert tile.width >= 1
        assert tile.height >= 1


def test_wcs_config_build_service_raises_for_missing_coverage(monkeypatch):
    config = WCSConfig.from_url("http://example.com/wcs", coverage_id="missing")

    def fake_describe(self, coverage_id=None, **params):
        raise httpx.HTTPError("not found")

    monkeypatch.setattr(WCSService, "describe_coverage", fake_describe)

    with pytest.raises(NetworkError, match="missing"):
        config.build_service()


def test_wcs_parser_parses_describe_coverage_with_extent() -> None:
    xml = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:CoverageDescription xmlns:wcs="http://www.opengis.net/wcs/2.0"
                         xmlns:gml="http://www.opengis.net/gml/3.2"
                         xmlns:ows="http://www.opengis.net/ows/1.1">
    <gml:identifier>coverage-1</gml:identifier>
    <gml:name>Elevation</gml:name>
    <gml:description>DTM</gml:description>
    <ows:Keywords><ows:Keyword>lidar</ows:Keyword></ows:Keywords>
    <wcs:SupportedFormat>image/tiff</wcs:SupportedFormat>
    <wcs:SupportedCRS>EPSG:4326</wcs:SupportedCRS>
    <wcs:NativeCRS>EPSG:27700</wcs:NativeCRS>
    <gml:boundedBy>
        <gml:Envelope>
            <gml:lowerCorner>0 0</gml:lowerCorner>
            <gml:upperCorner>10 10</gml:upperCorner>
        </gml:Envelope>
    </gml:boundedBy>
    <gml:TimePeriod>
        <gml:beginPosition>2020-01-01T00:00:00Z</gml:beginPosition>
        <gml:endPosition>2020-12-31T23:59:59Z</gml:endPosition>
    </gml:TimePeriod>
</wcs:CoverageDescription>"""

    parser = WCSParser("http://example.com/wcs")
    description = parser.parse_describe_coverage(xml)

    assert description.identifier == "coverage-1"
    assert description.title == "Elevation"
    assert description.keywords == ["lidar"]
    assert description.supported_formats == [Format.GEOTIFF]
    assert description.spatial_extent is not None
    assert description.spatial_extent.bbox.min_x == 0
    assert description.temporal_extent is not None
    assert description.temporal_extent.start_time is not None


@respx.mock
def test_wcs_service_get_coverage_handles_request_errors() -> None:
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )
    service.set_coverage_metadata(
        CoverageDescription(
            identifier="coverage-1",
            axis_labels={"EPSG:4326": ("Long", "Lat")},
        )
    )

    respx.get("http://example.com/wcs").mock(
        return_value=httpx.Response(502, text="bad gateway")
    )

    response = service.get_coverage(
        "coverage-1",
        BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        64,
        64,
    )

    assert response.success is False
    assert response.status_code == 502


@respx.mock
def test_wcs_describe_coverage_sends_config_headers_and_params() -> None:
    """Private-server auth on config must reach DescribeCoverage (#57)."""

    captured: dict[str, object] = {}
    describe_xml = """<?xml version='1.0' encoding='UTF-8'?>
<wcs:CoverageDescription xmlns:wcs="http://www.opengis.net/wcs/2.0"
                         xmlns:gml="http://www.opengis.net/gml/3.2">
    <wcs:CoverageId>coverage-1</wcs:CoverageId>
</wcs:CoverageDescription>"""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, text=describe_xml)

    respx.get("https://private.example/wcs").mock(side_effect=handler)

    service = WCSService(
        "https://private.example/wcs",
        coverage_id="coverage-1",
        headers={
            "User-Agent": "tilearray-caps-test/1.0",
            "Authorization": "Bearer secret-token",
        },
        params={"username": "gis-user", "password": "gis-pass"},
        fetch_timeout=12.5,
    )
    description = service.describe_coverage()

    assert description.identifier == "coverage-1"
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers.get("user-agent") == "tilearray-caps-test/1.0"
    assert headers.get("authorization") == "Bearer secret-token"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params.get("request") == "DescribeCoverage"
    assert params.get("coverageId") == "coverage-1"
    assert params.get("username") == "gis-user"
    assert params.get("password") == "gis-pass"


@respx.mock
def test_wcs_get_capabilities_applies_default_user_agent() -> None:
    captured_ua: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured_ua.append(request.headers.get("user-agent", ""))
        return httpx.Response(
            200,
            text="""<?xml version='1.0' encoding='UTF-8'?>
            <wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0"
                              xmlns:ows="http://www.opengis.net/ows/1.1"
                              version="2.0.1">
              <wcs:Contents/>
            </wcs:Capabilities>""",
        )

    respx.get("https://example.com/wcs").mock(side_effect=handler)
    WCSService("https://example.com/wcs").get_capabilities()

    assert captured_ua
    assert captured_ua[0] == default_headers()["User-Agent"]
    assert DEFAULT_HTTP_TIMEOUT == 30.0


def test_wcs_parser_reads_axis_labels_from_describe_coverage_fixtures() -> None:
    parser = WCSParser("http://example.com/wcs")

    ea = parser.parse_describe_coverage(
        (FIXTURES / "ea_lidar_describe_coverage.xml").read_text()
    )
    assert ea.native_crs == CRS.EPSG_27700
    assert ea.axis_labels["EPSG:27700"] == ("E", "N")
    assert ea.native_resolution == (1.0, 1.0)

    usgs = parser.parse_describe_coverage(
        (FIXTURES / "usgs_3dep_describe_coverage.xml").read_text()
    )
    assert usgs.native_crs == CRS.EPSG_3857
    assert usgs.axis_labels["EPSG:3857"] == ("x", "y")
    assert usgs.native_format == Format.GEOTIFF
    assert usgs.native_resolution is not None
    assert usgs.native_resolution[0] == pytest.approx(1.0, rel=1e-6)


def test_wcs_service_builds_subset_from_coverage_metadata() -> None:
    parser = WCSParser("http://example.com/wcs")
    ea_metadata = parser.parse_describe_coverage(
        (FIXTURES / "ea_lidar_describe_coverage.xml").read_text()
    )
    service = WCSService(
        "http://example.com/wcs",
        coverage_id=ea_metadata.identifier,
        crs=CRS.EPSG_27700,
    )
    service.set_coverage_metadata(ea_metadata)

    # In-extent sample (EA envelope starts at 80000, 4000).
    geometry = TileGeometry(
        bbox=BoundingBox(
            min_x=400000, min_y=300000, max_x=400010, max_y=300010, crs=CRS.EPSG_27700
        ),
        width=16,
        height=16,
        crs=CRS.EPSG_27700,
    )
    request = service.build_tile_request(geometry)
    assert request.coverage_empty is False
    subsets = request.params["subset"]

    assert subsets[0].startswith("E(")
    assert subsets[1].startswith("N(")


def test_wcs_service_reprojects_to_native_crs_for_arcgis_metadata() -> None:
    parser = WCSParser("http://example.com/wcs")
    usgs_metadata = parser.parse_describe_coverage(
        (FIXTURES / "usgs_3dep_describe_coverage.xml").read_text()
    )
    service = WCSService(
        "http://example.com/wcs",
        coverage_id=usgs_metadata.identifier,
        crs=CRS.EPSG_4326,
    )
    service.set_coverage_metadata(usgs_metadata)

    geometry = TileGeometry(
        bbox=BoundingBox(
            min_x=-105.01,
            min_y=39.74,
            max_x=-104.99,
            max_y=39.76,
            crs=CRS.EPSG_4326,
        ),
        width=16,
        height=16,
        crs=CRS.EPSG_4326,
    )
    request = service.build_tile_request(geometry)

    # Subsetting in the coverage native CRS: omit subsettingCRS (WCS 2 default).
    assert "subsettingCRS" not in request.params
    subsets = request.params["subset"]
    assert subsets[0].startswith("x(")
    assert subsets[1].startswith("y(")


def test_normalize_crs_reference() -> None:
    assert (
        normalize_crs_reference("http://www.opengis.net/def/crs/EPSG/0/3857")
        == "EPSG:3857"
    )
    assert normalize_crs_reference("EPSG:27700") == "EPSG:27700"
    assert normalize_crs_reference("urn:ogc:def:crs:EPSG::25833") == "EPSG:25833"
    assert CRS.parse("urn:ogc:def:crs:EPSG::25833") == "EPSG:25833"
    assert CRS.parse("urn:ogc:def:crs:OGC:1.3:CRS84") == CRS.parse("OGC:CRS84")


def test_wcs_service_coerce_helpers_validate_inputs() -> None:
    service = WCSService("http://example.com/wcs", coverage_id="coverage-1")

    assert service._coerce_format(Format.PNG) == Format.PNG
    assert service._coerce_format("image/png") == Format.PNG
    assert CRS.parse(CRS.EPSG_4326) == CRS.EPSG_4326
    assert CRS.parse("EPSG:4326") == CRS.EPSG_4326
    assert CRS.parse(4326) == CRS.EPSG_4326

    with pytest.raises(ValidationError, match="Unsupported WCS format"):
        service._coerce_format("image/unknown")

    assert CRS.parse("EPSG:25833") == "EPSG:25833"
    assert CRS.parse(32632) == "EPSG:32632"

    with pytest.raises(ValidationError, match="Did you mean 'EPSG:28992'"):
        CRS.parse("EPGS:28992")

    with pytest.raises(ValidationError, match="Invalid CRS"):
        CRS.parse(object())


def test_wcs_default_subset_axes_respect_angular_vs_linear() -> None:
    service = WCSService("http://example.com/wcs", coverage_id="coverage-1")

    assert service._default_subset_axes(CRS.EPSG_4326) == ("Long", "Lat")
    assert service._default_subset_axes(CRS.EPSG_27700) == ("E", "N")
    assert service._default_subset_axes(CRS.EPSG_3857) == ("x", "y")
    assert service._default_subset_axes(CRS.parse("EPSG:25833")) == ("E", "N")
    assert service._default_subset_axes(CRS.parse("EPSG:32632")) == ("E", "N")


def test_service_config_builds_xyz_service() -> None:
    config = XYZConfig.from_url(
        "https://tiles.example/{z}/{x}/{y}.png",
        zoom=10,
        headers={"User-Agent": "tilearray"},
        params={"layer": "roads"},
        chunk_size=(256, 256),
        grid_shape=(2, 2),
        cache_dir="/tmp/cache",
        resolution=(1.0, 1.0),
    )

    service = config.build_service()
    assert isinstance(service, XYZService)
    assert service.zoom == 10
    assert config.tile_kwargs()["zoom"] == 10
    assert config.tile_kwargs()["params"] == {"layer": "roads"}
    assert config.array_defaults()["chunk_size"] == (256, 256)
