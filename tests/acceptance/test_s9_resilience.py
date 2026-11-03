"""S9 — throttling, retries, hard-fail default, opt-in soft-fill (#97 → #61/#59/#65)."""

from __future__ import annotations

from typing import Any

import httpx
import numpy as np
import pytest
import respx

from tilearray.array import _load_tile_array, create_array
from tilearray.errors import NetworkError
from tilearray.fetch import FetchPolicy, FetchProgress, TileFetcher
from tilearray.service.base import BaseService, TileGeometry
from tilearray.tile_errors import soft_fail_exhausted_tiles
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

from .catalog import scenario_by_id

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    return np.ones((request.height or 1, request.width or 1), dtype=np.float32)


def _failing_decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    raise ValueError("corrupt tile body")


@respx.mock
def test_s9_retries_honour_retry_after_on_429() -> None:
    assert scenario_by_id("S9")["baseline"] == "pass"
    progress = FetchProgress(total=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=2)
    respx.get("https://example.invalid/tile").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, text="slow"),
            httpx.Response(200, content=b"ok"),
        ]
    )
    request = TileRequest(
        url="https://example.invalid/tile",
        params={},
        output_format=Format.GEOTIFF,
        width=2,
        height=2,
        retries=2,
    )
    array = _load_tile_array(
        request, None, _decoder, np.dtype("float32"), policy, progress
    )
    assert array.shape == (2, 2)
    assert progress.is_aborted() is False


@respx.mock
def test_s9_hard_fail_default_and_opt_in_soft_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OneTileService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.GEOTIFF

        def __init__(self) -> None:
            super().__init__("https://example.invalid")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            return [
                TileRequest(
                    url="https://example.invalid/bad",
                    params={},
                    output_format=Format.GEOTIFF,
                    crs=bbox.crs,
                    bbox=bbox,
                    width=chunk_size[0],
                    height=chunk_size[1],
                    retries=0,
                )
            ]

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=b"nope",
            content_type="text/plain",
            status_code=500,
            headers={},
            url=request.url,
            success=False,
            error_message="HTTP 500",
        )

    import tilearray.array as array_module
    import tilearray.decode as decode_module

    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: OneTileService())
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch)
    decode_module.register_tile_decoder(Format.GEOTIFF, _decoder)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError):
        create_array(
            service_url="https://example.invalid/{z}/{x}/{y}.png",
            bbox=bbox,
            crs=CRS.EPSG_4326,
            chunk_size=(2, 2),
            grid_shape=(1, 1),
            show_progress=False,
        ).compute()

    soft = create_array(
        service_url="https://example.invalid/{z}/{x}/{y}.png",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(2, 2),
        grid_shape=(1, 1),
        soft_fail=True,
        soft_fail_max_fraction=1.0,
        show_progress=False,
    ).compute()
    assert np.isnan(soft.values).all()
    assert soft_fail_exhausted_tiles is not None


@respx.mock
def test_s9_corrupt_body_fails_loudly() -> None:
    progress = FetchProgress(total=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0)
    respx.get("https://example.invalid/corrupt").mock(
        return_value=httpx.Response(200, content=b"not-an-image")
    )
    request = TileRequest(
        url="https://example.invalid/corrupt",
        params={},
        output_format=Format.GEOTIFF,
        width=2,
        height=2,
        retries=0,
    )
    with pytest.raises(ValueError, match="corrupt tile body"):
        _load_tile_array(
            request,
            None,
            _failing_decoder,
            np.dtype("float32"),
            policy,
            progress,
        )
