"""Unit tests for WMTS service adapter."""

from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx
import xarray as xr
from PIL import Image
from pytest import MonkeyPatch

from tilearray.array import create_array
from tilearray.errors import NetworkError, ValidationError
from tilearray.fetch import TileFetcher
from tilearray.service.base import TileGeometry
from tilearray.service.config import WMTSConfig
from tilearray.service.wmts import WMTSParser, WMTSService, WMTSTileMatrix
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


@respx.mock
def test_wmts_get_capabilities_sends_config_headers_and_params() -> None:
    """Private-server auth on config must reach WMTS GetCapabilities (#57)."""

    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["params"] = dict(request.url.params)
        return httpx.Response(200, text=WMTS_CAPABILITIES_XML)

    respx.get(WMTS_URL).mock(side_effect=handler)

    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=12,
        headers={
            "User-Agent": "tilearray-wmts-caps/1.0",
            "X-API-Key": "private-key",
        },
        params={"token": "query-token"},
    )
    caps = service.get_capabilities()

    assert "example_layer" in caps.layers
    headers = captured["headers"]
    assert isinstance(headers, dict)
    assert headers.get("user-agent") == "tilearray-wmts-caps/1.0"
    assert headers.get("x-api-key") == "private-key"
    params = captured["params"]
    assert isinstance(params, dict)
    assert params.get("request") == "GetCapabilities"
    assert params.get("token") == "query-token"


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

    plan = service.generate_tile_requests(bbox, (256, 256), tile_matrix=12)
    assert len(plan.tiles) > 1
    assert plan.grid_shape is not None
    rows, cols = plan.grid_shape
    assert rows * cols == len(plan.tiles)


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


# ---------------------------------------------------------------------------
# Native TileMatrix planning (#93)
# Independent expectations use the OGC ScaleDenominator formula:
#   pixel_size = ScaleDenominator * 0.00028 / metres_per_unit(CRS)
# with metres_per_degree = semi_major * π / 180 for geographic CRS.
# ---------------------------------------------------------------------------

_WGS84_SEMI_MAJOR_M = 6378137.0
_METRES_PER_DEGREE = _WGS84_SEMI_MAJOR_M * math.pi / 180.0
_OGC_PIXEL_M = 0.00028

# Fixture ScaleDenominator for CRS84 level "5" (see WMTS_CAPABILITIES_XML).
_CRS84_SCALE = 8735660.375448
# Independently: 8735660.375448 * 0.00028 / (6378137 * π/180) ≈ 0.02197265625°
_CRS84_PIXEL = _CRS84_SCALE * _OGC_PIXEL_M / _METRES_PER_DEGREE
_CRS84_TILE_SPAN = 256 * _CRS84_PIXEL  # ≈ 5.625°

# Synthetic EPSG:25833 matrix: exactly 100 m / pixel.
_UTM33_PIXEL_M = 100.0
_UTM33_SCALE = _UTM33_PIXEL_M / _OGC_PIXEL_M
_UTM33_TILE_SPAN = 256 * _UTM33_PIXEL_M  # 25600 m
_UTM33_TOP_LEFT_X = 250_000.0
_UTM33_TOP_LEFT_Y = 6_700_000.0

WMTS_EPSG25833_CAPABILITIES_XML = f"""<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>npi_layer</ows:Identifier>
      <TileMatrixSetLink>
        <TileMatrixSet>EPSG:25833</TileMatrixSet>
        <TileMatrixSetLimits>
          <TileMatrixLimits>
            <TileMatrix>utm33_2</TileMatrix>
            <MinTileRow>0</MinTileRow>
            <MaxTileRow>20</MaxTileRow>
            <MinTileCol>0</MinTileCol>
            <MaxTileCol>20</MaxTileCol>
          </TileMatrixLimits>
        </TileMatrixSetLimits>
      </TileMatrixSetLink>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>EPSG:25833</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:EPSG::25833</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>utm33_2</ows:Identifier>
        <ScaleDenominator>{_UTM33_SCALE}</ScaleDenominator>
        <TopLeftCorner>{_UTM33_TOP_LEFT_X} {_UTM33_TOP_LEFT_Y}</TopLeftCorner>
        <TileWidth>256</TileWidth>
        <TileHeight>256</TileHeight>
        <MatrixWidth>64</MatrixWidth>
        <MatrixHeight>64</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>"""

