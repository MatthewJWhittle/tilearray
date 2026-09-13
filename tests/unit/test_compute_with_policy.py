"""Unit tests for compute_with_policy (no network)."""

from __future__ import annotations

import os
from typing import Any

import numpy as np
import pytest
import xarray as xr
from pytest import MonkeyPatch

import tilearray.array as array_module
import tilearray.decode as decode_module
from tilearray.array import (
    _FETCH_POLICY_ATTR,
    _RECOMMENDED_NUM_WORKERS_ATTR,
    compute_thread_pool_size,
    compute_with_policy,
)
from tilearray.fetch import FetchPolicy
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)


@pytest.fixture(autouse=True)
def preserve_decoder_registry() -> Any:
    original = dict(decode_module._DECODER_REGISTRY)
    try:
        yield
    finally:
        decode_module._DECODER_REGISTRY = original


class DummyService:
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
                url="http://example.com/wcs/0/0",
                params={},
                output_format=Format.GEOTIFF,
                crs=bbox.crs,
                bbox=bbox,
                width=width,
                height=height,
            )
        ]


def _lazy_array(monkeypatch: MonkeyPatch) -> Any:
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

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        height = request.height or 1
        width = request.width or 1
        return np.ones((height, width), dtype=np.float32)

    monkeypatch.setattr(array_module, "get_service", fake_get_service)
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch_tile)
    decode_module.register_tile_decoder(Format.GEOTIFF, decoder)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)
    return array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        crs=CRS.EPSG_4326,
        chunk_size=(2, 2),
        output_format=Format.GEOTIFF,
        max_concurrent_requests=32,
        rate_limit_per_second=None,
    )


def test_create_array_stashes_fetch_policy_attrs(monkeypatch: MonkeyPatch) -> None:
    da = _lazy_array(monkeypatch)

    assert _FETCH_POLICY_ATTR in da.attrs
    assert da.attrs[_FETCH_POLICY_ATTR]["max_concurrent"] == 32
    assert da.attrs[_RECOMMENDED_NUM_WORKERS_ATTR] == compute_thread_pool_size(
        FetchPolicy(max_concurrent=32)
    )


def _patch_dataarray_compute(
    monkeypatch: MonkeyPatch,
) -> dict[str, Any]:
    recorded: dict[str, Any] = {}

    def fake_compute(self: xr.DataArray, **kwargs: Any) -> xr.DataArray:
        recorded.update(kwargs)
        return xr.DataArray(
            np.ones(self.shape, dtype=np.float32),
            coords=self.coords,
            dims=self.dims,
            attrs=self.attrs,
        )

    monkeypatch.setattr(xr.DataArray, "compute", fake_compute)
    return recorded


def test_compute_with_policy_applies_thread_scheduler_and_workers(
    monkeypatch: MonkeyPatch,
) -> None:
    da = _lazy_array(monkeypatch)
    recorded = _patch_dataarray_compute(monkeypatch)

    compute_with_policy(da)

    assert recorded["scheduler"] == "threads"
    assert recorded["num_workers"] == da.attrs[_RECOMMENDED_NUM_WORKERS_ATTR]
    assert recorded["num_workers"] >= 32


def test_compute_with_policy_explicit_policy_overrides_attrs(
    monkeypatch: MonkeyPatch,
) -> None:
    da = _lazy_array(monkeypatch)
    recorded = _patch_dataarray_compute(monkeypatch)

    policy = FetchPolicy(max_concurrent=16)
    compute_with_policy(da, fetch_policy=policy, num_workers=12)

    assert recorded["num_workers"] == 12
    assert recorded["scheduler"] == "threads"


def test_compute_with_policy_max_concurrent_shorthand(
    monkeypatch: MonkeyPatch,
) -> None:
    da = _lazy_array(monkeypatch)
    recorded = _patch_dataarray_compute(monkeypatch)

    compute_with_policy(da, max_concurrent=24)

    expected = compute_thread_pool_size(FetchPolicy(max_concurrent=24))
    assert recorded["num_workers"] == expected


def test_compute_with_policy_merges_user_compute_kwargs(
    monkeypatch: MonkeyPatch,
) -> None:
    da = _lazy_array(monkeypatch)
    recorded = _patch_dataarray_compute(monkeypatch)

    compute_with_policy(da, optimize_graph=False)

    assert recorded["optimize_graph"] is False
    assert recorded["scheduler"] == "threads"


def test_compute_with_policy_default_policy_when_no_attrs(
    monkeypatch: MonkeyPatch,
) -> None:
    import dask.array as da_lib

    lazy = da_lib.ones((4, 4), chunks=(2, 2))
    recorded: dict[str, Any] = {}

    def fake_compute(**kwargs: Any) -> np.ndarray:
        recorded.update(kwargs)
        return np.ones((4, 4), dtype=np.float32)

    monkeypatch.setattr(lazy, "compute", fake_compute)

    compute_with_policy(lazy)

    default_policy = FetchPolicy()
    assert recorded["num_workers"] == compute_thread_pool_size(default_policy)
    assert recorded["num_workers"] >= (os.cpu_count() or 1)
