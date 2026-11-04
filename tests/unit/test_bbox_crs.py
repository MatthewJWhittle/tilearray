"""Public ``bbox_crs=`` query CRS (replaces overloaded create/plan ``crs=``)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import numpy as np
import pytest
import xarray as xr
from PIL import Image
from pytest import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
import tilearray.decode as decode_module
from tilearray import create_array, plan_array
from tilearray.errors import ValidationError
from tilearray.fetch import TileFetcher
from tilearray.service.base import TileGeometry
from tilearray.service.config import WMSConfig, WMTSConfig, XYZConfig
from tilearray.service.wmts import WMTSTileMatrix
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

OSM_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
WMTS_URL = "https://example.com/wmts"
WMS_URL = "https://example.com/wms"

_UTM33_SCALE = 3571.4285714285716
_UTM33_TOP_LEFT_X = -92761.87160000019
_UTM33_TOP_LEFT_Y = 8_041_211.588458344
_OGC_PIXEL_M = 0.00028
_UTM33_PIXEL_M = _UTM33_SCALE * _OGC_PIXEL_M
_UTM33_TILE_SPAN = _UTM33_PIXEL_M * 256


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


@pytest.fixture
def png_tile_bytes() -> bytes:
    array = np.full((256, 256), 128, dtype=np.uint8)
    with BytesIO() as buffer:
        Image.fromarray(array, mode="L").save(buffer, format="PNG")
        return buffer.getvalue()


def _mock_fetch_tile(png_tile_bytes: bytes):
    def fetch_tile_impl(request: TileRequest, **kwargs: object) -> TileResponse:
        return TileResponse(
            data=png_tile_bytes,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    return fetch_tile_impl


class _DummyWCS:
    service_type = ServiceTypeEnum.WCS
    output_format = Format.GEOTIFF

    def generate_tile_requests(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: Any,
    ) -> list[TileRequest]:
        width, height = chunk_size
        return [
            TileRequest(
                url="http://example.com/wcs",
                params={},
                headers={},
                bbox=bbox,
                crs=bbox.crs,
                width=width,
                height=height,
                output_format=Format.GEOTIFF,
            )
        ]

    def plan_tiles(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: Any,
    ) -> list[TileGeometry]:
        width, height = chunk_size
        return [TileGeometry(bbox=bbox, width=width, height=height, crs=bbox.crs)]


def test_create_array_bbox_crs_xyz_native_3857(
    png_tile_bytes: bytes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))
    bbox = BoundingBox(
        min_x=-0.001, min_y=51.5, max_x=0.001, max_y=51.501, crs=CRS.EPSG_4326
    )
    config = XYZConfig.from_url(
        OSM_TEMPLATE, zoom=10, output_format=Format.PNG, chunk_size=(256, 256)
    )
    result = create_array(
        config, bbox, CRS.EPSG_4326, compute=True, show_progress=False
    )
    assert result.attrs["crs"] == "EPSG:3857"
    assert result.attrs["bbox_crs"] == "EPSG:4326"
    assert result.attrs["bbox_requested"]["crs"] == "EPSG:4326"


def test_create_array_bbox_crs_keyword_matches_positional(
    png_tile_bytes: bytes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))
    bbox = (-0.001, 51.5, 0.001, 51.501)
    config = XYZConfig.from_url(
        OSM_TEMPLATE, zoom=10, output_format=Format.PNG, chunk_size=(256, 256)
    )
    positional = create_array(
        config, bbox, CRS.EPSG_4326, compute=True, show_progress=False
    )
    keyword = create_array(
        config, bbox, bbox_crs=CRS.EPSG_4326, compute=True, show_progress=False
    )
    assert positional.attrs["crs"] == keyword.attrs["crs"] == "EPSG:3857"
    assert positional.attrs["bbox_crs"] == keyword.attrs["bbox_crs"] == "EPSG:4326"
    assert positional.shape == keyword.shape


def test_create_array_rejects_old_crs_keyword(
    png_tile_bytes: bytes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))
    config = XYZConfig.from_url(OSM_TEMPLATE, zoom=10)
    with pytest.raises(ValidationError, match="no longer accepts crs="):
        create_array(  # type: ignore[call-arg]
            config,
            (-0.001, 51.5, 0.001, 51.501),
            CRS.EPSG_4326,
            crs=CRS.EPSG_4326,
            show_progress=False,
        )


def test_create_array_bbox_crs_wmts_native_matrix(
    png_tile_bytes: bytes, monkeypatch: MonkeyPatch
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
    bbox_proj = BoundingBox(
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
        bbox_proj,
        "EPSG:25833",
        compute=True,
        matrix=matrix,
        matrix_crs=CRS.parse("EPSG:25833"),
        show_progress=False,
    )
    assert result.attrs["crs"] == "EPSG:25833"
    assert result.attrs["bbox_crs"] == "EPSG:25833"
    assert "bbox_requested" in result.attrs


def test_create_array_bbox_crs_wms(
    png_tile_bytes: bytes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))
    config = WMSConfig.from_url(
        WMS_URL,
        layers="vegetation",
        output_format=Format.PNG,
        chunk_size=(128, 128),
        grid_shape=(1, 1),
    )
    bbox = BoundingBox(
        min_x=-0.05, min_y=51.45, max_x=0.05, max_y=51.55, crs=CRS.EPSG_4326
    )
    result = create_array(
        config, bbox, CRS.EPSG_4326, compute=True, show_progress=False
    )
    assert isinstance(result, xr.DataArray)
    assert result.attrs["crs"] == "EPSG:4326"
    assert result.attrs["bbox_crs"] == "EPSG:4326"


def test_create_array_bbox_crs_wcs(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: _DummyWCS())

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        width = request.width or 1
        height = request.height or 1
        return TileResponse(
            data=b"\x00" * (width * height),
            content_type="application/octet-stream",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.full((height, width), 3.0, dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)
    bbox = BoundingBox(
        min_x=100.0, min_y=200.0, max_x=108.0, max_y=204.0, crs=CRS.EPSG_27700
    )
    result = create_array(
        "http://example.com/wcs",
        bbox,
        CRS.EPSG_27700,
        chunk_size=(8, 4),
        output_format=Format.GEOTIFF,
        show_progress=False,
    )
    assert result.attrs["crs"] == CRS.EPSG_27700.value
    assert result.attrs["bbox_crs"] == CRS.EPSG_27700.value


def test_plan_array_exposes_bbox_crs_and_output_crs(
    png_tile_bytes: bytes, monkeypatch: MonkeyPatch
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))
    bbox = (-0.001, 51.5, 0.001, 51.501)
    config = XYZConfig.from_url(
        OSM_TEMPLATE, zoom=10, output_format=Format.PNG, chunk_size=(256, 256)
    )
    plan = plan_array(config, bbox, CRS.EPSG_4326)
    assert plan.bbox_crs == "EPSG:4326"
    assert plan.crs == "EPSG:3857"
    text = plan.summary()
    assert "bbox_crs (query)" in text
    assert "crs (output)" in text
