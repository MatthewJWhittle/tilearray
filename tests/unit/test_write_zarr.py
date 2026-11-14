"""write_zarr marks stores complete only after a successful mosaic write."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import xarray as xr
from _pytest.monkeypatch import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
from tilearray import NetworkError, write_zarr
from tilearray.array import create_array
from tilearray.errors import ValidationError
from tilearray.service.base import BaseService, TileGeometry
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)
from tilearray.zarr_write import (
    COMPLETE_AT_ATTR,
    COMPLETE_ATTR,
    COMPLETE_ERROR_ATTR,
    TILE_COUNT_ATTR,
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


def test_write_zarr_sets_complete_flag_on_success(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    da = _lazy_mosaic(monkeypatch)
    out = tmp_path / "ok.zarr"
    write_zarr(da, out, name="elevation")

    ds = xr.open_zarr(out)
    assert ds.attrs.get(COMPLETE_ATTR) is True
    assert COMPLETE_AT_ATTR in ds.attrs
    assert COMPLETE_ERROR_ATTR not in ds.attrs
    assert ds.attrs.get(TILE_COUNT_ATTR) == 16
    assert "elevation" in ds
    assert ds.elevation.shape == da.shape


def test_write_zarr_marks_incomplete_and_reraises_on_tile_failure(
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

    with pytest.raises(NetworkError, match="simulated tile abort"):
        write_zarr(da, out, name="elevation")

    assert out.exists()
    ds = xr.open_zarr(out)
    assert ds.attrs.get(COMPLETE_ATTR) is False
    assert COMPLETE_AT_ATTR not in ds.attrs
    assert "simulated tile abort" in str(ds.attrs.get(COMPLETE_ERROR_ATTR, ""))
    assert ds.elevation.shape == da.shape


def test_write_zarr_complete_store_round_trips_values(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    da = _lazy_mosaic(monkeypatch, max_x=5120.0, max_y=5120.0)
    expected = da.compute()
    out = tmp_path / "roundtrip.zarr"
    write_zarr(da, out, name="elevation")

    loaded = xr.open_zarr(out)
    assert loaded.attrs.get(COMPLETE_ATTR) is True
    np.testing.assert_array_equal(loaded.elevation.values, expected.values)


def test_write_zarr_rejects_compute_kwarg(
    monkeypatch: MonkeyPatch, tmp_path: Path
) -> None:
    da = _lazy_mosaic(monkeypatch, max_x=2560.0, max_y=2560.0)
    with pytest.raises(ValidationError, match="compute=False"):
        write_zarr(da, tmp_path / "x.zarr", name="elevation", compute=True)
