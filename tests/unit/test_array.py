import base64
import math
import os
import threading
import time
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, cast

import numpy as np
import pytest
import xarray as xr
from PIL import Image
from pytest import MonkeyPatch

import tilearray.array as array_module
import tilearray.decode as decode_module
from tilearray.array import (
    ArrayRequest,
    _fetch_with_cache,
    _organize_tiles,
    _read_cache,
    _resize_tile_array,
    _write_cache,
    compute_thread_pool_size,
)
from tilearray.decode import (
    decoder_for_format,
    read_geotiff_bytes,
    read_geotiff_path,
    unwrap_multipart,
)
from tilearray.errors import ConfigurationError, NetworkError, ValidationError
from tilearray.fetch import FetchPolicy
from tilearray.fetch_presets import ea_dsp_fetch_defaults
from tilearray.service.base import BaseService, TileGeometry
from tilearray.service.config import WCSConfig
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)


@pytest.fixture(autouse=True)
def preserve_decoder_registry():
    original = dict(decode_module._DECODER_REGISTRY)
    try:
        yield
    finally:
        decode_module._DECODER_REGISTRY = original


class DummyService:
    service_type = ServiceTypeEnum.WCS
    coverage_id = "dummy"
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
                params={"tile": "0"},
                output_format=Format.GEOTIFF,
                crs=bbox.crs,
                bbox=bbox,
                width=width,
                height=height,
            )
        ]


def test_array_request_from_inputs_applies_defaults() -> None:
    config = WCSConfig.from_url(
        "http://example.com/wcs",
        coverage_id="cov",
        chunk_size=(128, 128),
        grid_shape=(2, 3),
        resolution=(4.0, 4.0),
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=512, max_y=1024, crs=CRS.EPSG_4326)

    request = ArrayRequest.from_inputs(
        service_url=config.base_url,
        service_config=config,
        bbox_input=bbox,
        crs_input=CRS.EPSG_4326,
        chunk_size_input=None,
        grid_shape_input=None,
        output_format_input=None,
        cache_dir_input=None,
        service_options_input={},
    )

    assert request.chunk_size == (128, 128)
    assert request.grid_shape == (2, 3)
    assert request.resolution == (4.0, 4.0)
    assert request.output_format == config.output_format
    assert request.service_url == config.base_url


def test_array_request_infers_grid_from_resolution() -> None:
    bbox = (0.0, 0.0, 512.0, 512.0)

    request = ArrayRequest.from_inputs(
        service_url="http://example.com/wcs",
        service_config=None,
        bbox_input=bbox,
        crs_input=CRS.EPSG_4326,
        chunk_size_input=(256, 256),
        grid_shape_input=None,
        output_format_input=Format.PNG,
        cache_dir_input=None,
        service_options_input={"resolution": (1.0, 1.0)},
    )

    assert request.grid_shape == (2, 2)
    assert request.resolution == (1.0, 1.0)


def test_array_request_snaps_resolution_bbox_outward() -> None:
    bbox = BoundingBox(
        min_x=0.0, min_y=0.0, max_x=1024.1, max_y=512.09, crs=CRS.EPSG_27700
    )

    request = ArrayRequest.from_inputs(
        service_url="http://example.com/wcs",
        service_config=None,
        bbox_input=bbox,
        crs_input=CRS.EPSG_27700,
        chunk_size_input=(1024, 512),
        grid_shape_input=None,
        output_format_input=Format.GEOTIFF,
        cache_dir_input=None,
        service_options_input={"resolution": (1.0, 1.0)},
    )

    assert request.bbox.max_x == 1025.0
    assert request.bbox.max_y == 513.0
    assert request.grid_shape == (2, 2)
    assert request.bbox_snap_delta == {
        "min_x": 0.0,
        "min_y": 0.0,
        "max_x": pytest.approx(0.9),
        "max_y": pytest.approx(0.91),
    }
    attrs = request.array_attrs(
        request.build_service(ServiceTypeEnum.WCS),
        request.tile_options(),
        Format.GEOTIFF,
    )
    assert "bbox_snap_delta" in attrs
    assert attrs["bbox_requested"]["max_x"] == 1024.1


def test_array_request_rejects_invalid_origin() -> None:
    with pytest.raises(ValidationError, match="origin"):
        ArrayRequest.from_inputs(
            service_url="http://example.com/wcs",
            service_config=None,
            bbox_input=(0.0, 0.0, 10.0, 10.0),
            crs_input=CRS.EPSG_4326,
            chunk_size_input=(8, 8),
            grid_shape_input=None,
            output_format_input=None,
            cache_dir_input=None,
            service_options_input={"resolution": (1.0, 1.0), "origin": "bad"},
        )


