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
from tilearray.fetch import TileFetcher
from tilearray.service.base import TileGeometry
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

# Independent Web Mercator maths (do not import production 3857 helpers for
# expected values — avoids circular asserts against the same slippy bug).
_INDEPENDENT_ORIGIN = math.pi * 6378137.0


def _independent_tile_span_m(zoom: int) -> float:
    return 2.0 * _INDEPENDENT_ORIGIN / (2**zoom)


def _independent_tile_bounds_3857(
    tile_x: int, tile_y: int, zoom: int
) -> tuple[float, float, float, float]:
    span = _independent_tile_span_m(zoom)
    min_x = -_INDEPENDENT_ORIGIN + tile_x * span
    max_x = min_x + span
    max_y = _INDEPENDENT_ORIGIN - tile_y * span
    min_y = max_y - span
    return min_x, min_y, max_x, max_y


def _independent_union_3857(
    x_min: int, y_min: int, x_max: int, y_max: int, zoom: int
) -> tuple[float, float, float, float]:
    span = _independent_tile_span_m(zoom)
    return (
        -_INDEPENDENT_ORIGIN + x_min * span,
        _INDEPENDENT_ORIGIN - (y_max + 1) * span,
        -_INDEPENDENT_ORIGIN + (x_max + 1) * span,
        _INDEPENDENT_ORIGIN - y_min * span,
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


def test_xyz_plan_sets_inferred_mosaic_bbox_in_epsg_3857() -> None:
    """Planner exposes tile-union extent in native Web Mercator (like WMTS)."""

    service = XYZService(OSM_TEMPLATE, zoom=10)
    # High-latitude inset so a lon/lat envelope affine would diverge hard.
    bbox = BoundingBox(
        min_x=10.0,
        min_y=69.5,
        max_x=10.4,
        max_y=69.8,
        crs=CRS.EPSG_4326,
    )
    tiles = list(service.plan_tiles(bbox, (256, 256), zoom=10))
    assert tiles
    assert service.inferred_mosaic_bbox is not None
    assert service.inferred_mosaic_bbox.crs == CRS.EPSG_3857
    assert all(tile.crs == CRS.EPSG_3857 for tile in tiles)

    x_min = min(t.tile_x for t in tiles if t.tile_x is not None)
    x_max = max(t.tile_x for t in tiles if t.tile_x is not None)
    y_min = min(t.tile_y for t in tiles if t.tile_y is not None)
    y_max = max(t.tile_y for t in tiles if t.tile_y is not None)
    exp_min_x, exp_min_y, exp_max_x, exp_max_y = _independent_union_3857(
        x_min, y_min, x_max, y_max, 10
    )
    mosaic = service.inferred_mosaic_bbox
    assert mosaic.min_x == pytest.approx(exp_min_x)
    assert mosaic.min_y == pytest.approx(exp_min_y)
    assert mosaic.max_x == pytest.approx(exp_max_x)
    assert mosaic.max_y == pytest.approx(exp_max_y)


@pytest.mark.parametrize(
    ("zoom", "tile_x", "tile_y"),
    [
        (8, 128, 128),  # equator
        (12, 2048, 1361),  # London-ish
        (6, 34, 15),  # high latitude (~70°N)
        (10, 163, 395),  # San Francisco-ish
    ],
)
def test_xyz_single_tile_mosaic_attrs_are_native_3857(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
    zoom: int,
    tile_x: int,
    tile_y: int,
) -> None:
    """Query lon/lat selects tiles; mosaic CRS/transform/coords stay EPSG:3857."""

    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))
    bbox = _single_tile_bbox(zoom, tile_x, tile_y)
    assert _tile_range_for_bbox(bbox, zoom) == (tile_x, tile_y, tile_x, tile_y)

    result = create_array(
        XYZConfig.from_url(
            OSM_TEMPLATE,
            zoom=zoom,
            output_format=Format.PNG,
            chunk_size=(256, 256),
        ),
        bbox,
        CRS.EPSG_4326,
        compute=True,
        show_progress=False,
    )

    assert result.shape == (256, 256)
    assert result.attrs["crs"] == CRS.EPSG_3857.value
    assert result.attrs["bbox_requested"]["crs"] == CRS.EPSG_4326.value
    assert result.attrs["bbox_requested"]["min_x"] == pytest.approx(bbox.min_x)
    assert result.attrs["transform_source"] == "mosaic_bbox"

    exp_min_x, exp_min_y, exp_max_x, exp_max_y = _independent_tile_bounds_3857(
        tile_x, tile_y, zoom
    )
    a, _b, c, _d, e, f = result.attrs["transform"]
    assert a == pytest.approx((exp_max_x - exp_min_x) / 256)
    assert e == pytest.approx((exp_min_y - exp_max_y) / 256)  # north-up, e < 0
    assert c == pytest.approx(exp_min_x)
    assert f == pytest.approx(exp_max_y)
    assert result.coords["x"].values[0] == pytest.approx(c + a * 0.5)
    assert result.coords["y"].values[0] == pytest.approx(f + e * 0.5)


