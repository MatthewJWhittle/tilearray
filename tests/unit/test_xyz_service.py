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
from tilearray.fetch import TileFetcher
from tilearray.service.base import TileGeometry, detect_service_type
from tilearray.service.config import XYZConfig
from tilearray.service.xyz import XYZService, _tile_bounds, _tile_range_for_bbox
from tilearray.tiles import fetch_tile
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

OSM_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


@pytest.fixture(autouse=True)
def reset_fetcher_instances():
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


@pytest.fixture
def png_tile_bytes() -> bytes:
    array = np.full((256, 256), 128, dtype=np.uint8)
    with BytesIO() as buffer:
        Image.fromarray(array, mode="L").save(buffer, format="PNG")
        return buffer.getvalue()


@pytest.fixture
def rgb_jpeg_tile_bytes() -> bytes:
    array = np.zeros((256, 256, 3), dtype=np.uint8)
    array[..., 0] = 200
    array[..., 1] = 100
    array[..., 2] = 50
    with BytesIO() as buffer:
        Image.fromarray(array, mode="RGB").save(buffer, format="JPEG")
        return buffer.getvalue()


def test_detect_service_type_for_xyz_template():
    detected = detect_service_type(OSM_TEMPLATE)
    assert detected == ServiceTypeEnum.XYZ


def test_xyz_tile_range_for_small_bbox():
    zoom = 16
    tile_x, tile_y = 32768, 21770
    outer = _tile_bounds(tile_x, tile_y, zoom, CRS.EPSG_4326)
    margin_x = (outer.max_x - outer.min_x) * 0.25
    margin_y = (outer.max_y - outer.min_y) * 0.25
    bbox = BoundingBox(
        min_x=outer.min_x + margin_x,
        min_y=outer.min_y + margin_y,
        max_x=outer.max_x - margin_x,
        max_y=outer.max_y - margin_y,
        crs=CRS.EPSG_4326,
    )

    x_min, y_min, x_max, y_max = _tile_range_for_bbox(bbox, zoom=zoom)
    assert (x_min, y_min, x_max, y_max) == (tile_x, tile_y, tile_x, tile_y)


def test_xyz_service_build_tile_request():
    service = XYZService(OSM_TEMPLATE, zoom=12, output_format=Format.PNG)
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

    assert request.url == "https://tile.openstreetmap.org/12/2048/1361.png"
    assert request.output_format == Format.PNG
    assert request.width == 256
    assert request.height == 256


def test_xyz_service_build_tile_request_includes_headers():
    service = XYZService(
        OSM_TEMPLATE,
        zoom=12,
        headers={"User-Agent": "tilearray-test/1.0"},
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

    request = service.build_tile_request(
        tile,
        headers={"X-Peer-Test": "enabled"},
    )

    assert request.headers == {
        "User-Agent": "tilearray-test/1.0",
        "X-Peer-Test": "enabled",
    }


def test_xyz_service_infers_grid_shape_for_multi_tile_bbox():
    service = XYZService(OSM_TEMPLATE, zoom=12)
    bbox = BoundingBox(
        min_x=-0.2,
        min_y=51.4,
        max_x=0.2,
        max_y=51.6,
        crs=CRS.EPSG_4326,
    )

    tiles = list(service.plan_tiles(bbox, (256, 256), zoom=12))
    assert len(tiles) > 1
    assert service.inferred_grid_shape is not None
    rows, cols = service.inferred_grid_shape
    assert rows * cols == len(tiles)


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


def _mock_fetch_tile(png_tile_bytes: bytes):
    def fetch_tile(request: TileRequest, **kwargs: object) -> TileResponse:
        return TileResponse(
            data=png_tile_bytes,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    return fetch_tile


def test_create_array_with_xyz_service(
    png_tile_bytes: bytes,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
):
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    zoom = 16
    tile_x, tile_y = 32768, 21770
    bbox = _single_tile_bbox(zoom, tile_x, tile_y)

    config = XYZConfig.from_url(
        OSM_TEMPLATE,
        zoom=zoom,
        output_format=Format.PNG,
        chunk_size=(256, 256),
        cache_dir=tmp_path,
    )

    result = create_array(
        config,
        bbox,
        CRS.EPSG_4326,
        compute=True,
    )

    assert isinstance(result, xr.DataArray)
    assert result.shape == (256, 256)
    assert result.attrs["service_type"] == ServiceTypeEnum.XYZ.value
    assert float(result.mean()) == pytest.approx(128.0, rel=1e-3)


def test_create_array_with_xyz_url_and_service_type(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
):
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    zoom = 16
    tile_x, tile_y = 32768, 21770
    bbox = _single_tile_bbox(zoom, tile_x, tile_y)

    result = create_array(
        OSM_TEMPLATE,
        (bbox.min_x, bbox.min_y, bbox.max_x, bbox.max_y),
        CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        zoom=zoom,
        output_format=Format.PNG,
        chunk_size=(256, 256),
        compute=True,
    )

    assert result.shape == (256, 256)
    assert result.attrs["service_type"] == "XYZ"


def test_create_array_with_rgb_jpeg_tile(
    rgb_jpeg_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
):
    monkeypatch.setattr(
        "tilearray.array.fetch_tile", _mock_fetch_tile(rgb_jpeg_tile_bytes)
    )

    zoom = 16
    tile_x, tile_y = 32768, 21770
    bbox = _single_tile_bbox(zoom, tile_x, tile_y)

    result = create_array(
        XYZConfig.from_url(
            OSM_TEMPLATE,
            zoom=zoom,
            output_format=Format.JPEG,
            chunk_size=(256, 256),
        ),
        bbox,
        CRS.EPSG_4326,
        compute=True,
    )

    assert result.shape == (256, 256, 3)
    assert result.dims == ("y", "x", "band")
    assert float(result[..., 0].mean()) == pytest.approx(200.0, rel=1e-3)
    assert float(result[..., 1].mean()) == pytest.approx(100.0, rel=1e-3)
    assert float(result[..., 2].mean()) == pytest.approx(50.0, rel=1e-3)


@respx.mock
def test_xyz_config_headers_reach_http_fetch(rgb_jpeg_tile_bytes: bytes) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, content=rgb_jpeg_tile_bytes)

    respx.get(url__regex=r"https://tile\.openstreetmap\.org/.*").mock(
        side_effect=handler
    )

    zoom = 16
    tile_x, tile_y = 32768, 21770
    service = XYZService(
        OSM_TEMPLATE,
        zoom=zoom,
        output_format=Format.JPEG,
        headers={"User-Agent": "tilearray-peer-test/1.0"},
    )
    tile = TileGeometry(
        bbox=_tile_bounds(tile_x, tile_y, zoom, CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=tile_x,
        tile_y=tile_y,
        zoom=zoom,
    )
    request = service.build_tile_request(tile)

    response = fetch_tile(request)

    assert response.success is True
    assert captured.get("user-agent") == "tilearray-peer-test/1.0"
