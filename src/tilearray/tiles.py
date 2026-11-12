"""
Generic tile fetching functionality for geospatial services.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from .errors import ValidationError
from .fetch import (
    FetchPolicy,
    FetchProgress,
    TileFetcher,
    fetch_tile_with_policy,
    get_fetcher,
)
from .types import BoundingBox, TileRequest, TileResponse

logger = logging.getLogger(__name__)

__all__ = [
    "FetchPolicy",
    "TileFetcher",
    "create_tile_grid",
    "fetch_tile",
    "get_fetcher",
    "resolution_axis_tile_sizes",
    "save_tile",
    "snap_bbox_outward",
]


def fetch_tile(
    request: TileRequest,
    *,
    policy: FetchPolicy | None = None,
    fetcher: TileFetcher | None = None,
    progress: FetchProgress | None = None,
) -> TileResponse:
    """
    Fetch a tile from any geospatial service.

    Args:
        request: Tile request parameters
        policy: Optional fetch policy (concurrency, retries, rate limits)
        fetcher: Optional explicit fetcher instance

    Returns:
        Tile response with data or error information
    """
    if fetcher is not None:
        return fetcher.fetch(request, progress=progress)
    return fetch_tile_with_policy(request, policy, progress=progress)


def save_tile(tile_response: TileResponse, output_path: str | Path) -> bool:
    """
    Save tile data to file.

    Args:
        tile_response: Response from tile request
        output_path: Path to save the tile

    Returns:
        True if successful, False otherwise
    """
    if not tile_response.success:
        logger.error(f"Cannot save failed tile: {tile_response.error_message}")
        return False

    try:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "wb") as f:
            f.write(tile_response.data)

        logger.debug(f"Saved tile to {output_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to save tile to {output_path}: {e}")
        return False


def snap_bbox_outward(
    bbox: BoundingBox,
    resolution: tuple[float, float],
    origin: tuple[float, float] = (0.0, 0.0),
) -> BoundingBox:
    """
    Align ``bbox`` onto an origin-aligned pixel grid without changing pixel count.

    Pixel counts come from the *requested* span via ``round(span / resolution)``
    (minimum 1), not from the union of grid cells that intersect the box. The
    minimum corner is snapped down onto the grid and the maximum is set to
    ``min + n_px * resolution``, so an exact N-pixel ground span stays N pixels
    even when the request is not origin-aligned. Already-aligned integer spans
    are unchanged. Sub-pixel requests still expand to one native pixel.
    """
    res_x, res_y = resolution
    if res_x <= 0 or res_y <= 0:
        raise ValidationError("resolution values must be positive")

    origin_x, origin_y = origin
    n_px = max(1, int(round((bbox.max_x - bbox.min_x) / res_x)))
    n_py = max(1, int(round((bbox.max_y - bbox.min_y) / res_y)))
    col_min = math.floor((bbox.min_x - origin_x) / res_x)
    row_min = math.floor((bbox.min_y - origin_y) / res_y)

    return BoundingBox(
        min_x=origin_x + col_min * res_x,
        min_y=origin_y + row_min * res_y,
        max_x=origin_x + (col_min + n_px) * res_x,
        max_y=origin_y + (row_min + n_py) * res_y,
        crs=bbox.crs,
    )


def resolution_axis_tile_sizes(n_px: int, chunk: int) -> list[int]:
    """
    Split ``n_px`` into tile sizes of at most ``chunk``.

    Only the final size may be shorter than ``chunk`` (for example 513 at 256
    → ``[256, 256, 1]``). Callers that organise tiles north-first / west-first
    must place this list in mosaic order so the short chunk stays last — the
    layout Zarr accepts — rather than as a leading sliver.
    """
    if chunk <= 0:
        raise ValidationError("chunk size must be positive")
    n_px = max(1, int(n_px))
    if n_px <= chunk:
        return [n_px]
    full, rem = divmod(n_px, chunk)
    if rem == 0:
        return [chunk] * full
    return [chunk] * full + [rem]


def create_tile_grid(
    bbox: BoundingBox,
    tile_size: tuple[int, int],
    origin: tuple[float, float],
    resolution: tuple[float, float],
) -> np.ndarray:
    """
    Create a grid of tiles covering the bounding box with the given tile size, origin and resolution.

    Tiles are aligned to a fixed grid anchored at ``origin``. Each tile spans
    ``tile_size`` pixels at the given ground resolution. When the bounding box
    is not grid-aligned, the returned tiles extend beyond the bbox so their
    union fully covers it.

    Args:
        bbox: Overall bounding box
        tile_size: Size of each tile in pixels as ``(width, height)``
        origin: Origin of the grid in the given CRS as ``(x, y)``
        resolution: Ground resolution as ``(x, y)`` units per pixel

    Returns:
        ``float64`` array with shape ``(n_rows, n_cols, 4)`` where the last
        dimension stores ``(min_x, min_y, max_x, max_y)`` for each tile.
        Rows increase with ``y`` (bottom row first).
    """
    tile_width, tile_height = tile_size
    res_x, res_y = resolution
    if tile_width <= 0 or tile_height <= 0:
        raise ValidationError("tile_size dimensions must be positive")
    if res_x <= 0 or res_y <= 0:
        raise ValidationError("resolution values must be positive")

    origin_x, origin_y = origin
    tile_width_units = tile_width * res_x
    tile_height_units = tile_height * res_y

    col_min = math.floor((bbox.min_x - origin_x) / tile_width_units)
    col_max = math.ceil((bbox.max_x - origin_x) / tile_width_units) - 1
    row_min = math.floor((bbox.min_y - origin_y) / tile_height_units)
    row_max = math.ceil((bbox.max_y - origin_y) / tile_height_units) - 1

    n_cols = col_max - col_min + 1
    n_rows = row_max - row_min + 1
    if n_cols <= 0 or n_rows <= 0:
        return np.empty((0, 0, 4), dtype=np.float64)

    grid = np.empty((n_rows, n_cols, 4), dtype=np.float64)
    for row_index, row in enumerate(range(row_min, row_max + 1)):
        min_y = origin_y + row * tile_height_units
        max_y = min_y + tile_height_units
        for col_index, col in enumerate(range(col_min, col_max + 1)):
            min_x = origin_x + col * tile_width_units
            max_x = min_x + tile_width_units
            grid[row_index, col_index] = (min_x, min_y, max_x, max_y)

    return grid
