from unittest.mock import MagicMock

import pytest
import requests

from tilearray.service.base import TileGeometry
from tilearray.service.config import WCSConfig, XYZConfig
from tilearray.service.wcs import WCSParser, WCSService
from tilearray.service.xyz import XYZService
from tilearray.types import CRS, BoundingBox, Format


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


def test_wcs_service_requires_coverage_id():
    service = WCSService("http://example.com/wcs")
    geometry = TileGeometry(
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=16,
        height=16,
        crs=CRS.EPSG_4326,
    )

    with pytest.raises(ValueError):
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


def test_wcs_config_build_service_raises_for_missing_coverage(monkeypatch):
    config = WCSConfig.from_url("http://example.com/wcs", coverage_id="missing")

    def fake_describe(self, coverage_id=None, **params):
        raise requests.HTTPError("not found")

    monkeypatch.setattr(WCSService, "describe_coverage", fake_describe)

    with pytest.raises(ValueError, match="missing"):
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


def test_wcs_service_get_coverage_handles_request_errors(monkeypatch) -> None:
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )

    def fake_get(*args, **kwargs):
        raise requests.HTTPError("bad gateway", response=MagicMock(status_code=502))

    monkeypatch.setattr(service.session, "get", fake_get)

    response = service.get_coverage(
        "coverage-1",
        BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        64,
        64,
    )

    assert response.success is False
    assert response.status_code == 502


def test_wcs_service_subset_axes_for_projected_crs() -> None:
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="coverage-1",
        crs=CRS.EPSG_27700,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=10, crs=CRS.EPSG_27700)
    subsets = service._format_subset(bbox, CRS.EPSG_27700)

    assert subsets[0].startswith("E(")
    assert subsets[1].startswith("N(")


def test_wcs_service_coerce_helpers_validate_inputs() -> None:
    service = WCSService("http://example.com/wcs", coverage_id="coverage-1")

    assert service._coerce_format(Format.PNG) == Format.PNG
    assert service._coerce_format("image/png") == Format.PNG
    assert service._coerce_crs(CRS.EPSG_4326) == CRS.EPSG_4326
    assert service._coerce_crs("EPSG:4326") == CRS.EPSG_4326
    assert service._coerce_crs(4326) == CRS.EPSG_4326

    with pytest.raises(ValueError, match="Unsupported WCS format"):
        service._coerce_format("image/unknown")

    with pytest.raises(ValueError, match="Invalid CRS value"):
        service._coerce_crs(object())


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
