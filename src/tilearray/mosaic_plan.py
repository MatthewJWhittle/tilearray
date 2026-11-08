"""Typed mosaic plan result (tiles + native extent/grid).

Owns the seam between protocol planning and ``ArrayRequest``: one call returns
HTTP tile requests **plus** optional native mosaic bbox/CRS/grid. Fixed grids
(XYZ, metadata/slippy WMTS) expose index-addressed geometries; query-window
planners (WMS/WCS) do not, so mosaic extent stays the prepared query bbox.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .types import CRS, BoundingBox, TileRequest


@runtime_checkable
class IndexedTileGeometry(Protocol):
    """Minimal geometry shape used to detect native fixed-grid mosaics."""

    tile_x: int | None
    tile_y: int | None
    bbox: BoundingBox
    crs: CRS


@dataclass(frozen=True)
class MosaicPlan:
    """Result of planning tiles for one AOI.

    ``mosaic_bbox`` / ``grid_shape`` are set for native fixed grids (tile indices
    present). When both are ``None``, the caller keeps the query bbox and CRS
    prepared before planning (WMS/WCS).
    """

    tiles: Sequence[TileRequest]
    grid_shape: tuple[int, int] | None = None
    mosaic_bbox: BoundingBox | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tiles", tuple(self.tiles))

    @classmethod
    def from_planning(
        cls,
        tiles: Sequence[TileRequest],
        geometries: Sequence[IndexedTileGeometry],
    ) -> MosaicPlan:
        """Build a plan from materialised requests and their spatial layout."""

        meta = native_mosaic_meta(geometries)
        if meta is None:
            return cls(tiles=tiles)
        grid_shape, mosaic_bbox = meta
        return cls(tiles=tiles, grid_shape=grid_shape, mosaic_bbox=mosaic_bbox)

    def __iter__(self) -> Iterator[TileRequest]:
        return iter(self.tiles)

    def __len__(self) -> int:
        return len(self.tiles)

    def __getitem__(self, index: int) -> TileRequest:
        return self.tiles[index]


def native_mosaic_meta(
    geometries: Sequence[IndexedTileGeometry],
) -> tuple[tuple[int, int], BoundingBox] | None:
    """Return ``(grid_shape, mosaic_bbox)`` for an indexed native tile grid.

    Returns ``None`` when geometries are not index-addressed (query-window
    planners), so mosaic CRS/extent stay with the caller's prepared bbox.
    """

    if not geometries:
        return None
    if any(geom.tile_x is None or geom.tile_y is None for geom in geometries):
        return None

    xs = [int(geom.tile_x) for geom in geometries if geom.tile_x is not None]
    ys = [int(geom.tile_y) for geom in geometries if geom.tile_y is not None]
    if len(xs) != len(geometries) or len(ys) != len(geometries):
        return None
    cols = max(xs) - min(xs) + 1
    rows = max(ys) - min(ys) + 1

    mosaic_crs = geometries[0].crs
    if any(geom.crs != mosaic_crs for geom in geometries):
        return None
    if any(geom.bbox.crs != mosaic_crs for geom in geometries):
        return None

    mosaic_bbox = BoundingBox(
        min_x=min(geom.bbox.min_x for geom in geometries),
        min_y=min(geom.bbox.min_y for geom in geometries),
        max_x=max(geom.bbox.max_x for geom in geometries),
        max_y=max(geom.bbox.max_y for geom in geometries),
        crs=mosaic_crs,
    )
    return (rows, cols), mosaic_bbox
