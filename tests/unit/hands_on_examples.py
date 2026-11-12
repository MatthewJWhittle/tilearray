"""Shared builders and fixture paths for durable hands-on behaviour tests."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from PIL import Image

from tilearray.service.base import TileGeometry
from tilearray.types import CRS, BoundingBox

DATA = Path(__file__).resolve().parents[1] / "data"
WMTS_CAPS = DATA / "wmts_caps"
PRED3_TIF = DATA / "wcs_tiles" / "float32_predictor3_deflate.tif"

TIME_DIMENSION_CAPS = WMTS_CAPS / "time_dimension_googlemaps.xml"
FORMAT_STYLE_CAPS = WMTS_CAPS / "format_style_kvp_and_rest.xml"
VERSIONED_URN_CAPS = WMTS_CAPS / "versioned_epsg_urn_supportedcrs.xml"
EPSG4326_WORLD_CAPS = WMTS_CAPS / "epsg4326_world_matrix.xml"


def png_bytes(height: int, width: int, bands: int = 3) -> bytes:
    """Synthetic PNG body for offline fetch/decode examples."""
    if bands == 1:
        arr: np.ndarray = np.zeros((height, width), dtype=np.uint8)
    else:
        arr = np.zeros((height, width, bands), dtype=np.uint8)
    buf = BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def tile_geometry(
    *,
    min_x: float = 0.0,
    min_y: float = 0.0,
    max_x: float = 1.0,
    max_y: float = 1.0,
    crs: CRS = CRS.EPSG_4326,
    width: int = 256,
    height: int = 256,
    tile_x: int | None = None,
    tile_y: int | None = None,
    zoom: int | None = None,
) -> TileGeometry:
    """Minimal request geometry for protocol builder examples."""
    return TileGeometry(
        bbox=BoundingBox(min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y, crs=crs),
        width=width,
        height=height,
        crs=crs,
        tile_x=tile_x,
        tile_y=tile_y,
        zoom=zoom,
    )