def test_create_array_with_custom_decoder(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    bbox = (-1.0, 50.0, -0.5, 50.5)
    calls: list[TileRequest] = []

    def fake_get_service(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        calls.append(request)
        width = request.width or 1
        height = request.height or 1
        return TileResponse(
            data=b"\x00" * (width * height),
            content_type="application/octet-stream",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(8, 8),
        cache_dir=tmp_path,
    )

    assert result.dims == ("y", "x")
    assert result.shape == (8, 8)
    compute_fn = cast(Callable[[], xr.DataArray], result.compute)
    computed = compute_fn()
    assert np.allclose(computed, 1.0)
    assert calls, "Expected fetch_tile to be called"


def test_create_array_without_decoder_raises(monkeypatch: MonkeyPatch) -> None:
    def fake_service_factory(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

    monkeypatch.setattr(array_module, "get_service", fake_service_factory)
    monkeypatch.setattr(decode_module, "_DECODER_REGISTRY", {})

    with pytest.raises(ConfigurationError):
        array_module.create_array(
            service_url="http://example.com/wcs",
            bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
            crs=CRS.EPSG_4326,
        )


def test_create_array_with_service_config(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    config = WCSConfig.from_url(
        "http://example.com/wcs",
        coverage_id="dummy",
        chunk_size=(4, 4),
        cache_dir=tmp_path,
    )

    calls: list[TileRequest] = []

    def fake_build_service(self: WCSConfig) -> DummyService:
        return DummyService()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        calls.append(request)
        width = request.width or 1
        height = request.height or 1
        return TileResponse(
            data=b"\x00" * (width * height),
            content_type="application/octet-stream",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    monkeypatch.setattr(WCSConfig, "build_service", fake_build_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url=config,
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        crs=CRS.EPSG_4326,
    )

    assert result.shape == (4, 4)
    compute_fn = cast(Callable[[], xr.DataArray], result.compute)
    computed = compute_fn()
    assert np.allclose(computed, 1.0)
    assert result.attrs["service_url"] == config.base_url
    assert result.attrs["coverage_id"] == "dummy"
    assert calls, "Expected fetch_tile to be called"


def test_create_array_infers_decoder_from_service(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    class DecoderService(DummyService):
        output_format = Format.GEOTIFF

    def fake_get_service(*args: Any, **kwargs: Any) -> DecoderService:
        return DecoderService()

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
            error_message=None,
        )

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)
    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=(-1.0, 50.0, -0.5, 50.5),
        crs=CRS.EPSG_4326,
        chunk_size=(4, 4),
        cache_dir=tmp_path,
    )

    compute_fn = cast(Callable[[], xr.DataArray], result.compute)
    computed = compute_fn()
    assert computed.shape == (4, 4)
    assert np.allclose(computed, 1.0)


def test_builtin_png_decoder(monkeypatch: MonkeyPatch, tmp_path: Path) -> None:
    class PNGService(BaseService):
        service_type = ServiceTypeEnum.WCS

        def __init__(self) -> None:
            super().__init__("http://example.com")
            self.output_format = Format.PNG

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            width, height = chunk_size
            return [
                TileRequest(
                    url="http://example.com/png",
                    params={},
                    output_format=Format.PNG,
                    crs=bbox.crs,
                    bbox=bbox,
                    width=width,
                    height=height,
                )
            ]

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR4nGP4//8/AxJgYGBg+I8BADCBA/5Yy7d/AAAAAElFTkSuQmCC"
    )

    def fake_get_service(*args: Any, **kwargs: Any) -> PNGService:
        return PNGService()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=png_data,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    result = array_module.create_array(
        service_url="http://example.com/png",
        bbox=(-1.0, 50.0, -0.5, 50.5),
        crs=CRS.EPSG_4326,
        chunk_size=(2, 2),
        cache_dir=tmp_path,
    )

    computed = cast(Callable[[], xr.DataArray], result.compute)()
    assert computed.shape == (2, 2, 4)
    assert computed.dims == ("y", "x", "band")


def test_create_array_multi_tile_rgb_jpeg_mosaic(
    monkeypatch: MonkeyPatch,
) -> None:
    """Multi-tile RGB JPEG mosaics must keep band=3, not stack bands via da.block."""

    tile_height = tile_width = 8
    rows, cols = 2, 3
    rgb = np.zeros((tile_height, tile_width, 3), dtype=np.uint8)
    rgb[..., 0] = 200
    rgb[..., 1] = 100
    rgb[..., 2] = 50
    with BytesIO() as buffer:
        Image.fromarray(rgb, mode="RGB").save(buffer, format="JPEG")
        jpeg_bytes = buffer.getvalue()

    class GridJPEGService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.JPEG

        def __init__(self) -> None:
            super().__init__("http://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            width, height = chunk_size
            grid_rows, grid_cols = options.get("grid_shape", (1, 1))
            span_x = (bbox.max_x - bbox.min_x) / grid_cols
            span_y = (bbox.max_y - bbox.min_y) / grid_rows
            tiles: list[TileRequest] = []
            for row in range(grid_rows):
                for col in range(grid_cols):
                    min_x = bbox.min_x + col * span_x
                    max_x = min_x + span_x
                    min_y = bbox.min_y + (grid_rows - 1 - row) * span_y
                    max_y = min_y + span_y
                    tile_bbox = BoundingBox(
                        min_x=min_x,
                        min_y=min_y,
                        max_x=max_x,
                        max_y=max_y,
                        crs=bbox.crs,
                    )
                    tiles.append(
                        TileRequest(
                            url=f"http://example.com/{row}/{col}.jpg",
                            params={},
                            output_format=Format.JPEG,
                            crs=bbox.crs,
                            bbox=tile_bbox,
                            width=width,
                            height=height,
                        )
                    )
            return tiles

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    def fake_get_service(*args: Any, **kwargs: Any) -> GridJPEGService:
        return GridJPEGService()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=jpeg_bytes,
            content_type="image/jpeg",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    bbox = BoundingBox(min_x=-5, min_y=50, max_x=0, max_y=55, crs=CRS.EPSG_4326)
    result = array_module.create_array(
        service_url="http://example.com",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(tile_width, tile_height),
        grid_shape=(rows, cols),
        output_format=Format.JPEG,
        compute=True,
    )

    assert result.shape == (tile_height * rows, tile_width * cols, 3)
    assert result.dims == ("y", "x", "band")
    assert np.isfinite(result).all()
    assert float(result[..., 0].mean()) == pytest.approx(200.0, rel=1e-3)
    assert float(result[..., 1].mean()) == pytest.approx(100.0, rel=1e-3)
    assert float(result[..., 2].mean()) == pytest.approx(50.0, rel=1e-3)


def test_plan_tiles_uses_resolution() -> None:
    recorded: list[TileRequest] = []

    class RecordingService(BaseService):
        service_type = ServiceTypeEnum.WCS

        def __init__(self) -> None:
            super().__init__("http://example.com")

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            request = TileRequest(
                url="http://example.com/wcs",
                params={},
                output_format=Format.GEOTIFF,
                crs=tile.crs,
                bbox=tile.bbox,
                width=tile.width,
                height=tile.height,
            )
            recorded.append(request)
            return request

    service = RecordingService()
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1000, max_y=1000, crs=CRS.EPSG_4326)
    requests = service.generate_tile_requests(
        bbox,
        (500, 500),
        resolution=(1.0, 1.0),
    )

    assert len(requests) == 4
    assert {req.width for req in recorded} == {500}
    assert {req.height for req in recorded} == {500}


@pytest.mark.unit
def test_create_array_y_coords_north_at_row_zero(
    monkeypatch: MonkeyPatch,
) -> None:
    """Y coordinates must match _organize_tiles: row 0 is north (max_y)."""

    rows, cols = 2, 2
    chunk = 2
    bbox = BoundingBox(min_x=0, min_y=0, max_x=4, max_y=4, crs=CRS.EPSG_4326)

    class GridTileService(BaseService):
        service_type = ServiceTypeEnum.WCS

        def __init__(self) -> None:
            super().__init__("http://example.com/wcs")
            self.output_format = Format.GEOTIFF

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return TileRequest(
                url="http://example.com/wcs",
                params={},
                output_format=Format.GEOTIFF,
                crs=tile.crs,
                bbox=tile.bbox,
                width=tile.width,
                height=tile.height,
            )

    def fake_get_service(*args: Any, **kwargs: Any) -> GridTileService:
        return GridTileService()

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
            error_message=None,
        )

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        assert request.bbox is not None
        height = request.height or 1
        width = request.width or 1
        # Encode tile centroid latitude so geographic alignment is checkable.
        value = float((request.bbox.min_y + request.bbox.max_y) / 2.0)
        return np.full((height, width), value, dtype=np.float32)

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)
    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(chunk, chunk),
        grid_shape=(rows, cols),
    )

    y = result.coords["y"].values
    # Pixel centers: first center is half a cell south of the north edge.
    assert y[0] == pytest.approx(bbox.max_y - 0.5)
    assert y[-1] == pytest.approx(bbox.min_y + 0.5)
    assert np.all(np.diff(y) < 0)

    computed = result.compute()
    # Northern half of the mosaic (row 0) should sit at higher y than southern half.
    north_value = float(computed.isel(y=0).mean())
    south_value = float(computed.isel(y=-1).mean())
    assert north_value > south_value


