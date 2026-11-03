"""Offline contract tests for AOI preflight / fetch summary (#61)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
import xarray as xr

import tilearray.array as array_module
import tilearray.plan as plan_module
from tilearray import (
    CRS,
    ArrayPlan,
    BoundingBox,
    FetchSummary,
    Format,
    XYZConfig,
    fetch_summary,
    plan_array,
    recommend_grid_shape,
)
from tilearray.array import ArrayRequest, _cache_key
from tilearray.errors import ValidationError
from tilearray.fetch import FetchPolicy, FetchProgress, TileFetcher
from tilearray.service.base import BaseService, TileGeometry
from tilearray.types import ServiceTypeEnum, TileRequest, TileResponse


def test_recommend_grid_shape_matches_array_request() -> None:
    bbox = (0.0, 0.0, 512.0, 512.0)
    chunk = (256, 256)
    resolution = (1.0, 1.0)

    grid = recommend_grid_shape(
        bbox, chunk_size=chunk, resolution=resolution, crs=CRS.EPSG_4326
    )
    assert grid == (2, 2)

    request = ArrayRequest.from_inputs(
        service_url="http://example.com/wcs",
        service_config=None,
        bbox_input=bbox,
        crs_input=CRS.EPSG_4326,
        chunk_size_input=chunk,
        grid_shape_input=None,
        output_format_input=Format.GEOTIFF,
        cache_dir_input=None,
        service_options_input={"resolution": resolution},
    )
    assert request.grid_shape == grid


def test_recommend_grid_shape_snaps_partial_pixels() -> None:
    bbox = BoundingBox(
        min_x=0.0, min_y=0.0, max_x=1024.1, max_y=512.09, crs=CRS.EPSG_27700
    )
    grid = recommend_grid_shape(bbox, chunk_size=(1024, 512), resolution=(1.0, 1.0))
    assert grid == (2, 2)


def test_recommend_grid_shape_rejects_bad_inputs() -> None:
    with pytest.raises(ValidationError, match="resolution"):
        recommend_grid_shape(
            (0.0, 0.0, 10.0, 10.0),
            chunk_size=(8, 8),
            resolution=(0.0, 1.0),
            crs=CRS.EPSG_4326,
        )
    with pytest.raises(ValidationError, match="crs is required"):
        recommend_grid_shape(
            (0.0, 0.0, 10.0, 10.0),
            chunk_size=(8, 8),
            resolution=(1.0, 1.0),
        )


class _SizedWCS(BaseService):
    """Fake WCS-like planner that records tile pixel sizes."""

    service_type = ServiceTypeEnum.WCS
    output_format = Format.GEOTIFF

    def __init__(self, max_pixels: int) -> None:
        super().__init__("http://example.com/wcs")
        self.max_pixels = max_pixels
        self.seen_sizes: list[tuple[int, int]] = []

    def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
        width = tile.width
        height = tile.height
        self.seen_sizes.append((width, height))
        if width * height > self.max_pixels:
            raise AssertionError(
                f"planned tile {width}×{height} exceeds fake server cap "
                f"{self.max_pixels}"
            )
        return TileRequest(
            url="http://example.com/wcs",
            params={"WIDTH": str(width), "HEIGHT": str(height)},
            output_format=Format.GEOTIFF,
            crs=tile.crs,
            bbox=tile.bbox,
            width=width,
            height=height,
        )


def test_plan_array_large_aoi_respects_chunk_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Synthetic large AOI: every planned request stays within chunk_size."""

    max_chunk = (256, 256)
    service = _SizedWCS(max_pixels=max_chunk[0] * max_chunk[1])

    def fake_get_service(*args: Any, **kwargs: Any) -> _SizedWCS:
        return service

    monkeypatch.setattr(array_module, "get_service", fake_get_service)

    bbox = BoundingBox(
        min_x=0.0, min_y=0.0, max_x=2000.0, max_y=1500.0, crs=CRS.EPSG_27700
    )
    plan = plan_array(
        "http://example.com/wcs",
        bbox,
        CRS.EPSG_27700,
        service_type=ServiceTypeEnum.WCS,
        chunk_size=max_chunk,
        output_format=Format.GEOTIFF,
        resolution=(1.0, 1.0),
    )

    assert isinstance(plan, ArrayPlan)
    assert plan.request_count == plan.grid_shape[0] * plan.grid_shape[1]
    assert plan.request_count >= 8
    assert plan.pixel_height == 1500
    assert plan.pixel_width == 2000
    assert plan.bands == 1
    assert plan.bands_source == "format_policy"
    assert plan.estimated_nbytes == 1500 * 2000 * 1 * 4
    assert plan.fetch_policy["max_concurrent"] >= 1
    assert all(w * h <= max_chunk[0] * max_chunk[1] for w, h in service.seen_sizes)
    assert all(w <= max_chunk[0] and h <= max_chunk[1] for w, h in service.seen_sizes)
    text = plan.summary()
    assert "preflight" in text
    assert "2000" in text
    assert "unknowns" not in text or "band" not in text.split("unknowns")[-1]


