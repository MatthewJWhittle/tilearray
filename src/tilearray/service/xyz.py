"""XYZ (slippy map) tile service implementation."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any

from ..errors import ValidationError
from ..types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest
from .base import BaseService, TileGeometry, register_service
from .requests import tile_option_passthrough

# Spherical Web Mercator half-world extent (π · WGS84 semi-major axis).
_WEB_MERCATOR_ORIGIN = math.pi * 6378137.0


@register_service(ServiceTypeEnum.XYZ)
class XYZService(BaseService):
    """Client for XYZ / slippy-map tile endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        zoom: int,
        tile_size: int = 256,
        output_format: Format | None = Format.PNG,
        crs: CRS | None = CRS.EPSG_4326,
        **config: Any,
    ) -> None:
        super().__init__(base_url, zoom=zoom, tile_size=tile_size, **config)
        if zoom < 0:
            raise ValidationError("zoom must be non-negative")
        if tile_size <= 0:
            raise ValidationError("tile_size must be positive")
        # Use peeled base_url so template query strings are not double-applied.
        self.url_template = self.base_url
        self.zoom = zoom
        self.tile_size = tile_size
        self.output_format = output_format or Format.PNG
        # Default query CRS for bbox interpretation when callers omit crs=.
        # Slippy pixels are always Web Mercator; mosaic labelling uses 3857.
        self.native_crs = crs or CRS.EPSG_4326

    @classmethod
    def from_url(cls, url: str, **config: Any) -> XYZService:
        zoom = config.get("zoom")
        if zoom is None:
            raise ValidationError("XYZ services require a zoom level")
        return cls(url, **config)

    def plan_tiles(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: object,
    ) -> Iterable[TileGeometry]:
        zoom_value = options.get("zoom", self.zoom)
        tile_size_value = options.get("tile_size", self.tile_size)
        zoom = int(zoom_value) if isinstance(zoom_value, (int, str)) else self.zoom
        tile_size = (
            int(tile_size_value)
            if isinstance(tile_size_value, (int, str))
            else self.tile_size
        )
        # crs= / options["crs"] is query CRS only (bbox already normalised by
        # ArrayRequest); slippy mosaics are labelled EPSG:3857 via MosaicPlan
        # (indexed tile union) — do not label TileGeometry with the query CRS.

        working_bbox = bbox if bbox.crs == CRS.EPSG_4326 else bbox.to_crs(CRS.EPSG_4326)
        x_min, y_min, x_max, y_max = _tile_range_for_bbox(working_bbox, zoom)

        for tile_y in range(y_min, y_max + 1):
            for tile_x in range(x_min, x_max + 1):
                tile_bbox = _tile_bounds_3857(tile_x, tile_y, zoom)
                yield TileGeometry(
                    bbox=tile_bbox,
                    width=tile_size,
                    height=tile_size,
                    crs=CRS.EPSG_3857,
                    tile_x=tile_x,
                    tile_y=tile_y,
                    zoom=zoom,
                )

    def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
        tile_x = _require_tile_index(tile.tile_x, options.get("x"))
        tile_y = _require_tile_index(tile.tile_y, options.get("y"))
        zoom = int(
            options.get("zoom", tile.zoom if tile.zoom is not None else self.zoom)
        )

        url = self.url_template.format(x=tile_x, y=tile_y, z=zoom)
        fmt = options.get("output_format") or self.output_format
        if isinstance(fmt, str):
            fmt = Format(fmt)

        return self.compose_tile_request(
            tile,
            url=url,
            output_format=fmt,
            **tile_option_passthrough(options, "x", "y", "zoom"),
        )


def _require_tile_index(tile_index: int | None, override: int | None) -> int:
    value = override if override is not None else tile_index
    if value is None:
        raise ValidationError("XYZ tile indices are required")
    return int(value)


def _tile_range_for_bbox(bbox: BoundingBox, zoom: int) -> tuple[int, int, int, int]:
    x_min = _lon_to_tile_x(bbox.min_x, zoom)
    x_max = _lon_to_tile_x(bbox.max_x, zoom)
    y_min = _lat_to_tile_y(bbox.max_y, zoom)
    y_max = _lat_to_tile_y(bbox.min_y, zoom)
    return x_min, y_min, x_max, y_max


def _lon_to_tile_x(lon: float, zoom: int) -> int:
    scale = 2**zoom
    return int(math.floor((lon + 180.0) / 360.0 * scale))


def _lat_to_tile_y(lat: float, zoom: int) -> int:
    scale = 2**zoom
    lat_rad = math.radians(lat)
    return int(
        math.floor((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale)
    )


def _tile_span_3857(zoom: int) -> float:
    """Axis-aligned Web Mercator tile width/height in metres at ``zoom``."""

    return float(2.0 * _WEB_MERCATOR_ORIGIN / float(2**zoom))


def _tile_bounds_3857(x: int, y: int, zoom: int) -> BoundingBox:
    """Native EPSG:3857 footprint of one slippy tile (metre-linear grid)."""

    span = _tile_span_3857(zoom)
    min_x = -_WEB_MERCATOR_ORIGIN + x * span
    max_x = min_x + span
    max_y = _WEB_MERCATOR_ORIGIN - y * span
    min_y = max_y - span
    return BoundingBox(
        min_x=min_x,
        min_y=min_y,
        max_x=max_x,
        max_y=max_y,
        crs=CRS.EPSG_3857,
    )


def _tile_bounds(x: int, y: int, zoom: int, crs: CRS) -> BoundingBox:
    """Geographic envelope of a slippy tile, optionally reprojected.

    Prefer :func:`_tile_bounds_3857` for mosaic georef — corner lon/lat envelopes
    are for tile-index helpers and tests, not for labelling Mercator pixels.
    """

    scale = 2**zoom
    min_x = x / scale * 360.0 - 180.0
    max_x = (x + 1) / scale * 360.0 - 180.0
    max_y = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / scale))))
    min_y = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / scale))))
    bbox = BoundingBox(
        min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y, crs=CRS.EPSG_4326
    )
    return bbox if crs == CRS.EPSG_4326 else bbox.to_crs(crs)