@pytest.mark.unit
def test_create_array_single_tile_pixel_center_coords(
    monkeypatch: MonkeyPatch,
) -> None:
    bbox = BoundingBox(min_x=10, min_y=20, max_x=30, max_y=40, crs=CRS.EPSG_4326)

    def fake_get_service(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

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
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(4, 4),
    )

    height, width = 4, 4
    px_w = (bbox.max_x - bbox.min_x) / width
    px_h = (bbox.max_y - bbox.min_y) / height
    y = result.coords["y"].values
    x = result.coords["x"].values
    assert y[0] == pytest.approx(bbox.max_y - px_h / 2)
    assert y[-1] == pytest.approx(bbox.min_y + px_h / 2)
    assert x[0] == pytest.approx(bbox.min_x + px_w / 2)
    assert x[-1] == pytest.approx(bbox.max_x - px_w / 2)
    assert result.attrs["transform"] == (
        px_w,
        0.0,
        bbox.min_x,
        0.0,
        -px_h,
        bbox.max_y,
    )
    assert result.attrs["transform_source"] == "mosaic_bbox"


@pytest.mark.unit
def test_create_array_mosaic_crs_matches_request_crs_no_silent_retag(
    monkeypatch: MonkeyPatch,
) -> None:
    """Design contract (#98): mosaic attrs CRS is the request crs=; no silent retag.

    Alpha uses one ``crs=`` for bbox interpretation and mosaic labelling. Pixel
    values are not client-warped onto another CRS; GeoTIFF tags must not replace
    ``attrs["crs"]``. Separate ``bbox_crs`` / output CRS is target design only.
    """

    bbox = BoundingBox(
        min_x=100.0, min_y=200.0, max_x=108.0, max_y=204.0, crs=CRS.EPSG_27700
    )

    def fake_get_service(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

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
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.full((height, width), 7.0, dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_27700,
        chunk_size=(8, 4),
        output_format=Format.GEOTIFF,
    )

    assert result.attrs["crs"] == CRS.EPSG_27700.value
    assert result.attrs["transform_source"] == "mosaic_bbox"
    a, b, c, d, e, f = result.attrs["transform"]
    assert (a, b, c, d, e, f) == (1.0, 0.0, 100.0, 0.0, -1.0, 204.0)
    assert result.coords["x"].values[0] == pytest.approx(c + a * 0.5)
    assert result.coords["y"].values[0] == pytest.approx(f + e * 0.5)


@pytest.mark.unit
def test_geotiff_elevation_attrs_crs_transform_nodata(
    monkeypatch: MonkeyPatch,
) -> None:
    """Elevation GeoTIFF path: mosaic CRS/transform + NaN nodata; no band dim."""

    bbox = BoundingBox(
        min_x=100.0, min_y=200.0, max_x=108.0, max_y=204.0, crs=CRS.EPSG_27700
    )

    def fake_get_service(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

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
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.full((height, width), 42.0, dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_27700,
        # DummyService treats chunk_size as (width, height).
        chunk_size=(8, 4),
        output_format=Format.GEOTIFF,
    )

    assert result.dims == ("y", "x")
    assert result.shape == (4, 8)
    assert result.attrs["crs"] == CRS.EPSG_27700.value
    assert result.attrs["band_policy"] == "first_band"
    assert result.attrs["transform_source"] == "mosaic_bbox"
    assert math.isnan(result.attrs["nodata"])
    assert math.isnan(result.attrs["_FillValue"])
    a, b, c, d, e, f = result.attrs["transform"]
    assert (a, b, c, d, e, f) == (1.0, 0.0, 100.0, 0.0, -1.0, 204.0)
    # Centers consistent with Affine: x = c + a*(col+0.5), y = f + e*(row+0.5)
    assert result.coords["x"].values[0] == pytest.approx(c + a * 0.5)
    assert result.coords["y"].values[0] == pytest.approx(f + e * 0.5)
    assert np.all(np.diff(result.coords["y"].values) < 0)


@pytest.mark.unit
def test_rgb_jpeg_attrs_preserve_bands_no_nodata(
    monkeypatch: MonkeyPatch,
) -> None:
    """RGB imagery: preserve bands, mosaic transform, no nodata attrs."""

    rgb = np.zeros((4, 4, 3), dtype=np.uint8)
    rgb[..., 0] = 10
    rgb[..., 1] = 20
    rgb[..., 2] = 30
    with BytesIO() as buffer:
        Image.fromarray(rgb, mode="RGB").save(buffer, format="JPEG")
        jpeg_bytes = buffer.getvalue()

    class JPEGService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.JPEG

        def __init__(self) -> None:
            super().__init__("http://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            width, height = chunk_size
            return [
                TileRequest(
                    url="http://example.com/0/0.jpg",
                    params={},
                    output_format=Format.JPEG,
                    crs=bbox.crs,
                    bbox=bbox,
                    width=width,
                    height=height,
                )
            ]

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: JPEGService())
    monkeypatch.setattr(
        array_module,
        "fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=jpeg_bytes,
            content_type="image/jpeg",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        ),
    )

    bbox = BoundingBox(min_x=-1.0, min_y=50.0, max_x=0.0, max_y=51.0, crs=CRS.EPSG_4326)
    result = array_module.create_array(
        service_url="http://example.com",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(4, 4),
        output_format=Format.JPEG,
        compute=True,
    )

    assert result.dims == ("y", "x", "band")
    assert result.sizes["band"] == 3
    assert list(result.coords["band"].values) == [0, 1, 2]
    assert result.attrs["crs"] == CRS.EPSG_4326.value
    assert result.attrs["band_policy"] == "preserve"
    assert result.attrs["transform_source"] == "mosaic_bbox"
    assert "nodata" not in result.attrs
    assert "_FillValue" not in result.attrs
    assert len(result.attrs["transform"]) == 6


@pytest.mark.unit
def test_rgba_png_attrs_four_bands_no_nodata(
    monkeypatch: MonkeyPatch,
) -> None:
    """RGBA PNG path keeps 4 bands and omits elevation nodata attrs."""

    class PNGService(BaseService):
        service_type = ServiceTypeEnum.WMS
        output_format = Format.PNG

        def __init__(self) -> None:
            super().__init__("http://example.com/wms")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            width, height = chunk_size
            return [
                TileRequest(
                    url="http://example.com/wms",
                    params={},
                    output_format=Format.PNG,
                    crs=bbox.crs,
                    bbox=bbox,
                    width=width,
                    height=height,
                )
            ]

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    png_data = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAYAAABytg0kAAAAFUlEQVR4nGP4"
        "//8/AxJgYGBg+I8BADCBA/5Yy7d/AAAAAElFTkSuQmCC"
    )

    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: PNGService())
    monkeypatch.setattr(
        array_module,
        "fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=png_data,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        ),
    )

    result = array_module.create_array(
        service_url="http://example.com/wms",
        bbox=(-1.0, 50.0, -0.5, 50.5),
        crs=CRS.EPSG_4326,
        chunk_size=(2, 2),
        output_format=Format.PNG,
        compute=True,
    )

    assert result.dims == ("y", "x", "band")
    assert result.sizes["band"] == 4
    assert result.attrs["band_policy"] == "preserve"
    assert "nodata" not in result.attrs
    assert result.attrs["transform_source"] == "mosaic_bbox"