def test_xyz_london_z16_affine_matches_tile_union_3857(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
) -> None:
    """Docs London window: mosaic georef = tile-union metres, not query degrees."""

    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    zoom = 16
    bbox = BoundingBox(
        min_x=-0.005,
        min_y=51.495,
        max_x=0.005,
        max_y=51.505,
        crs=CRS.EPSG_4326,
    )
    x_min, y_min, x_max, y_max = _tile_range_for_bbox(bbox, zoom)
    rows = y_max - y_min + 1
    cols = x_max - x_min + 1
    assert (rows, cols) == (4, 2)
    height, width = rows * 256, cols * 256
    assert (height, width) == (1024, 512)

    result = create_array(
        XYZConfig.from_url(
            OSM_TEMPLATE,
            zoom=zoom,
            output_format=Format.PNG,
            chunk_size=(256, 256),
        ),
        bbox,
        CRS.EPSG_4326,
        compute=True,
        show_progress=False,
    )

    assert result.shape == (height, width)
    assert result.attrs["crs"] == "EPSG:3857"
    exp_min_x, exp_min_y, exp_max_x, exp_max_y = _independent_union_3857(
        x_min, y_min, x_max, y_max, zoom
    )
    a, _b, c, _d, e, f = result.attrs["transform"]
    assert a == pytest.approx((exp_max_x - exp_min_x) / width)
    assert e == pytest.approx((exp_min_y - exp_max_y) / height)
    assert c == pytest.approx(exp_min_x)
    assert f == pytest.approx(exp_max_y)
    # Pixel scale is metre-linear (≈2.39 m/px at z16), not degrees.
    span = _independent_tile_span_m(zoom)
    assert a == pytest.approx(span / 256)
    assert abs(e) == pytest.approx(span / 256)


def test_xyz_query_crs_3857_still_labels_native_grid(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
) -> None:
    """Passing crs=EPSG:3857 still only interprets the bbox; output stays 3857."""

    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    zoom = 8
    tile_x, tile_y = 128, 128
    min_x, min_y, max_x, max_y = _independent_tile_bounds_3857(tile_x, tile_y, zoom)
    # Inset in metres inside the tile.
    margin = (max_x - min_x) * 0.25
    bbox = BoundingBox(
        min_x=min_x + margin,
        min_y=min_y + margin,
        max_x=max_x - margin,
        max_y=max_y - margin,
        crs=CRS.EPSG_3857,
    )

    result = create_array(
        XYZConfig.from_url(
            OSM_TEMPLATE,
            zoom=zoom,
            output_format=Format.PNG,
            chunk_size=(256, 256),
        ),
        bbox,
        CRS.EPSG_3857,
        compute=True,
        show_progress=False,
    )

    assert result.attrs["crs"] == "EPSG:3857"
    assert result.shape == (256, 256)
    a, _b, c, _d, e, f = result.attrs["transform"]
    assert c == pytest.approx(min_x)
    assert f == pytest.approx(max_y)
    assert a == pytest.approx((max_x - min_x) / 256)
    assert e == pytest.approx((min_y - max_y) / 256)
