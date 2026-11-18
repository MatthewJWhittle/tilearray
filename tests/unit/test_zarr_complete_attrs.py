"""Incomplete vs complete Zarr stores are a persist concern (#198).

Plain ``to_zarr`` writes metadata eagerly. An aborted compute can leave a
full-shape store that still opens — path presence alone is not a finish
signal. Callers are not expected to hand-stamp ``tilearray_complete*``;
a ``staging`` lifecycle (temp → atomic publish) is the planned handoff.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import xarray as xr
from _pytest.monkeypatch import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
from tilearray import NetworkError, compute_mosaic, create_array
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
    max_x: float = 10240.0,
    max_y: float = 10240.0,
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


def test_to_zarr_writes_metadata_before_compute(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Eager metadata means the store opens before any tile finishes."""
    da = _lazy_mosaic(monkeypatch, max_x=5120.0, max_y=5120.0)
    out = tmp_path / "eager.zarr"
    delayed = da.to_dataset(name="elevation").to_zarr(out, mode="w", compute=False)

    assert out.exists()
    ds = xr.open_zarr(out)
    assert "elevation" in ds
    assert ds.elevation.shape == da.shape
    # No library completion attr is required for open — and none is stamped.
    assert "tilearray_complete" not in ds.attrs
    # Keep the Delayed so the graph is not GC'd mid-assertion.
    assert delayed is not None


def test_aborted_to_zarr_leaves_openable_full_shape_store(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """Abort mid-write still leaves a full-shape store that opens cleanly."""
    calls = {"n": 0}

    def _fail_on_tenth(request: TileRequest, **kwargs: Any) -> TileResponse:
        calls["n"] += 1
        if calls["n"] >= 10:
            raise NetworkError("simulated tile abort")
        return _fake_fetch_tile(request, **kwargs)

    da = _lazy_mosaic(monkeypatch)
    monkeypatch.setattr(array_module, "fetch_tile", _fail_on_tenth)
    out = tmp_path / "partial.zarr"
    delayed = da.to_dataset(name="elevation").to_zarr(out, mode="w", compute=False)

    with pytest.raises(NetworkError, match="simulated tile abort"):
        compute_mosaic(delayed)

    assert out.exists()
    ds = xr.open_zarr(out)
    assert "elevation" in ds
    assert ds.elevation.shape == da.shape
    # Store alone has no finish bit — incomplete vs complete is not distinguishable
    # from path presence or mosaic attrs written at graph-build time.
    assert "tilearray_complete" not in ds.attrs


def test_to_zarr_group_placement_keeps_root_free_of_mosaic_vars(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    """``group=`` nests the mosaic; root group is not the array container."""
    da = _lazy_mosaic(monkeypatch, max_x=5120.0, max_y=5120.0)
    out = tmp_path / "grouped.zarr"
    delayed = da.to_dataset(name="elevation").to_zarr(
        out, mode="w", compute=False, group="mosaic"
    )
    compute_mosaic(delayed)

    ds = xr.open_zarr(out, group="mosaic")
    assert "elevation" in ds
    assert ds.elevation.shape == da.shape

    root = zarr.open_group(str(out), mode="r")
    assert "elevation" not in root
    assert "tilearray_complete" not in root.attrs