@pytest.mark.unit
def test_mosaic_affine_and_pixel_centers_helpers() -> None:
    bbox = BoundingBox(min_x=0.0, min_y=0.0, max_x=10.0, max_y=20.0, crs=CRS.EPSG_4326)
    transform = array_module._mosaic_affine_transform(bbox, height=4, width=5)
    assert transform == (2.0, 0.0, 0.0, 0.0, -5.0, 20.0)
    y, x = array_module._pixel_center_coords(bbox, height=4, width=5)
    assert x[0] == pytest.approx(1.0)
    assert x[-1] == pytest.approx(9.0)
    assert y[0] == pytest.approx(17.5)
    assert y[-1] == pytest.approx(2.5)


def test_organize_tiles_orders_by_bbox() -> None:
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=2, crs=CRS.EPSG_4326)

    def _tile(x_index: int, y_index: int) -> TileRequest:
        min_x = x_index
        max_x = x_index + 1
        min_y = y_index
        max_y = y_index + 1
        return TileRequest(
            url="http://example.com",
            params={},
            output_format=Format.GEOTIFF,
            crs=bbox.crs,
            bbox=BoundingBox(
                min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y, crs=bbox.crs
            ),
            width=1,
            height=1,
        )

    tiles = [
        _tile(0, 0),  # bottom-left
        _tile(1, 1),  # top-right
        _tile(1, 0),  # bottom-right
        _tile(0, 1),  # top-left
    ]

    grid = _organize_tiles(tiles, rows=2, cols=2)
    assert grid[0][0].bbox.min_y == 1  # top row first
    assert grid[1][0].bbox.min_y == 0  # bottom row second
    assert grid[0][0].bbox.min_x == 0  # left-to-right within row
    assert grid[0][1].bbox.min_x == 1