def test_plan_array_edge_leftover_tiles(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _SizedWCS(max_pixels=100 * 100)

    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: service)

    bbox = BoundingBox(
        min_x=0.0, min_y=0.0, max_x=250.0, max_y=180.0, crs=CRS.EPSG_27700
    )
    plan = plan_array(
        "http://example.com/wcs",
        bbox,
        CRS.EPSG_27700,
        service_type=ServiceTypeEnum.WCS,
        chunk_size=(100, 100),
        output_format=Format.GEOTIFF,
        resolution=(1.0, 1.0),
    )

    assert plan.grid_shape == (2, 3)
    assert plan.request_count == 6
    assert plan.pixel_width == 250
    assert plan.pixel_height == 180
    widths = sorted({w for w, _ in service.seen_sizes})
    heights = sorted({h for _, h in service.seen_sizes})
    assert 50 in widths  # leftover column
    assert 80 in heights  # leftover row
    assert max(widths) == 100
    assert max(heights) == 100


def test_plan_array_jpeg_bands_unknown_without_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class JpegService(BaseService):
        service_type = ServiceTypeEnum.WMS
        output_format = Format.JPEG

        def __init__(self) -> None:
            super().__init__("http://example.com/wms")

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return TileRequest(
                url="http://example.com/wms",
                params={},
                output_format=Format.JPEG,
                crs=tile.crs,
                bbox=tile.bbox,
                width=tile.width,
                height=tile.height,
            )

    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: JpegService())

    plan = plan_array(
        "http://example.com/wms",
        (0.0, 0.0, 10.0, 10.0),
        CRS.EPSG_4326,
        service_type=ServiceTypeEnum.WMS,
        chunk_size=(256, 256),
        grid_shape=(2, 2),
        output_format=Format.JPEG,
    )
    assert plan.bands is None
    assert plan.bands_source == "unknown"
    assert plan.estimated_nbytes is None
    assert any("band count" in item for item in plan.unknowns)
    assert "estimated array bytes: unknown" in plan.summary()


def test_plan_array_cache_warm_cold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _SizedWCS(max_pixels=256 * 256)
    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: service)

    bbox = BoundingBox(
        min_x=0.0, min_y=0.0, max_x=512.0, max_y=256.0, crs=CRS.EPSG_27700
    )
    plan = plan_array(
        "http://example.com/wcs",
        bbox,
        CRS.EPSG_27700,
        service_type=ServiceTypeEnum.WCS,
        chunk_size=(256, 256),
        output_format=Format.GEOTIFF,
        resolution=(1.0, 1.0),
        cache_dir=tmp_path,
    )
    assert plan.cached_tiles == 0
    assert plan.request_count == 2

    first = plan.tile_requests[0]
    key = _cache_key(first)
    (tmp_path / f"{key}.tile").write_bytes(b"fake")

    plan2 = plan_array(
        "http://example.com/wcs",
        bbox,
        CRS.EPSG_27700,
        service_type=ServiceTypeEnum.WCS,
        chunk_size=(256, 256),
        output_format=Format.GEOTIFF,
        resolution=(1.0, 1.0),
        cache_dir=tmp_path,
    )
    assert plan2.cached_tiles == 1
    assert "1 warm / 1 cold" in plan2.summary()


