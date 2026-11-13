"""Mosaic Dask HighLevelGraph layer count stays O(1) vs tile count (#185 / #178)."""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from pytest import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
import tilearray.decode as decode_module
from tilearray.mosaic_plan import MosaicPlan
from tilearray.service.base import BaseService, TileGeometry
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = pytest.mark.unit

# Fixed small bound: stackstac-style from_array + map_blocks is a handful of
# layers; must not grow with tile count (delayed/from_delayed was ~2N).
_MAX_HLG_LAYERS = 16


@pytest.fixture(autouse=True)
def preserve_decoder_registry() -> Any:
    original = dict(decode_module._DECODER_REGISTRY)
    try:
        yield
    finally:
        decode_module._DECODER_REGISTRY = original


def _grid_service(rows: int, cols: int, *, tile_h: int = 4, tile_w: int = 4) -> type:
    class GridService(BaseService):
        service_type = ServiceTypeEnum.WCS
        output_format = Format.GEOTIFF

        def __init__(self) -> None:
            super().__init__("http://example.com/wcs")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            width, height = chunk_size
            span_x = (bbox.max_x - bbox.min_x) / cols
            span_y = (bbox.max_y - bbox.min_y) / rows
            tiles: list[TileRequest] = []
            for row in range(rows):
                for col in range(cols):
                    min_x = bbox.min_x + col * span_x
                    max_x = min_x + span_x
                    min_y = bbox.min_y + (rows - 1 - row) * span_y
                    max_y = min_y + span_y
                    tiles.append(
                        TileRequest(
                            url=f"http://example.com/wcs/{row}/{col}",
                            params={"r": str(row), "c": str(col)},
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
            return MosaicPlan(tiles=tuple(tiles))

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    return GridService


def _lazy_mosaic(
    monkeypatch: MonkeyPatch,
    *,
    rows: int,
    cols: int,
    tile_h: int = 4,
    tile_w: int = 4,
    n_bands: int = 1,
) -> Any:
    service_cls = _grid_service(rows, cols, tile_h=tile_h, tile_w=tile_w)

    def fake_get_service(*args: Any, **kwargs: Any) -> Any:
        return service_cls()

    def fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=b"\x00",
            content_type="application/octet-stream",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        h = request.height or tile_h
        w = request.width or tile_w
        # Encode row/col into pixels so assemble correctness is checkable.
        r = int(request.params.get("r", "0"))
        c = int(request.params.get("c", "0"))
        if n_bands > 1:
            out = np.zeros((h, w, n_bands), dtype=np.float32)
            out[..., 0] = float(r)
            out[..., 1] = float(c)
            return out
        return np.full((h, w), float(r * 100 + c), dtype=np.float32)

    monkeypatch.setattr(planning_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)

    bbox = BoundingBox(
        min_x=0.0,
        min_y=0.0,
        max_x=float(cols * tile_w),
        max_y=float(rows * tile_h),
        crs=CRS.EPSG_4326,
    )
    return array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        chunk_size=(tile_w, tile_h),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        # Plan-time bind so preserve-policy geotiff does not use tifffile on
        # stub bytes (registry alone is skipped when band_policy is overridden).
        tile_decoder=decoder,
        band_policy="preserve",
        show_progress=False,
        rate_limit_per_second=None,
    )


def _hlg_layer_count(data_array: Any) -> int:
    dask_arr = data_array.data
    graph = getattr(dask_arr, "dask", None)
    layers = getattr(graph, "layers", None)
    if layers is None:
        pytest.fail("expected a HighLevelGraph with .layers on the mosaic array")
    return len(layers)


def test_mosaic_graph_layer_count_stays_bounded_as_tile_count_grows(
    monkeypatch: MonkeyPatch,
) -> None:
    """HighLevelGraph layers must stay O(1) from ~1k tiles to ~10k tiles."""

    # 32×32 = 1024 tiles; 100×100 = 10_000 tiles (issue acceptance scale).
    small = _lazy_mosaic(monkeypatch, rows=32, cols=32)
    large = _lazy_mosaic(monkeypatch, rows=100, cols=100)

    n_small = _hlg_layer_count(small)
    n_large = _hlg_layer_count(large)

    assert n_small <= _MAX_HLG_LAYERS, f"32×32 mosaic had {n_small} HLG layers"
    assert n_large <= _MAX_HLG_LAYERS, f"100×100 mosaic had {n_large} HLG layers"
    # Must not grow with tile count (delayed path grew ~linearly).
    assert n_large <= n_small + 2, (
        f"layer count grew with tiles: {n_small} (1k) → {n_large} (10k)"
    )


def test_map_blocks_mosaic_pixels_match_tile_grid(monkeypatch: MonkeyPatch) -> None:
    """Assembled mosaic pixels still follow the planned north-up tile grid."""

    rows, cols = 3, 4
    tile_h = tile_w = 4
    da = _lazy_mosaic(
        monkeypatch, rows=rows, cols=cols, tile_h=tile_h, tile_w=tile_w, n_bands=1
    )
    assert _hlg_layer_count(da) <= _MAX_HLG_LAYERS

    computed = da.compute(scheduler="synchronous")
    assert computed.shape == (rows * tile_h, cols * tile_w)
    for r in range(rows):
        for c in range(cols):
            block = computed[
                r * tile_h : (r + 1) * tile_h,
                c * tile_w : (c + 1) * tile_w,
            ]
            assert float(np.mean(block)) == pytest.approx(float(r * 100 + c))