@pytest.mark.unit
def test_read_ea_lidar_geotiff_fixture() -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "wcs_tiles"
        / "ea_lidar_64x64.tif"
    )
    data = read_geotiff_path(str(fixture))

    assert data.shape == (64, 64)
    assert data.dtype == np.float32
    assert np.isfinite(data).all()
    assert 100 < float(np.nanmean(data)) < 300


@pytest.mark.unit
def test_decode_geotiff_handles_oversized_native_resolution_tile() -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "wcs_tiles"
        / "ea_lidar_64x64.tif"
    )
    raw = fixture.read_bytes()
    response = TileResponse(
        data=raw,
        content_type="image/tiff",
        status_code=200,
        headers={},
        url="http://example.com/wcs",
        success=True,
        error_message=None,
    )
    request = TileRequest(
        url="http://example.com/wcs",
        params={},
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_27700),
        width=13,
        height=13,
    )

    decoder = decoder_for_format(Format.GEOTIFF)
    assert decoder is not None
    decoded = decoder(response, request)
    assert decoded.shape == (64, 64)


@pytest.mark.unit
def test_resize_tile_array_supports_non_divisible_downsample() -> None:
    source = np.arange(64 * 64, dtype=np.float32).reshape(64, 64)
    resized = _resize_tile_array(source, 13, 13)

    assert resized.shape == (13, 13)
    assert np.isfinite(resized).all()


