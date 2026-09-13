"""Unit tests for WMTS service adapter."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from PIL import Image
from pytest import MonkeyPatch

from tilearray.array import create_array
from tilearray.fetch import TileFetcher
from tilearray.service.base import TileGeometry
from tilearray.service.config import WMTSConfig
from tilearray.service.wmts import WMTSParser, WMTSService
from tilearray.service.xyz import _tile_bounds
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = pytest.mark.unit

WMTS_URL = "https://example.com/wmts"
REST_TEMPLATE = (
    "https://example.com/wmts/rest/{Layer}/{Style}/{TileMatrixSet}/"
    "{TileMatrix}/{TileRow}/{TileCol}.png"
)


@pytest.fixture(autouse=True)
def reset_fetcher_instances():
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


@pytest.fixture
def png_tile_bytes() -> bytes:
    array = np.full((256, 256), 96, dtype=np.uint8)
    with BytesIO() as buffer:
        Image.fromarray(array, mode="L").save(buffer, format="PNG")
        return buffer.getvalue()


@pytest.fixture
def rgb_jpeg_tile_bytes() -> bytes:
    array = np.zeros((256, 256, 3), dtype=np.uint8)
    array[..., 0] = 180
    array[..., 1] = 90
    array[..., 2] = 45
    with BytesIO() as buffer:
        Image.fromarray(array, mode="RGB").save(buffer, format="JPEG")
        return buffer.getvalue()


WMTS_CAPABILITIES_XML = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>example_layer</ows:Identifier>
      <ResourceURL format="image/png" resourceType="tile"
                   template="https://example.com/wmts/rest/example_layer/default/EPSG4326/{TileMatrix}/{TileRow}/{TileCol}.png"/>
      <TileMatrixSetLink><TileMatrixSet>EPSG4326</TileMatrixSet></TileMatrixSetLink>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>EPSG4326</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:OGC:1.3:CRS84</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>5</ows:Identifier>
        <ScaleDenominator>8735660.375448</ScaleDenominator>
        <TopLeftCorner>-180 90</TopLeftCorner>
        <TileWidth>256</TileWidth>
        <TileHeight>256</TileHeight>
        <MatrixWidth>32</MatrixWidth>
        <MatrixHeight>16</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>"""


def test_wmts_parser_reads_capabilities() -> None:
    parser = WMTSParser()
    caps = parser.parse_get_capabilities(WMTS_CAPABILITIES_XML)

    assert "example_layer" in caps.layers
    assert caps.layers["example_layer"].resource_url_template is not None
    assert "EPSG4326" in caps.tile_matrix_sets
    assert "5" in caps.tile_matrix_sets["EPSG4326"].matrices


def test_wmts_service_build_kvp_tile_request() -> None:
    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=12,
        output_format=Format.PNG,
    )
    tile = TileGeometry(
        bbox=_tile_bounds(2048, 1361, 12, CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=2048,
        tile_y=1361,
        zoom=12,
    )

    request = service.build_tile_request(tile)

    assert request.url == WMTS_URL
    assert request.params["service"] == "WMTS"
    assert request.params["request"] == "GetTile"
    assert request.params["layer"] == "example_layer"
    assert request.params["TileMatrixSet"] == "EPSG4326"
    assert request.params["TileMatrix"] == "12"
    assert request.params["TileRow"] == "1361"
    assert request.params["TileCol"] == "2048"
    assert request.params["format"] == "image/png"


def test_wmts_service_build_rest_tile_request() -> None:
    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=12,
        url_template=REST_TEMPLATE,
        output_format=Format.PNG,
    )
    tile = TileGeometry(
        bbox=_tile_bounds(2048, 1361, 12, CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=2048,
        tile_y=1361,
        zoom=12,
    )

    request = service.build_tile_request(tile)

    assert request.url == (
        "https://example.com/wmts/rest/example_layer//EPSG4326/12/1361/2048.png"
    )
    assert request.params == {}


def test_wmts_service_build_rest_tile_request_includes_headers() -> None:
    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=12,
        url_template=REST_TEMPLATE,
        headers={"User-Agent": "tilearray-test/1.0"},
    )
    tile = TileGeometry(
        bbox=_tile_bounds(1, 1, 12, CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=1,
        tile_y=1,
        zoom=12,
    )

    request = service.build_tile_request(
        tile,
        headers={"X-Peer-Test": "enabled"},
    )

    assert request.headers == {
        "User-Agent": "tilearray-test/1.0",
        "X-Peer-Test": "enabled",
    }


def test_wmts_service_plan_tiles_for_bbox() -> None:
    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=12,
    )
    bbox = BoundingBox(
        min_x=-0.2,
        min_y=51.4,
        max_x=0.2,
        max_y=51.6,
        crs=CRS.EPSG_4326,
    )

    tiles = list(service.plan_tiles(bbox, (256, 256), tile_matrix=12))
    assert len(tiles) > 1
    assert service.inferred_grid_shape is not None
    rows, cols = service.inferred_grid_shape
    assert rows * cols == len(tiles)


def _mock_fetch_tile(tile_bytes: bytes):
    def fetch_tile_impl(request: TileRequest, **kwargs: object) -> TileResponse:
        return TileResponse(
            data=tile_bytes,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    return fetch_tile_impl


def test_create_array_with_wmts_kvp_service(
    png_tile_bytes: bytes,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    zoom = 16
    tile_x, tile_y = 32768, 21770
    bbox = _single_tile_bbox(zoom, tile_x, tile_y)

    config = WMTSConfig.from_url(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=zoom,
        output_format=Format.PNG,
        chunk_size=(256, 256),
        cache_dir=tmp_path,
    )

    result = create_array(config, bbox, CRS.EPSG_4326, compute=True)

    assert isinstance(result, xr.DataArray)
    assert result.shape == (256, 256)
    assert result.attrs["service_type"] == ServiceTypeEnum.WMTS.value
    assert float(result.mean()) == pytest.approx(96.0, rel=1e-3)


def test_create_array_with_wmts_rest_jpeg(
    rgb_jpeg_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "tilearray.array.fetch_tile", _mock_fetch_tile(rgb_jpeg_tile_bytes)
    )

    zoom = 16
    tile_x, tile_y = 32768, 21770
    bbox = _single_tile_bbox(zoom, tile_x, tile_y)

    config = WMTSConfig.from_url(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=zoom,
        url_template=REST_TEMPLATE,
        output_format=Format.JPEG,
        chunk_size=(256, 256),
    )

    result = create_array(config, bbox, CRS.EPSG_4326, compute=True)

    assert result.shape == (256, 256, 3)
    assert result.dims == ("y", "x", "band")
    assert float(result[..., 0].mean()) == pytest.approx(180.0, abs=5.0)


def _single_tile_bbox(zoom: int, tile_x: int, tile_y: int) -> BoundingBox:
    outer = _tile_bounds(tile_x, tile_y, zoom, CRS.EPSG_4326)
    margin_x = (outer.max_x - outer.min_x) * 0.25
    margin_y = (outer.max_y - outer.min_y) * 0.25
    return BoundingBox(
        min_x=outer.min_x + margin_x,
        min_y=outer.min_y + margin_y,
        max_x=outer.max_x - margin_x,
        max_y=outer.max_y - margin_y,
        crs=CRS.EPSG_4326,
    )
