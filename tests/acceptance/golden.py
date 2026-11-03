"""Independent golden expectations for WMTS tile maths (#97).

These helpers intentionally do **not** call tilearray planning code. Expected
bounds come from OGC WMTS scale-denominator rules so acceptance tests can
detect silent circular agreement with the library under test.
"""

from __future__ import annotations

from typing import Literal

# OGC WMTS 1.0 standardised rendering pixel size (0.28 mm) expressed in metres.
STANDARD_PIXEL_SIZE_M = 0.00028

# Common equatorial metres-per-degree factor used in GIS-SE / GeoTools examples.
# Documented as an approximation; golden fixtures pin the exact constant.
METERS_PER_DEGREE = 111319.49079327358

CrsKind = Literal["projected", "geographic"]


def meters_per_unit(crs_kind: CrsKind) -> float:
    """Return metres per CRS unit for WMTS pixel-span calculations."""

    if crs_kind == "projected":
        return 1.0
    if crs_kind == "geographic":
        return METERS_PER_DEGREE
    raise ValueError(f"Unknown crs_kind: {crs_kind!r}")


def pixel_span(scale_denominator: float, crs_kind: CrsKind) -> float:
    """Ground size of one pixel in CRS units."""

    return scale_denominator * STANDARD_PIXEL_SIZE_M / meters_per_unit(crs_kind)


def tile_span(
    scale_denominator: float,
    tile_width: int,
    tile_height: int,
    crs_kind: CrsKind,
) -> tuple[float, float]:
    """Return ``(span_x, span_y)`` for one matrix tile in CRS units."""

    px = pixel_span(scale_denominator, crs_kind)
    return px * tile_width, px * tile_height


def tile_bounds_nw(
    *,
    top_left_x: float,
    top_left_y: float,
    col: int,
    row: int,
    span_x: float,
    span_y: float,
) -> tuple[float, float, float, float]:
    """Bounds for a tile whose matrix origin is the north-west corner.

    Returns ``(min_x, min_y, max_x, max_y)`` with X increasing east and Y
    decreasing south (standard projected / lon-lat NW origin).
    """

    min_x = top_left_x + col * span_x
    max_x = min_x + span_x
    max_y = top_left_y - row * span_y
    min_y = max_y - span_y
    return min_x, min_y, max_x, max_y