@pytest.mark.unit
def test_create_array_resamples_native_resolution_wcs_tile(
    monkeypatch: MonkeyPatch,
) -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "wcs_tiles"
        / "ea_lidar_64x64.tif"
    )
    raw = fixture.read_bytes()
    bbox = BoundingBox(min_x=0, min_y=0, max_x=64, max_y=64, crs=CRS.EPSG_27700)

    class NativeResolutionService(BaseService):
        service_type = ServiceTypeEnum.WCS

        def __init__(self) -> None:
            super().__init__("http://example.com/wcs")
            self.output_format = Format.GEOTIFF

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return TileRequest(
                url="http://example.com/wcs",
                params={},
                output_format=Format.GEOTIFF,
                crs=tile.crs,
                bbox=tile.bbox,
                width=tile.width,
                height=tile.height,
            )

    service = NativeResolutionService()

    def fake_get_service(*args: Any, **kwargs: Any) -> NativeResolutionService:
        return service

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=raw,
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_27700,
        chunk_size=(64, 64),
        resolution=(5.0, 5.0),
    )

    computed = result.compute()
    assert computed.shape == (13, 13)
    assert np.isfinite(computed).all()


def test_create_array_on_progress_callback(monkeypatch: MonkeyPatch) -> None:
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)
    progress_events: list[tuple[int, int]] = []

    def fake_get_service(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

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
            error_message=None,
        )

    def on_progress(
        done: int,
        total: int,
        request: TileRequest,
        response: TileResponse,
    ) -> None:
        progress_events.append((done, total))

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(4, 4),
        compute=True,
        on_progress=on_progress,
        rate_limit_per_second=None,
    )

    assert result.shape == (4, 4)
    assert progress_events == [(1, 1)]


