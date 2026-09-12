"""
Generic tile fetching functionality for geospatial services.
"""

from typing import Dict, Any, Optional, Union, Tuple, List
from dataclasses import dataclass
import math
import requests
import logging
from pathlib import Path

import numpy as np

from .types import BoundingBox, CRS, Format, TileRequest, TileResponse

logger = logging.getLogger(__name__)


def fetch_tile(request: TileRequest) -> TileResponse:
    """
    Generic function to fetch a tile from any geospatial service.
    
    Args:
        request: Tile request parameters
        
    Returns:
        Tile response with data or error information
        
    Raises:
        requests.RequestException: For network-related errors
        ValueError: For invalid request parameters
    """
    if not request.url:
        raise ValueError("URL is required")
    
    # Prepare headers
    headers = request.headers or {}
    if request.output_format:
        headers.setdefault('Accept', request.output_format.value)
    
    # Make the request with retries
    last_exception = None
    for attempt in range(request.retries + 1):
        try:
            logger.debug(f"Fetching tile (attempt {attempt + 1}/{request.retries + 1}): {request.url}")
            
            response = requests.get(
                request.url,
                params=request.params or None,
                headers=headers,
                timeout=request.timeout,
                stream=True  # For large tiles
            )
            
            # Check if request was successful
            if response.status_code == 200:
                data = response.content
                return TileResponse(
                    data=data,
                    content_type=response.headers.get('content-type', ''),
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    url=response.url,
                    success=True
                )
            else:
                error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(f"Tile request failed: {error_msg}")
                
                if attempt == request.retries:  # Last attempt
                    return TileResponse(
                        data=b'',
                        content_type=response.headers.get('content-type', ''),
                        status_code=response.status_code,
                        headers=dict(response.headers),
                        url=response.url,
                        success=False,
                        error_message=error_msg
                    )
                
        except requests.RequestException as e:
            last_exception = e
            logger.warning(f"Tile request attempt {attempt + 1} failed: {e}")
            
            if attempt == request.retries:  # Last attempt
                return TileResponse(
                    data=b'',
                    content_type='',
                    status_code=0,
                    headers={},
                    url=request.url,
                    success=False,
                    error_message=f"Network error: {str(e)}"
                )
    
    # This should never be reached, but just in case
    return TileResponse(
        data=b'',
        content_type='',
        status_code=0,
        headers={},
        url=request.url,
        success=False,
        error_message=f"All retry attempts failed: {str(last_exception)}"
    )


def save_tile(tile_response: TileResponse, output_path: Union[str, Path]) -> bool:
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
        
        with open(output_path, 'wb') as f:
            f.write(tile_response.data)
        
        logger.debug(f"Saved tile to {output_path}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save tile to {output_path}: {e}")
        return False


# Tile Operations
def create_tile_grid(
    bbox: BoundingBox,
    tile_size: Tuple[int, int],
    origin: Tuple[float, float],
    resolution: Tuple[float, float],
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
        raise ValueError("tile_size dimensions must be positive")
    if res_x <= 0 or res_y <= 0:
        raise ValueError("resolution values must be positive")

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