WMTS_EPSG4326_AXIS_ORDER_XML = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>axis_layer</ows:Identifier>
      <TileMatrixSetLink><TileMatrixSet>EPSG:4326</TileMatrixSet></TileMatrixSetLink>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>EPSG:4326</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:EPSG::4326</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>0</ows:Identifier>
        <ScaleDenominator>279541132.0143589</ScaleDenominator>
        <TopLeftCorner>90 -180</TopLeftCorner>
        <TileWidth>256</TileWidth>
        <TileHeight>256</TileHeight>
        <MatrixWidth>2</MatrixWidth>
        <MatrixHeight>1</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>"""


def test_wmts_parser_reads_crs84_supported_crs() -> None:
    caps = WMTSParser().parse_get_capabilities(WMTS_CAPABILITIES_XML)
    tms = caps.tile_matrix_sets["EPSG4326"]
    assert tms.supported_crs == CRS.parse("OGC:CRS84")
    matrix = tms.matrices["5"]
    assert matrix.top_left_x == pytest.approx(-180.0)
    assert matrix.top_left_y == pytest.approx(90.0)


def test_wmts_parser_swaps_epsg4326_top_left_axis_order() -> None:
    caps = WMTSParser().parse_get_capabilities(WMTS_EPSG4326_AXIS_ORDER_XML)
    matrix = caps.tile_matrix_sets["EPSG:4326"].matrices["0"]
    # Capabilities TopLeftCorner is lat/lon (90, -180); stored always_xy.
    assert matrix.top_left_x == pytest.approx(-180.0)
    assert matrix.top_left_y == pytest.approx(90.0)


def test_wmts_plan_crs84_matrix_tile_bounds() -> None:
    """CRS84 matrix uses degree units — not ScaleDenominator*0.00028 raw."""

    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=5,
    )
    service.apply_capabilities(
        WMTSParser().parse_get_capabilities(WMTS_CAPABILITIES_XML)
    )

    # Interior of tile (col=0, row=0); independent span ≈ 5.625°.
    bbox = BoundingBox(
        min_x=-179.0,
        min_y=85.0,
        max_x=-175.0,
        max_y=89.0,
        crs=CRS.EPSG_4326,
    )
    plan = service.generate_tile_requests(bbox, (256, 256))
    tiles = list(service.plan_tiles(bbox, (256, 256)))

    assert len(tiles) == 1
    tile = tiles[0]
    assert tile.tile_x == 0
    assert tile.tile_y == 0
    assert tile.crs == CRS.parse("OGC:CRS84")
    assert tile.bbox.min_x == pytest.approx(-180.0)
    assert tile.bbox.max_y == pytest.approx(90.0)
    assert tile.bbox.max_x == pytest.approx(-180.0 + _CRS84_TILE_SPAN)
    assert tile.bbox.min_y == pytest.approx(90.0 - _CRS84_TILE_SPAN)
    assert plan.mosaic_bbox is not None
    assert plan.mosaic_bbox.crs == CRS.parse("OGC:CRS84")


def test_wmts_plan_epsg25833_matrix_tile_bounds() -> None:
    service = WMTSService(
        WMTS_URL,
        layer="npi_layer",
        tile_matrix_set="EPSG:25833",
        tile_matrix="utm33_2",
        crs=CRS.parse("EPSG:25833"),
    )
    service.apply_capabilities(
        WMTSParser().parse_get_capabilities(WMTS_EPSG25833_CAPABILITIES_XML)
    )

    # Independently: tile col=2,row=1 covers
    # x [301200, 326800], y [6648800, 6674400]
    expected_min_x = _UTM33_TOP_LEFT_X + 2 * _UTM33_TILE_SPAN
    expected_max_x = _UTM33_TOP_LEFT_X + 3 * _UTM33_TILE_SPAN
    expected_max_y = _UTM33_TOP_LEFT_Y - 1 * _UTM33_TILE_SPAN
    expected_min_y = _UTM33_TOP_LEFT_Y - 2 * _UTM33_TILE_SPAN

    bbox = BoundingBox(
        min_x=expected_min_x + 100.0,
        min_y=expected_min_y + 100.0,
        max_x=expected_max_x - 100.0,
        max_y=expected_max_y - 100.0,
        crs=CRS.parse("EPSG:25833"),
    )
    tiles = list(service.plan_tiles(bbox, (256, 256)))

    assert len(tiles) == 1
    tile = tiles[0]
    assert tile.tile_x == 2
    assert tile.tile_y == 1
    assert tile.tile_matrix_id == "utm33_2"
    assert tile.crs == CRS.parse("EPSG:25833")
    assert tile.bbox.min_x == pytest.approx(expected_min_x)
    assert tile.bbox.max_x == pytest.approx(expected_max_x)
    assert tile.bbox.min_y == pytest.approx(expected_min_y)
    assert tile.bbox.max_y == pytest.approx(expected_max_y)


def test_wmts_plan_transforms_query_bbox_crs_to_matrix_crs() -> None:
    """Caller bbox in EPSG:4326 selects EPSG:25833 tiles; output stays native."""

    service = WMTSService(
        WMTS_URL,
        layer="npi_layer",
        tile_matrix_set="EPSG:25833",
        tile_matrix="utm33_2",
    )
    service.apply_capabilities(
        WMTSParser().parse_get_capabilities(WMTS_EPSG25833_CAPABILITIES_XML)
    )

    # Approx lon/lat near easting 301200+ / northing 6674400- (UTM 33N).
    bbox = BoundingBox(
        min_x=10.55,
        min_y=60.05,
        max_x=10.75,
        max_y=60.20,
        crs=CRS.EPSG_4326,
    )
    plan = service.generate_tile_requests(bbox, (256, 256))
    tiles = list(service.plan_tiles(bbox, (256, 256)))

    assert tiles
    assert all(tile.crs == CRS.parse("EPSG:25833") for tile in tiles)
    assert plan.mosaic_bbox is not None
    assert plan.mosaic_bbox.crs == CRS.parse("EPSG:25833")


def test_wmts_unknown_matrix_raises_without_web_mercator_fallback() -> None:
    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="EPSG4326",
        tile_matrix=99,
    )
    service.apply_capabilities(
        WMTSParser().parse_get_capabilities(WMTS_CAPABILITIES_XML)
    )

    bbox = BoundingBox(min_x=-1.0, min_y=50.0, max_x=1.0, max_y=52.0, crs=CRS.EPSG_4326)
    with pytest.raises(ValidationError, match="TileMatrix '99' was not found"):
        list(service.plan_tiles(bbox, (256, 256)))


@respx.mock
def test_wmts_projected_tms_autoload_capabilities_network_error() -> None:
    """Non-slippy TMS auto-loads GetCapabilities; HTTP failure is NetworkError."""

    respx.get(WMTS_URL).mock(side_effect=httpx.ConnectError("refused"))
    service = WMTSService(
        WMTS_URL,
        layer="npi_layer",
        tile_matrix_set="EPSG:25833",
        tile_matrix="utm33_2",
        crs=CRS.parse("EPSG:25833"),
    )
    bbox = BoundingBox(
        min_x=300_000.0,
        min_y=6_650_000.0,
        max_x=310_000.0,
        max_y=6_660_000.0,
        crs=CRS.parse("EPSG:25833"),
    )
    with pytest.raises(NetworkError, match="GetCapabilities"):
        list(service.plan_tiles(bbox, (256, 256)))


@respx.mock
def test_wmts_projected_tms_autoloads_get_capabilities() -> None:
    """create_array-style planning loads caps for EPSG:25833 without a prior apply."""

    respx.get(WMTS_URL).mock(
        return_value=httpx.Response(200, text=WMTS_EPSG25833_CAPABILITIES_XML)
    )
    service = WMTSService(
        WMTS_URL,
        layer="npi_layer",
        tile_matrix_set="EPSG:25833",
        tile_matrix="utm33_2",
        crs=CRS.parse("EPSG:25833"),
    )
    expected_min_x = _UTM33_TOP_LEFT_X + 2 * _UTM33_TILE_SPAN
    expected_max_y = _UTM33_TOP_LEFT_Y - 1 * _UTM33_TILE_SPAN
    bbox = BoundingBox(
        min_x=expected_min_x + 10.0,
        min_y=expected_max_y - _UTM33_TILE_SPAN + 10.0,
        max_x=expected_min_x + 100.0,
        max_y=expected_max_y - 10.0,
        crs=CRS.parse("EPSG:25833"),
    )
    tiles = list(service.plan_tiles(bbox, (256, 256)))
    assert len(tiles) == 1
    assert tiles[0].tile_x == 2
    assert tiles[0].tile_y == 1
    assert tiles[0].crs == CRS.parse("EPSG:25833")


def test_wmts_lookup_matches_zero_padded_matrix_ids() -> None:
    """Kartverket-style TileMatrix ids ('08') match caller tile_matrix=8."""

    xml = f"""<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0"
              xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>topo</ows:Identifier>
      <TileMatrixSetLink><TileMatrixSet>utm33n</TileMatrixSet></TileMatrixSetLink>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>utm33n</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:EPSG::25833</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>08</ows:Identifier>
        <ScaleDenominator>{_UTM33_SCALE}</ScaleDenominator>
        <TopLeftCorner>{_UTM33_TOP_LEFT_X} {_UTM33_TOP_LEFT_Y}</TopLeftCorner>
        <TileWidth>256</TileWidth>
        <TileHeight>256</TileHeight>
        <MatrixWidth>64</MatrixWidth>
        <MatrixHeight>64</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>"""
    service = WMTSService(
        WMTS_URL,
        layer="topo",
        tile_matrix_set="utm33n",
        tile_matrix=8,
        crs=CRS.parse("EPSG:25833"),
    )
    service.apply_capabilities(WMTSParser().parse_get_capabilities(xml))
    expected_min_x = _UTM33_TOP_LEFT_X + 2 * _UTM33_TILE_SPAN
    expected_max_y = _UTM33_TOP_LEFT_Y - 1 * _UTM33_TILE_SPAN
    bbox = BoundingBox(
        min_x=expected_min_x + 10.0,
        min_y=expected_max_y - _UTM33_TILE_SPAN + 10.0,
        max_x=expected_min_x + 100.0,
        max_y=expected_max_y - 10.0,
        crs=CRS.parse("EPSG:25833"),
    )
    tiles = list(service.plan_tiles(bbox, (256, 256)))
    assert len(tiles) == 1
    assert tiles[0].tile_matrix_id == "08"
    request = service.build_tile_request(tiles[0], tile_matrix=8)
    assert request.params["TileMatrix"] == "08"


def test_wmts_matrix_override_plans_without_capabilities() -> None:
    matrix = WMTSTileMatrix(
        identifier="utm33_2",
        scale_denominator=_UTM33_SCALE,
        top_left_x=_UTM33_TOP_LEFT_X,
        top_left_y=_UTM33_TOP_LEFT_Y,
        matrix_width=64,
        matrix_height=64,
        tile_width=256,
        tile_height=256,
    )
    service = WMTSService(
        WMTS_URL,
        layer="npi_layer",
        tile_matrix_set="EPSG:25833",
        tile_matrix="utm33_2",
        crs=CRS.parse("EPSG:25833"),
    )
    expected_min_x = _UTM33_TOP_LEFT_X + 2 * _UTM33_TILE_SPAN
    expected_max_y = _UTM33_TOP_LEFT_Y - 1 * _UTM33_TILE_SPAN
    bbox = BoundingBox(
        min_x=expected_min_x + 10.0,
        min_y=expected_max_y - _UTM33_TILE_SPAN + 10.0,
        max_x=expected_min_x + 100.0,
        max_y=expected_max_y - 10.0,
        crs=CRS.parse("EPSG:25833"),
    )
    tiles = list(
        service.plan_tiles(
            bbox,
            (256, 256),
            matrix=matrix,
            matrix_crs=CRS.parse("EPSG:25833"),
        )
    )
    assert len(tiles) == 1
    assert tiles[0].tile_x == 2
    assert tiles[0].tile_y == 1


def test_create_array_wmts_native_matrix_labels_output_crs(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    matrix = WMTSTileMatrix(
        identifier="utm33_2",
        scale_denominator=_UTM33_SCALE,
        top_left_x=_UTM33_TOP_LEFT_X,
        top_left_y=_UTM33_TOP_LEFT_Y,
        matrix_width=64,
        matrix_height=64,
        tile_width=256,
        tile_height=256,
    )
    expected_min_x = _UTM33_TOP_LEFT_X + 2 * _UTM33_TILE_SPAN
    expected_max_x = _UTM33_TOP_LEFT_X + 3 * _UTM33_TILE_SPAN
    expected_max_y = _UTM33_TOP_LEFT_Y - 1 * _UTM33_TILE_SPAN
    expected_min_y = _UTM33_TOP_LEFT_Y - 2 * _UTM33_TILE_SPAN

    bbox = BoundingBox(
        min_x=expected_min_x + 50.0,
        min_y=expected_min_y + 50.0,
        max_x=expected_max_x - 50.0,
        max_y=expected_max_y - 50.0,
        crs=CRS.parse("EPSG:25833"),
    )
    config = WMTSConfig.from_url(
        WMTS_URL,
        layer="npi_layer",
        tile_matrix_set="EPSG:25833",
        tile_matrix="utm33_2",
        output_format=Format.PNG,
        chunk_size=(256, 256),
        crs=CRS.parse("EPSG:25833"),
    )

    result = create_array(
        config,
        bbox,
        CRS.parse("EPSG:25833"),
        compute=True,
        matrix=matrix,
        matrix_crs=CRS.parse("EPSG:25833"),
        show_progress=False,
    )

    assert result.attrs["crs"] == "EPSG:25833"
    assert result.shape == (256, 256)
    # Pixel centres from native mosaic extent (tile-aligned), not the query inset.
    assert float(result.x.min()) == pytest.approx(expected_min_x + _UTM33_PIXEL_M / 2.0)
    assert float(result.x.max()) == pytest.approx(expected_max_x - _UTM33_PIXEL_M / 2.0)
    assert float(result.y.max()) == pytest.approx(expected_max_y - _UTM33_PIXEL_M / 2.0)
    assert float(result.y.min()) == pytest.approx(expected_min_y + _UTM33_PIXEL_M / 2.0)
    assert "bbox_requested" in result.attrs


def test_wmts_slippy_fallback_labels_native_3857(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
) -> None:
    """No-caps Web Mercator WMTS matches XYZ: tile-union EPSG:3857 georef."""

    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    zoom = 16
    tile_x, tile_y = 32768, 21770
    bbox = _single_tile_bbox(zoom, tile_x, tile_y)
    # Independent metre footprint (same formula as XYZ tests; not via planner).
    origin = math.pi * 6378137.0
    span = 2.0 * origin / (2**zoom)
    exp_min_x = -origin + tile_x * span
    exp_max_x = exp_min_x + span
    exp_max_y = origin - tile_y * span
    exp_min_y = exp_max_y - span

    service = WMTSService(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=zoom,
        output_format=Format.PNG,
    )
    plan = service.generate_tile_requests(bbox, (256, 256), crs=CRS.EPSG_4326)
    tiles = list(service.plan_tiles(bbox, (256, 256), crs=CRS.EPSG_4326))
    assert len(tiles) == 1
    assert plan.mosaic_bbox is not None
    assert plan.mosaic_bbox.crs == CRS.EPSG_3857
    assert tiles[0].crs == CRS.EPSG_3857

    config = WMTSConfig.from_url(
        WMTS_URL,
        layer="example_layer",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=zoom,
        output_format=Format.PNG,
        chunk_size=(256, 256),
    )
    result = create_array(
        config, bbox, CRS.EPSG_4326, compute=True, show_progress=False
    )

    assert result.attrs["crs"] == "EPSG:3857"
    assert result.attrs["bbox_requested"]["crs"] == "EPSG:4326"
    a, _b, c, _d, e, f = result.attrs["transform"]
    assert c == pytest.approx(exp_min_x)
    assert f == pytest.approx(exp_max_y)
    assert a == pytest.approx((exp_max_x - exp_min_x) / 256)
    assert e == pytest.approx((exp_min_y - exp_max_y) / 256)