def test_load_array_raises_when_tile_fetch_fails_after_retries(
    monkeypatch: MonkeyPatch,
) -> None:
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)

    def fake_get_service(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=b"",
            content_type="",
            status_code=403,
            headers={},
            url=request.url,
            success=False,
            error_message="HTTP 403: Forbidden",
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    with pytest.raises(NetworkError, match="HTTP 403"):
        array_module.load_array(
            service_url="http://example.com/wcs",
            bbox=bbox,
            crs=CRS.EPSG_4326,
            chunk_size=(4, 4),
            fetch_retries=0,
            rate_limit_per_second=None,
        )


def test_create_array_downsamples_oversized_tiles(monkeypatch: MonkeyPatch) -> None:
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)

    def fake_get_service(*args: Any, **kwargs: Any) -> DummyService:
        return DummyService()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=b"",
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = (request.height or 1) * 2
        width = (request.width or 1) * 2
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(256, 256),
    )

    computed = result.compute()
    assert computed.shape == (256, 256)
    assert float(computed.mean()) == 1.0


def test_compute_thread_pool_size_at_least_fetch_ceiling() -> None:
    policy = FetchPolicy(max_concurrent=32)
    assert compute_thread_pool_size(policy) >= 32
    assert compute_thread_pool_size(policy) >= (os.cpu_count() or 1)
    assert compute_thread_pool_size(policy, override=4) == 4


def test_resolve_fetch_policy_keeps_host_ceiling_on_lower_override() -> None:
    """Lower max_concurrent_requests on a preset config is call-local, not shared."""

    config = WCSConfig.from_url(
        "https://environment.data.gov.uk/example/wcs",
        coverage_id="test-coverage",
        **ea_dsp_fetch_defaults(),
    )
    assert config.max_concurrent_requests == 10

    polite = array_module._resolve_fetch_policy(config, {"max_concurrent_requests": 1})
    assert polite.max_concurrent == 10
    assert polite.call_max_concurrent == 1
    assert polite.adaptive_concurrency is True
    assert polite.forbidden_circuit_breaker is True

    raised = array_module._resolve_fetch_policy(config, {"max_concurrent_requests": 12})
    assert raised.max_concurrent == 12
    assert raised.call_max_concurrent is None

    bare = array_module._resolve_fetch_policy(None, {"max_concurrent_requests": 1})
    assert bare.max_concurrent == 1
    assert bare.call_max_concurrent is None


def test_create_array_compute_exceeds_cpu_count_inflight(
    monkeypatch: MonkeyPatch,
) -> None:
    cpu = os.cpu_count() or 1
    rows, cols = 4, 4
    tile_count = rows * cols
    max_concurrent = cpu + 8

    inflight = 0
    peak = 0
    lock = threading.Lock()

    class GridTileService(DummyService):
        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            width, height = chunk_size
            grid_rows, grid_cols = options.get("grid_shape", (1, 1))
            span_x = (bbox.max_x - bbox.min_x) / grid_cols
            span_y = (bbox.max_y - bbox.min_y) / grid_rows
            tiles: list[TileRequest] = []
            for row in range(grid_rows):
                for col in range(grid_cols):
                    min_x = bbox.min_x + col * span_x
                    max_x = min_x + span_x
                    min_y = bbox.min_y + (grid_rows - 1 - row) * span_y
                    max_y = min_y + span_y
                    tile_bbox = BoundingBox(
                        min_x=min_x,
                        min_y=min_y,
                        max_x=max_x,
                        max_y=max_y,
                        crs=bbox.crs,
                    )
                    tiles.append(
                        TileRequest(
                            url=f"http://example.com/wcs/{row}/{col}",
                            params={"tile": f"{row}-{col}"},
                            output_format=Format.GEOTIFF,
                            crs=bbox.crs,
                            bbox=tile_bbox,
                            width=width,
                            height=height,
                        )
                    )
            return tiles

    def fake_get_service(*args: Any, **kwargs: Any) -> GridTileService:
        return GridTileService()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        nonlocal inflight, peak
        with lock:
            inflight += 1
            peak = max(peak, inflight)
        time.sleep(0.05)
        with lock:
            inflight -= 1
        width = request.width or 1
        height = request.height or 1
        return TileResponse(
            data=b"\x00" * (width * height),
            content_type="application/octet-stream",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)
    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=4, max_y=4, crs=CRS.EPSG_4326)
    array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(2, 2),
        grid_shape=(rows, cols),
        compute=True,
        max_concurrent_requests=max_concurrent,
        rate_limit_per_second=None,
    )

    assert peak > cpu
    assert peak >= min(tile_count, max_concurrent) // 2


