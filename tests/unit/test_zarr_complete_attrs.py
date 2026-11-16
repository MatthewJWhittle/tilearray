"""Documented tilearray_complete attrs around plain to_zarr (#198)."""

from __future__ import annotations

from datetime import datetime, timezone
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

COMPLETE_ATTR = "tilearray_complete"
COMPLETE_AT_ATTR = "tilearray_complete_at"
COMPLETE_ERROR_ATTR = "tilearray_complete_error"


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


def _stamp_complete(
    path: Path,
    *,
    complete: bool,
    error: str | None = None,
    group: str | None = None,
) -> None:
    store_group = zarr.open_group(str(path), mode="a", path=group)
    store_group.attrs[COMPLETE_ATTR] = bool(complete)
    if complete:
        store_group.attrs[COMPLETE_AT_ATTR] = datetime.now(timezone.utc).isoformat()
        if COMPLETE_ERROR_ATTR in store_group.attrs:
            del store_group.attrs[COMPLETE_ERROR_ATTR]
    else:
        if COMPLETE_AT_ATTR in store_group.attrs:
            del store_group.attrs[COMPLETE_AT_ATTR]
        if error is not None:
            store_group.attrs[COMPLETE_ERROR_ATTR] = error
        elif COMPLETE_ERROR_ATTR in store_group.attrs:
            del store_group.attrs[COMPLETE_ERROR_ATTR]
    zarr.consolidate_metadata(str(path))


def test_plain_to_zarr_complete_flag_on_success(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    da = _lazy_mosaic(monkeypatch, max_x=5120.0, max_y=5120.0)
    out = tmp_path / "ok.zarr"
    delayed = da.to_dataset(name="elevation").to_zarr(out, mode="w", compute=False)
    _stamp_complete(out, complete=False)
    compute_mosaic(delayed)
    _stamp_complete(out, complete=True)

    ds = xr.open_zarr(out)
    assert ds.attrs.get(COMPLETE_ATTR) is True
    assert COMPLETE_AT_ATTR in ds.attrs
    assert COMPLETE_ERROR_ATTR not in ds.attrs
    assert "elevation" in ds
    assert ds.elevation.shape == da.shape


def test_plain_to_zarr_marks_incomplete_on_tile_failure(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
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
    _stamp_complete(out, complete=False)

    with pytest.raises(NetworkError, match="simulated tile abort") as exc_info:
        try:
            compute_mosaic(delayed)
        except Exception as exc:
            _stamp_complete(
                out,
                complete=False,
                error=f"{type(exc).__name__}: {exc}",
            )
            raise

    assert out.exists()
    ds = xr.open_zarr(out)
    assert ds.attrs.get(COMPLETE_ATTR) is False
    assert COMPLETE_AT_ATTR not in ds.attrs
    assert "simulated tile abort" in str(ds.attrs.get(COMPLETE_ERROR_ATTR, ""))
    assert ds.elevation.shape == da.shape
    assert exc_info.value


def test_plain_to_zarr_complete_attrs_on_group(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    da = _lazy_mosaic(monkeypatch, max_x=5120.0, max_y=5120.0)
    out = tmp_path / "grouped.zarr"
    delayed = da.to_dataset(name="elevation").to_zarr(
        out, mode="w", compute=False, group="mosaic"
    )
    _stamp_complete(out, complete=False, group="mosaic")
    compute_mosaic(delayed)
    _stamp_complete(out, complete=True, group="mosaic")

    ds = xr.open_zarr(out, group="mosaic")
    assert ds.attrs.get(COMPLETE_ATTR) is True
    assert COMPLETE_AT_ATTR in ds.attrs
    assert "elevation" in ds

    root = zarr.open_group(str(out), mode="r")
    assert COMPLETE_ATTR not in root.attrs
