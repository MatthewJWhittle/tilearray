import base64
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
    _organize_tiles,
    _resize_tile_array,
    compute_thread_pool_size,
)
from tilearray.decode import (
    decoder_for_format,
    read_geotiff_path,
)
from tilearray.errors import NetworkError
from tilearray.fetch import FetchPolicy
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

    with pytest.raises(RuntimeError):
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
