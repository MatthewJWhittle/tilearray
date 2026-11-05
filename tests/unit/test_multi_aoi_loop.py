"""Offline multi-AOI / ML-patch loop contracts (#87).

Documents the near-term recipe: one bbox → one mosaic, sequential or
overlapping compute under the shared host gate — no union megamosaic, no
orchestration helper.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import respx
from PIL import Image
from pytest import MonkeyPatch

from tilearray import WMTSConfig, compute_mosaic, create_array
from tilearray.array import _fetch_policy_from_summary
from tilearray.fetch import TileFetcher
from tilearray.service.xyz import _tile_bounds
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = pytest.mark.unit

WMTS_URL = "https://example.com/wmts"
HOST_CEILING = 2
ZOOM = 12


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


@pytest.fixture
def png_tile_bytes() -> bytes:
    array = np.full((256, 256), 42, dtype=np.uint8)
    with BytesIO() as buffer:
        Image.fromarray(array, mode="L").save(buffer, format="PNG")
        return buffer.getvalue()


def _inset_tile_bbox(tile_x: int, tile_y: int, zoom: int = ZOOM) -> BoundingBox:
    """Single-tile AOI inset from slippy bounds (noncontiguous when tiles differ)."""

    outer = _tile_bounds(tile_x, tile_y, zoom, CRS.EPSG_4326)
    margin_x = (outer.max_x - outer.min_x) * 0.25
    margin_y = (outer.max_y - outer.min_y) * 0.25
    return BoundingBox(
        min_x=outer.min_x + margin_x,
        min_y=outer.min_y + margin_y,
        max_x=outer.max_x - margin_x,
        max_y=outer.max_y - margin_y,
        crs=CRS.EPSG_4326,
    )


def _wmts_config(**overrides: Any) -> WMTSConfig:
    defaults: dict[str, Any] = {
        "layer": "example_layer",
        "tile_matrix_set": "EPSG4326",
        "tile_matrix": ZOOM,
        "output_format": Format.PNG,
        "chunk_size": (256, 256),
        "max_concurrent_requests": HOST_CEILING,
        "rate_limit_per_second": None,
        "adaptive_concurrency": False,
        "fetch_retries": 0,
    }
    defaults.update(overrides)
    return WMTSConfig.from_url(WMTS_URL, **defaults)


def _mock_fetch_tile(tile_bytes: bytes):
    def fetch_tile_impl(request: TileRequest, **kwargs: object) -> TileResponse:
        return TileResponse(
            data=tile_bytes,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    return fetch_tile_impl


def test_multi_aoi_loop_noncontiguous_wmts_shapes_and_provenance(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
) -> None:
    """Separated WMTS bboxes → independent mosaics with stable CRS/shape/attrs."""

    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    # Far-apart Web Mercator tiles — not a contiguous UK megabbox.
    patch_tiles = ((2048, 1361), (2040, 1300), (2100, 1200))
    boxes = [_inset_tile_bbox(tx, ty) for tx, ty in patch_tiles]
    config = _wmts_config()

    lazy = [
        create_array(config, bbox, CRS.EPSG_4326, show_progress=False) for bbox in boxes
    ]
    assert len(lazy) == 3

    patches = [compute_mosaic(da) for da in lazy]

    for arr in patches:
        assert arr.shape == (256, 256)
        assert arr.attrs["crs"] == CRS.EPSG_3857.value
        assert arr.attrs["transform_source"] == "mosaic_bbox"
        assert arr.attrs["service_type"] == ServiceTypeEnum.WMTS.value
        assert "nodata" not in arr.attrs  # rendered PNG imagery
        assert arr.attrs["bbox_requested"]["crs"] == CRS.EPSG_4326.value
        a, _b, c, _d, e, f = arr.attrs["transform"]
        assert arr.coords["x"].values[0] == pytest.approx(c + a * 0.5)
        assert arr.coords["y"].values[0] == pytest.approx(f + e * 0.5)
        assert float(arr.mean()) == pytest.approx(42.0, rel=1e-3)

    # Distinct sites → distinct mosaic origins (not one shared grid).
    origins = {
        (arr.attrs["transform"][2], arr.attrs["transform"][5]) for arr in patches
    }
    assert len(origins) == 3


def test_union_bbox_plans_more_tiles_than_separate_patches(
    png_tile_bytes: bytes,
    monkeypatch: MonkeyPatch,
) -> None:
    """Union of distant boxes is the wrong ML model — plans an empty-heavy megagrid."""

    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    boxes = [
        _inset_tile_bbox(2048, 1361),
        _inset_tile_bbox(2100, 1200),
    ]
    config = _wmts_config()

    # Single-tile insets → one partition each; union spans many matrices.
    separate_partitions = sum(
        create_array(config, bbox, CRS.EPSG_4326, show_progress=False).data.npartitions
        for bbox in boxes
    )
    union = BoundingBox(
        min_x=min(b.min_x for b in boxes),
        min_y=min(b.min_y for b in boxes),
        max_x=max(b.max_x for b in boxes),
        max_y=max(b.max_y for b in boxes),
        crs=CRS.EPSG_4326,
    )
    union_da = create_array(config, union, CRS.EPSG_4326, show_progress=False)

    assert separate_partitions == 2
    assert union_da.data.npartitions > separate_partitions


@respx.mock
def test_overlapping_multi_aoi_computes_respect_shared_host_ceiling(
    png_tile_bytes: bytes,
) -> None:
    """Several queued mosaics contend on one gate — never exceed host ceiling."""

    latency_s = 0.05
    n_patches = 4
    inflight = 0
    peak = 0
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal inflight, peak
        with lock:
            inflight += 1
            peak = max(peak, inflight)
        time.sleep(latency_s)
        with lock:
            inflight -= 1
        return httpx.Response(
            200,
            content=png_tile_bytes,
            headers={"content-type": "image/png"},
        )

    respx.get(WMTS_URL).mock(side_effect=handler)

    # Distinct tiles so each mosaic issues its own GetTile(s).
    patch_tiles = ((2048, 1361), (2049, 1361), (2050, 1361), (2051, 1361))
    config = _wmts_config()
    lazy = [
        create_array(
            config,
            _inset_tile_bbox(tx, ty),
            CRS.EPSG_4326,
            show_progress=False,
        )
        for tx, ty in patch_tiles[:n_patches]
    ]

    # Same shared gate the mosaics use (policy stamped at create_array).
    shared = _fetch_policy_from_summary(lazy[0].attrs["tilearray_fetch_policy"])
    assert shared.max_concurrent == HOST_CEILING
    fetcher = TileFetcher.for_policy(shared)

    with ThreadPoolExecutor(max_workers=n_patches) as pool:
        futures = [pool.submit(compute_mosaic, da) for da in lazy]
        results = [future.result() for future in futures]

    assert len(results) == n_patches
    assert all(arr.shape == (256, 256) for arr in results)
    assert peak <= HOST_CEILING
    assert fetcher.stats.max_inflight <= HOST_CEILING
    assert peak >= 1


@respx.mock
def test_shared_cache_dir_reuses_overlapping_tile_bytes(
    png_tile_bytes: bytes,
    tmp_path: Path,
) -> None:
    """cache_dir on the shared config skips HTTP on a second pass of the same tile."""

    hits = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal hits
        hits += 1
        return httpx.Response(
            200,
            content=png_tile_bytes,
            headers={"content-type": "image/png"},
        )

    respx.get(WMTS_URL).mock(side_effect=handler)

    bbox = _inset_tile_bbox(2048, 1361)
    config = _wmts_config(cache_dir=tmp_path)

    first = compute_mosaic(
        create_array(config, bbox, CRS.EPSG_4326, show_progress=False)
    )
    cold_hits = hits
    second = compute_mosaic(
        create_array(config, bbox, CRS.EPSG_4326, show_progress=False)
    )

    assert cold_hits >= 1
    assert hits == cold_hits  # warm cache: no extra HTTP
    assert first.shape == second.shape == (256, 256)
    np.testing.assert_allclose(first.values, second.values)
