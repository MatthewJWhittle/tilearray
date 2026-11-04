"""Contract tests for default multi-tile progress (#86)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from pytest import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
import tilearray.decode as decode_module
import tilearray.progress as progress_module
from tilearray.fetch import FetchProgress
from tilearray.progress import (
    make_tile_progress_callback,
    resolve_on_progress,
    resolve_show_progress,
)
from tilearray.service.base import BaseService, TileGeometry
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


@pytest.mark.parametrize(
    ("total", "show_progress", "env", "display", "expected"),
    [
        (4, False, None, True, False),
        (4, True, "0", False, True),
        (1, None, None, True, False),
        (4, None, None, True, True),
        (4, None, None, False, False),
        (4, None, "0", True, False),
        (4, None, "false", True, False),
        (4, None, "off", True, False),
        (1, None, "1", False, True),
        (4, None, "yes", False, True),
    ],
)
def test_resolve_show_progress_matrix(
    monkeypatch: MonkeyPatch,
    total: int,
    show_progress: bool | None,
    env: str | None,
    display: bool,
    expected: bool,
) -> None:
    if env is None:
        monkeypatch.delenv(progress_module.PROGRESS_ENV, raising=False)
    else:
        monkeypatch.setenv(progress_module.PROGRESS_ENV, env)
    monkeypatch.setattr(progress_module, "_display_supports_progress", lambda: display)

    assert (
        resolve_show_progress(total_tiles=total, show_progress=show_progress)
        is expected
    )


def test_resolve_on_progress_prefers_user_callback(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(progress_module, "_display_supports_progress", lambda: True)
    calls: list[int] = []

    def user(
        done: int,
        total: int,
        request: TileRequest,
        response: TileResponse,
    ) -> None:
        del total, request, response
        calls.append(done)

    resolved = resolve_on_progress(user, total_tiles=4, show_progress=None)
    assert resolved is user


def test_resolve_on_progress_quiet_when_disabled(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(progress_module, "_display_supports_progress", lambda: True)
    assert resolve_on_progress(None, total_tiles=4, show_progress=False) is None


def test_make_tile_progress_callback_ticks_and_closes(
    monkeypatch: MonkeyPatch,
) -> None:
    bar = MagicMock()
    bar.n = 0

    def fake_tqdm(*args: Any, **kwargs: Any) -> MagicMock:
        del args, kwargs
        return bar

    monkeypatch.setattr(progress_module, "tqdm", fake_tqdm)

    callback = make_tile_progress_callback(3)
    request = TileRequest(
        url="http://example.com/0",
        params={},
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=1,
        height=1,
    )
    response = TileResponse(
        data=b"\x00",
        content_type="application/octet-stream",
        status_code=200,
        headers={},
        url=request.url,
        success=True,
        error_message=None,
    )

    callback(1, 3, request, response)
    assert bar.update.call_args_list[0].args == (1,)
    bar.n = 1
    callback(2, 3, request, response)
    assert bar.update.call_args_list[1].args == (1,)
    bar.n = 2
    callback(3, 3, request, response)
    assert bar.update.call_args_list[2].args == (1,)
    bar.close.assert_called_once()


def test_fetch_progress_default_bar_via_tick(monkeypatch: MonkeyPatch) -> None:
    """Default callback is invoked through FetchProgress.tick (compose path)."""

    events: list[tuple[int, int]] = []

    def recording(
        done: int,
        total: int,
        request: TileRequest,
        response: TileResponse,
    ) -> None:
        del request, response
        events.append((done, total))

    monkeypatch.setattr(
        progress_module,
        "make_tile_progress_callback",
        lambda total, **kwargs: recording,
    )
    monkeypatch.setattr(progress_module, "_display_supports_progress", lambda: True)

    callback = resolve_on_progress(None, total_tiles=2, show_progress=None)
    progress = FetchProgress(total=2, on_progress=callback)
    request = TileRequest(
        url="http://example.com/1",
        params={},
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=1,
        height=1,
    )
    ok = TileResponse(
        data=b"\x00",
        content_type="application/octet-stream",
        status_code=200,
        headers={},
        url=request.url,
        success=True,
        error_message=None,
    )
    progress.tick(request, ok)
    progress.tick(request, ok)
    assert events == [(1, 2), (2, 2)]


def test_create_array_default_progress_multi_tile(
    monkeypatch: MonkeyPatch,
) -> None:
    rows, cols = 2, 2
    tile_height = tile_width = 4
    progress_events: list[tuple[int, int]] = []

    class GridService(BaseService):
        service_type = ServiceTypeEnum.WCS
        coverage_id = "dummy"
        output_format = Format.GEOTIFF

        def __init__(self) -> None:
            super().__init__("http://example.com/wcs")

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
                    tiles.append(
                        TileRequest(
                            url=f"http://example.com/{row}/{col}",
                            params={},
                            output_format=Format.GEOTIFF,
                            crs=bbox.crs,
                            bbox=BoundingBox(
                                min_x=min_x,
                                min_y=min_y,
                                max_x=max_x,
                                max_y=max_y,
                                crs=bbox.crs,
                            ),
                            width=width,
                            height=height,
                        )
                    )
            return tiles

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    def fake_get_service(*args: Any, **kwargs: Any) -> GridService:
        return GridService()

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

    def recording_callback(
        done: int,
        total: int,
        request: TileRequest,
        response: TileResponse,
    ) -> None:
        del request, response
        progress_events.append((done, total))

    monkeypatch.setattr(planning_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)
    monkeypatch.setattr(
        progress_module,
        "make_tile_progress_callback",
        lambda total, **kwargs: recording_callback,
    )
    monkeypatch.setattr(progress_module, "_display_supports_progress", lambda: True)
    monkeypatch.delenv(progress_module.PROGRESS_ENV, raising=False)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=2, crs=CRS.EPSG_4326)
    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        chunk_size=(tile_width, tile_height),
        grid_shape=(rows, cols),
        compute=True,
        rate_limit_per_second=None,
    )

    assert result.shape == (tile_height * rows, tile_width * cols)
    assert len(progress_events) == rows * cols
    assert progress_events[-1] == (rows * cols, rows * cols)


def test_create_array_show_progress_false_skips_default(
    monkeypatch: MonkeyPatch,
) -> None:
    rows, cols = 2, 2
    tile_height = tile_width = 4
    made = {"count": 0}

    def tracking_factory(total: int, **kwargs: Any) -> Any:
        del kwargs
        made["count"] += 1

        def _noop(
            done: int,
            total_tiles: int,
            request: TileRequest,
            response: TileResponse,
        ) -> None:
            del done, total_tiles, request, response

        return _noop

    class GridService(BaseService):
        service_type = ServiceTypeEnum.WCS
        coverage_id = "dummy"
        output_format = Format.GEOTIFF

        def __init__(self) -> None:
            super().__init__("http://example.com/wcs")

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
                    tiles.append(
                        TileRequest(
                            url=f"http://example.com/{row}/{col}",
                            params={},
                            output_format=Format.GEOTIFF,
                            crs=bbox.crs,
                            bbox=BoundingBox(
                                min_x=min_x,
                                min_y=min_y,
                                max_x=max_x,
                                max_y=max_y,
                                crs=bbox.crs,
                            ),
                            width=width,
                            height=height,
                        )
                    )
            return tiles

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    def fake_get_service(*args: Any, **kwargs: Any) -> GridService:
        return GridService()

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

    monkeypatch.setattr(planning_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)
    monkeypatch.setattr(
        progress_module, "make_tile_progress_callback", tracking_factory
    )
    monkeypatch.setattr(progress_module, "_display_supports_progress", lambda: True)
    monkeypatch.delenv(progress_module.PROGRESS_ENV, raising=False)

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=2, crs=CRS.EPSG_4326)
    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        chunk_size=(tile_width, tile_height),
        grid_shape=(rows, cols),
        compute=True,
        show_progress=False,
        rate_limit_per_second=None,
    )

    assert result.shape == (tile_height * rows, tile_width * cols)
    assert made["count"] == 0
