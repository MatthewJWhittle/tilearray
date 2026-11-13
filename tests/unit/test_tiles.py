"""Unit tests for tile grid utilities."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx
import pytest
import respx

from tilearray.errors import ValidationError
from tilearray.fetch import FetchPolicy, TileFetcher
from tilearray.tiles import (
    create_tile_grid,
    fetch_tile,
    resolution_axis_tile_sizes,
    save_tile,
    snap_bbox_outward,
)
from tilearray.types import CRS, BoundingBox, Format, TileRequest, TileResponse

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _bbox(min_x: float, min_y: float, max_x: float, max_y: float) -> BoundingBox:
    return BoundingBox(
        min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y, crs=CRS.EPSG_4326
    )


def test_snap_bbox_outward_preserves_rounded_pixel_count() -> None:
    """Fractional edges align min down; span uses round(span/res), not pixel-union."""
    bbox = _bbox(0.1, 0.1, 1024.1, 512.25)
    snapped = snap_bbox_outward(bbox, resolution=(1.0, 1.0), origin=(0.0, 0.0))

    assert snapped.min_x == 0.0
    assert snapped.min_y == 0.0
    assert snapped.max_x == 1024.0
    assert snapped.max_y == 512.0


def test_snap_bbox_outward_leaves_aligned_bbox_unchanged() -> None:
    bbox = _bbox(0, 0, 1024, 1024)
    snapped = snap_bbox_outward(bbox, resolution=(1.0, 1.0), origin=(0.0, 0.0))

    assert snapped.min_x == bbox.min_x
    assert snapped.min_y == bbox.min_y
    assert snapped.max_x == bbox.max_x
    assert snapped.max_y == bbox.max_y


def test_snap_bbox_outward_rejects_non_positive_resolution() -> None:
    bbox = _bbox(0, 0, 10, 10)
    with pytest.raises(ValidationError, match="resolution"):
        snap_bbox_outward(bbox, resolution=(0.0, 1.0), origin=(0.0, 0.0))


def test_snap_bbox_outward_keeps_integer_span_when_unaligned() -> None:
    """Half-pixel offset must not inflate an exact N-pixel ground span to N+1."""
    bbox = BoundingBox(
        min_x=-10297015.0,
        min_y=4364542.0,
        max_x=-10291895.0,
        max_y=4369662.0,
        crs=CRS.EPSG_3857,
    )
    resolution = (10.0, 10.0)
    assert (bbox.max_x - bbox.min_x) / resolution[0] == 512.0
    assert (bbox.max_y - bbox.min_y) / resolution[1] == 512.0

    snapped = snap_bbox_outward(bbox, resolution=resolution, origin=(0.0, 0.0))

    assert (snapped.max_x - snapped.min_x) / resolution[0] == 512.0
    assert (snapped.max_y - snapped.min_y) / resolution[1] == 512.0
    assert snapped.min_x == -10297020.0
    assert snapped.min_y == 4364540.0
    assert snapped.max_x == -10291900.0
    assert snapped.max_y == 4369660.0


def test_resolution_axis_tile_sizes_keeps_remainder_final() -> None:
    assert resolution_axis_tile_sizes(512, 256) == [256, 256]
    assert resolution_axis_tile_sizes(513, 256) == [256, 256, 1]
    assert resolution_axis_tile_sizes(100, 256) == [100]
    assert resolution_axis_tile_sizes(1024, 1024) == [1024]


def test_create_tile_grid_aligned_bbox() -> None:
    bbox = _bbox(0, 0, 100, 100)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(0, 0), resolution=(1.0, 1.0)
    )

    assert grid.shape == (10, 10, 4)
    assert grid[0, 0].tolist() == [0.0, 0.0, 10.0, 10.0]
    assert grid[-1, -1].tolist() == [90.0, 90.0, 100.0, 100.0]


def test_create_tile_grid_non_aligned_bbox_extends_to_cover() -> None:
    bbox = _bbox(12, 8, 88, 92)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(0, 0), resolution=(1.0, 1.0)
    )

    assert grid.shape == (10, 8, 4)
    assert grid[0, 0, 0] <= bbox.min_x
    assert grid[0, 0, 1] <= bbox.min_y
    assert grid[-1, -1, 2] >= bbox.max_x
    assert grid[-1, -1, 3] >= bbox.max_y


def test_create_tile_grid_partial_tile_single_cell() -> None:
    bbox = _bbox(22, 22, 28, 28)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(0, 0), resolution=(1.0, 1.0)
    )

    assert grid.shape == (1, 1, 4)
    assert grid[0, 0].tolist() == [20.0, 20.0, 30.0, 30.0]


def test_create_tile_grid_spans_multiple_partial_tiles() -> None:
    bbox = _bbox(25, 25, 35, 35)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(0, 0), resolution=(1.0, 1.0)
    )

    assert grid.shape == (2, 2, 4)
    assert grid[0, 0].tolist() == [20.0, 20.0, 30.0, 30.0]
    assert grid[1, 1].tolist() == [30.0, 30.0, 40.0, 40.0]


def test_create_tile_grid_respects_origin_offset() -> None:
    bbox = _bbox(105, 205, 125, 225)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(100, 200), resolution=(1.0, 1.0)
    )

    assert grid.shape == (3, 3, 4)
    assert grid[0, 0].tolist() == [100.0, 200.0, 110.0, 210.0]
    assert grid[2, 2].tolist() == [120.0, 220.0, 130.0, 230.0]


def test_create_tile_grid_uses_anisotropic_resolution() -> None:
    bbox = _bbox(0, 0, 20, 10)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(0, 0), resolution=(2.0, 1.0)
    )

    assert grid.shape == (1, 1, 4)
    assert grid[0, 0].tolist() == [0.0, 0.0, 20.0, 10.0]


def test_create_tile_grid_rejects_invalid_resolution() -> None:
    bbox = _bbox(0, 0, 10, 10)

    with pytest.raises(ValidationError, match="resolution"):
        create_tile_grid(bbox, tile_size=(10, 10), origin=(0, 0), resolution=(0.0, 1.0))


def test_create_tile_grid_rejects_invalid_tile_size() -> None:
    bbox = _bbox(0, 0, 10, 10)

    with pytest.raises(ValidationError, match="tile_size"):
        create_tile_grid(bbox, tile_size=(0, 10), origin=(0, 0), resolution=(1.0, 1.0))


def test_create_tile_grid_handles_negative_coordinates() -> None:
    bbox = _bbox(-45, -35, -5, -15)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(-50, -50), resolution=(1.0, 1.0)
    )

    assert grid.shape == (3, 5, 4)
    assert grid[0, 0, 0] <= bbox.min_x
    assert grid[-1, -1, 2] >= bbox.max_x


def test_create_tile_grid_boundary_on_grid_line() -> None:
    bbox = _bbox(50, 50, 100, 100)
    grid = create_tile_grid(
        bbox, tile_size=(10, 10), origin=(0, 0), resolution=(1.0, 1.0)
    )

    assert grid.shape == (5, 5, 4)
    assert math.isclose(grid[0, 0, 0], 50.0)
    assert math.isclose(grid[-1, -1, 2], 100.0)


def test_fetch_tile_requires_url() -> None:
    request = TileRequest(url="", params={})

    with pytest.raises(ValidationError, match="URL is required"):
        fetch_tile(request)


@respx.mock
def test_fetch_tile_returns_successful_response() -> None:
    request = TileRequest(
        url="https://example.com/tile",
        params={"layer": "a"},
        output_format=Format.GEOTIFF,
        retries=0,
    )
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(
            200,
            content=b"tile-bytes",
            headers={"content-type": "image/tiff"},
        )
    )

    response = fetch_tile(
        request,
        policy=FetchPolicy(max_concurrent=1, rate_limit_per_second=None),
    )

    assert response.success is True
    assert response.data == b"tile-bytes"


@respx.mock
def test_fetch_tile_returns_error_after_http_failure() -> None:
    request = TileRequest(
        url="https://example.com/tile",
        params={},
        retries=0,
    )
    respx.get("https://example.com/tile").mock(
        return_value=httpx.Response(500, text="server error")
    )

    response = fetch_tile(
        request,
        policy=FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0),
    )

    assert response.success is False
    assert response.status_code == 500
    assert response.error_message is not None


@respx.mock
def test_fetch_tile_returns_error_after_network_failure() -> None:
    request = TileRequest(
        url="https://example.com/tile",
        params={},
        retries=0,
    )
    respx.get("https://example.com/tile").mock(
        side_effect=httpx.ConnectError("offline")
    )

    response = fetch_tile(
        request,
        policy=FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0),
    )

    assert response.success is False
    assert response.status_code == 0
    assert "Network error" in (response.error_message or "")


def test_save_tile_writes_bytes_to_disk(tmp_path: Path) -> None:
    output_path = tmp_path / "nested" / "tile.tif"
    tile_response = TileResponse(
        data=b"abc",
        content_type="image/tiff",
        status_code=200,
        headers={},
        url="https://example.com/tile",
        success=True,
    )

    assert save_tile(tile_response, output_path) is True
    assert output_path.read_bytes() == b"abc"


def test_save_tile_returns_false_for_failed_response() -> None:
    tile_response = TileResponse(
        data=b"",
        content_type="",
        status_code=500,
        headers={},
        url="https://example.com/tile",
        success=False,
        error_message="failed",
    )

    with patch("tilearray.tiles.logger") as logger:
        assert save_tile(tile_response, "/tmp/unused.tif") is False
    logger.error.assert_called_once()