def test_plan_array_xyz_notes_native_zoom(tmp_path: Path) -> None:
    config = XYZConfig.from_url(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        zoom=10,
        output_format=Format.PNG,
        chunk_size=(256, 256),
        cache_dir=tmp_path,
        max_concurrent_requests=2,
    )
    bbox = BoundingBox(min_x=-0.2, min_y=51.4, max_x=0.2, max_y=51.6, crs=CRS.EPSG_4326)
    plan = plan_array(config, bbox, CRS.EPSG_4326)
    assert plan.request_count > 1
    assert plan.service_type == ServiceTypeEnum.XYZ.value
    assert any("native zoom" in note for note in plan.notes)
    assert plan.fetch_policy["max_concurrent"] == 2
    assert plan.bands is None  # PNG


def test_fetch_summary_from_progress_and_stats() -> None:
    TileFetcher.reset_instances()
    policy = FetchPolicy(max_concurrent=3, adaptive_concurrency=False)
    fetcher = TileFetcher.for_policy(policy)
    fetcher.stats._record_request()
    fetcher.stats._enter_inflight()
    fetcher.stats._leave_inflight()

    progress = FetchProgress(total=4)
    progress.done = 2
    progress.record_soft_fail(
        TileRequest(url="http://example.com/1", params={}, crs=CRS.EPSG_4326)
    )

    da = xr.DataArray(
        np.zeros((2, 2), dtype=np.float32),
        dims=("y", "x"),
        attrs={
            "tilearray_fetch_policy": {
                "max_concurrent": 3,
                "max_connections": None,
                "retries": 3,
                "timeout": 30.0,
                "rate_limit_per_second": 2.0,
                "adaptive_concurrency": False,
                "initial_concurrent": 2,
                "min_concurrent": 1,
                "multiplicative_decrease": 0.5,
                "additive_increase": 2,
                "forbidden_circuit_breaker": False,
                "forbidden_window_seconds": 5.0,
                "forbidden_threshold": 6,
                "forbidden_cooldown_seconds": 15.0,
                "call_max_concurrent": None,
            },
            "_tilearray_fetch_progress": progress,
            "tilearray_tile_errors": {
                "soft_failed": 1,
                "aborted": False,
                "examples": [],
            },
        },
    )

    summary = fetch_summary(da)
    assert isinstance(summary, FetchSummary)
    assert summary.total == 4
    assert summary.done == 2
    assert summary.soft_failed == 1
    assert summary.request_count == 1
    assert summary.max_inflight == 1
    text = summary.summary()
    assert "2/4" in text
    assert "soft-failed: 1" in text

    TileFetcher.reset_instances()


def test_fetch_summary_honest_unknowns_without_progress() -> None:
    da = xr.DataArray(np.zeros((1, 1)), dims=("y", "x"), attrs={})
    summary = fetch_summary(da)
    assert summary.total is None
    assert summary.done is None
    assert any("FetchProgress" in item for item in summary.unknowns)
    assert "unknown" in summary.summary().lower()


def test_plan_array_does_not_fetch_pixels(monkeypatch: pytest.MonkeyPatch) -> None:
    service = _SizedWCS(max_pixels=256 * 256)
    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: service)

    def boom(*args: Any, **kwargs: Any) -> TileResponse:
        raise AssertionError("plan_array must not fetch mosaic tiles by default")

    monkeypatch.setattr(array_module, "fetch_tile", boom)
    monkeypatch.setattr(plan_module, "_probe_tile_band_count", boom)

    plan_array(
        "http://example.com/wcs",
        BoundingBox(min_x=0, min_y=0, max_x=100, max_y=100, crs=CRS.EPSG_27700),
        CRS.EPSG_27700,
        service_type=ServiceTypeEnum.WCS,
        chunk_size=(50, 50),
        output_format=Format.GEOTIFF,
        resolution=(1.0, 1.0),
    )