@pytest.mark.unit
def test_decode_raster_image_preserves_rgb_bands() -> None:
    source = np.zeros((64, 64, 3), dtype=np.uint8)
    source[..., 0] = 200
    source[..., 1] = 100
    source[..., 2] = 50
    with BytesIO() as buffer:
        Image.fromarray(source, mode="RGB").save(buffer, format="JPEG")
        raw = buffer.getvalue()

    response = TileResponse(
        data=raw,
        content_type="image/jpeg",
        status_code=200,
        headers={},
        url="https://example.com/tile.jpg",
        success=True,
    )
    request = TileRequest(url="https://example.com/tile.jpg", params={})

    decoder = decoder_for_format(Format.JPEG)
    assert decoder is not None
    decoded = decoder(response, request)

    assert decoded.shape == (64, 64, 3)
    assert float(decoded[..., 0].mean()) == pytest.approx(200.0)
    assert float(decoded[..., 1].mean()) == pytest.approx(100.0)
    assert float(decoded[..., 2].mean()) == pytest.approx(50.0)


@pytest.mark.unit
def test_resize_tile_array_preserves_rgb_bands() -> None:
    source = np.stack(
        [
            np.full((64, 64), 200.0, dtype=np.float32),
            np.full((64, 64), 100.0, dtype=np.float32),
            np.full((64, 64), 50.0, dtype=np.float32),
        ],
        axis=-1,
    )

    resized = _resize_tile_array(source, 32, 32)

    assert resized.shape == (32, 32, 3)
    assert float(resized[..., 0].mean()) == pytest.approx(200.0)
    assert float(resized[..., 1].mean()) == pytest.approx(100.0)
    assert float(resized[..., 2].mean()) == pytest.approx(50.0)


GEOTIFF_FIXTURE = (
    Path(__file__).resolve().parents[1] / "data" / "wcs_tiles" / "ea_lidar_64x64.tif"
)


def test_cache_round_trip_multipart_geotiff_preserves_decode(tmp_path: Path) -> None:
    tiff_bytes = GEOTIFF_FIXTURE.read_bytes()
    multipart_body = (
        b"--wcs\r\n"
        b"Content-Type: text/xml\r\n"
        b"Content-ID: GML-Part\r\n\r\n"
        b"<gmlcov:RectifiedGridCoverage/>\r\n"
        b"--wcs\r\n"
        b"Content-Type: image/tiff\r\n"
        b"Content-ID: coverage.tif\r\n"
        b"Content-Transfer-Encoding: binary\r\n\r\n" + tiff_bytes + b"\r\n--wcs--\r\n"
    )
    request = TileRequest(
        url="https://example.com/wcs",
        params={"coverage": "dtm"},
        output_format=Format.GEOTIFF,
        width=64,
        height=64,
    )
    content_type = 'multipart/related; boundary="wcs"'

    _write_cache(tmp_path, request, multipart_body, content_type)
    cached = _read_cache(tmp_path, request)
    assert cached is not None
    cached_data, cached_type = cached
    assert cached_data == multipart_body
    assert cached_type == content_type

    def fail_fetch(*args: Any, **kwargs: Any) -> TileResponse:
        raise AssertionError("fetch_tile should not run on warm cache hit")

    import tilearray.array as array_module

    original_fetch = array_module.fetch_tile
    array_module.fetch_tile = fail_fetch  # type: ignore[assignment]
    try:
        response = _fetch_with_cache(request, tmp_path)
    finally:
        array_module.fetch_tile = original_fetch

    assert response.success is True
    assert response.content_type == content_type
    unwrapped = unwrap_multipart(bytes(response.data), response)
    decoded = read_geotiff_bytes(unwrapped)
    assert decoded.shape == (64, 64)


def test_cache_legacy_multipart_without_meta_sniffs_content_type(
    tmp_path: Path,
) -> None:
    tiff_bytes = GEOTIFF_FIXTURE.read_bytes()
    multipart_body = (
        b"--wcs\r\nContent-Type: image/tiff\r\n\r\n" + tiff_bytes + b"\r\n--wcs--\r\n"
    )
    request = TileRequest(
        url="https://example.com/wcs",
        params={"coverage": "legacy"},
        output_format=Format.GEOTIFF,
        width=64,
        height=64,
    )
    key_path = tmp_path / f"{array_module._cache_key(request)}.tile"
    key_path.write_bytes(multipart_body)

    cached = _read_cache(tmp_path, request)
    assert cached is not None
    _, cached_type = cached
    assert cached_type.startswith("multipart/related")
