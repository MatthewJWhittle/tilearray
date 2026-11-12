"""Lazy mosaics must be deepcopy/pickle/Zarr-safe (no live progress on attrs)."""

from __future__ import annotations

import copy
import pickle
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from _pytest.monkeypatch import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
from tilearray.array import _PROGRESS_REF_ATTR, create_array
from tilearray.fetch import (
    FetchProgress,
    lookup_fetch_progress,
    register_fetch_progress,
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

pytestmark = pytest.mark.unit

zarr = pytest.importorskip("zarr")


def _zeros_decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    """Module-level decoder so lazy graphs stay pickleable in serialisation tests."""
    height = request.height or 1
    width = request.width or 1
    return np.zeros((height, width), dtype=np.float32)


def _fake_fetch_tile(request: TileRequest, **kwargs: Any) -> TileResponse:
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


class _GridService(BaseService):
    service_type = ServiceTypeEnum.WCS
    output_format = Format.GEOTIFF

    def __init__(self) -> None:
        super().__init__("http://example.com/wcs")

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


def _patch_resolution_mosaic(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: _GridService())
    monkeypatch.setattr(array_module, "fetch_tile", _fake_fetch_tile)


def _lazy_mosaic(
    monkeypatch: MonkeyPatch,
    *,
    max_x: float,
    max_y: float,
    chunk: int = 256,
    resolution: float = 10.0,
) -> Any:
    bbox = BoundingBox(
        min_x=0.0,
        min_y=0.0,
        max_x=max_x,
        max_y=max_y,
        crs=CRS.EPSG_3857,
    )
    _patch_resolution_mosaic(monkeypatch)
    return create_array(
        "http://example.com/wcs",
        bbox,
        CRS.EPSG_3857,
        service_type=ServiceTypeEnum.WCS,
        chunk_size=(chunk, chunk),
        output_format=Format.GEOTIFF,
        resolution=(resolution, resolution),
        tile_decoder=_zeros_decoder,
        compute=False,
        show_progress=False,
    )


def _chunks_zarr_legal(chunks: tuple[tuple[int, ...], ...]) -> None:
    for axis_chunks in chunks:
        if len(axis_chunks) <= 1:
            continue
        regular = axis_chunks[0]
        assert all(size == regular for size in axis_chunks[:-1])
        assert axis_chunks[-1] <= regular


def test_fetch_progress_pickle_round_trip_drops_lock() -> None:
    progress = FetchProgress(total=3, call_max_concurrent=2)
    progress.tick  # noqa: B018 — attribute exists
    with progress._lock:
        progress.done = 1
    blob = pickle.dumps(progress)
    restored = pickle.loads(blob)
    assert restored.total == 3
    assert restored.done == 1
    assert isinstance(restored._lock, type(threading.Lock()))
    assert restored.on_progress is None


def test_lazy_mosaic_attrs_hold_progress_token_not_live_session(
    monkeypatch: MonkeyPatch,
) -> None:
    da = _lazy_mosaic(monkeypatch, max_x=5120.0, max_y=5120.0)
    ref = da.attrs.get(_PROGRESS_REF_ATTR)
    assert isinstance(ref, str)
    assert not isinstance(ref, FetchProgress)
    assert lookup_fetch_progress(ref) is not None
    assert all(
        not isinstance(
            value, (FetchProgress, type(threading.Lock()), threading.Semaphore)
        )
        for value in da.attrs.values()
    )


def test_lazy_mosaic_survives_deepcopy_and_pickle(monkeypatch: MonkeyPatch) -> None:
    da = _lazy_mosaic(monkeypatch, max_x=5120.0, max_y=5120.0)
    cloned = copy.deepcopy(da)
    assert cloned.shape == da.shape
    blob = pickle.dumps(da)
    restored = pickle.loads(blob)
    assert restored.shape == da.shape


def test_lazy_mosaic_to_zarr_with_uniform_trailing_remainder(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """5130 m / 10 m → 513 px; short leftover must be the final chunk only."""
    da = _lazy_mosaic(monkeypatch, max_x=5130.0, max_y=5130.0)
    assert da.shape == (513, 513)
    _chunks_zarr_legal(da.data.chunks)
    assert da.data.chunks[0][-1] == 1
    assert da.data.chunks[0][0] == 256

    out = tmp_path / "elevation.zarr"
    da.to_dataset(name="elevation").to_zarr(out)
    loaded = zarr.open_group(str(out), mode="r")
    assert "elevation" in loaded
    assert loaded["elevation"].shape == (513, 513)


def test_lazy_mosaic_remainder_like_large_grid_is_final_chunk(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Analog of EA 1024-grid remainders: short size is last, never first."""
    # 2*1024 + 867 = 2915 px at 1 m
    da = _lazy_mosaic(
        monkeypatch, max_x=2915.0, max_y=2915.0, chunk=1024, resolution=1.0
    )
    assert da.shape == (2915, 2915)
    assert da.data.chunks[0] == (1024, 1024, 867)
    assert da.data.chunks[1] == (1024, 1024, 867)
    _chunks_zarr_legal(da.data.chunks)

    out = tmp_path / "big_slice.zarr"
    da.to_dataset(name="elevation").to_zarr(out)
    assert zarr.open_array(str(out / "elevation"), mode="r").shape == (2915, 2915)


def test_compute_mosaic_clears_progress_token(monkeypatch: MonkeyPatch) -> None:
    from tilearray import compute_mosaic

    da = _lazy_mosaic(monkeypatch, max_x=2560.0, max_y=2560.0)
    token = da.attrs.get(_PROGRESS_REF_ATTR)
    assert isinstance(token, str)
    assert lookup_fetch_progress(token) is not None
    result = compute_mosaic(da)
    assert _PROGRESS_REF_ATTR not in result.attrs
    # Lazy source may keep the token for fetch_summary; result must not.
    assert all(not isinstance(v, FetchProgress) for v in result.attrs.values())


def test_register_lookup_round_trip() -> None:
    progress = FetchProgress(total=1)
    token = register_fetch_progress(progress)
    assert lookup_fetch_progress(token) is progress
