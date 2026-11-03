"""Service registry and base abstractions for OGC tile services."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, Callable, cast
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from ..errors import ConfigurationError, ValidationError
from ..tiles import snap_bbox_outward
from ..types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest
from .requests import compose_tile_request as _compose_tile_request
from .requests import merge_str_mappings

logger = logging.getLogger(__name__)

__all__ = [
    "TileGeometry",
    "BaseService",
    "register_service",
    "detect_service_type",
    "get_service",
]


class TileGeometry(BaseModel):
    """Spatial description of a tile that will be fetched from a service."""

    bbox: BoundingBox = Field(..., description="Geographic extent that the tile covers")
    width: int = Field(..., gt=0, description="Tile width in output pixels")
    height: int = Field(..., gt=0, description="Tile height in output pixels")
    crs: CRS = Field(
        default=CRS.EPSG_4326, description="CRS in which bounds are expressed"
    )
    tile_x: int | None = Field(default=None, description="XYZ tile column index")
    tile_y: int | None = Field(default=None, description="XYZ tile row index")
    zoom: int | None = Field(default=None, description="XYZ zoom level")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("bbox")
    @classmethod
    def ensure_bbox_crs(
        cls, bbox: BoundingBox, info: ValidationInfo
    ) -> BoundingBox:  # pragma: no cover - simple validation
        crs = info.data.get("crs")
        if crs is not None and bbox.crs != crs:
            raise ValueError("TileGeometry bbox CRS must match the tile CRS")
        return bbox


class BaseService(ABC):
    """Abstract base class for service-specific implementations."""

    service_type: ServiceTypeEnum

    def __init__(self, base_url: str, **config: object) -> None:
        self.base_url = base_url.rstrip("/?")
        self.config = config

    @classmethod
    def from_url(cls, url: str, **config: object) -> BaseService:
        """Factory hook for constructing a service from a URL."""

        return cls(url, **config)

    # ------------------------------------------------------------------
    # Tile generation API
    # ------------------------------------------------------------------
    def generate_tile_requests(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: object,
    ) -> list[TileRequest]:
        """Generate concrete tile requests for the provided bounding box."""

        tile_geoms = list(self.plan_tiles(bbox, chunk_size, **options))
        return [
            self.build_tile_request(tile_geom, **options) for tile_geom in tile_geoms
        ]

    def plan_tiles(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: object,
    ) -> Iterable[TileGeometry]:
        """Return the spatial layout of tiles for the requested area."""

        width, height = chunk_size
        resolution = options.get("resolution")

        if resolution:
            res_x, res_y = cast(tuple[float, float], resolution)
            if res_x <= 0 or res_y <= 0:
                raise ValidationError("resolution values must be positive")

            origin = _coerce_grid_origin(options.get("origin"))
            snapped = snap_bbox_outward(bbox, (res_x, res_y), origin)
            if (
                snapped.min_x != bbox.min_x
                or snapped.min_y != bbox.min_y
                or snapped.max_x != bbox.max_x
                or snapped.max_y != bbox.max_y
            ):
                logger.debug(
                    "Snapped resolution plan bbox outward by "
                    "min_x=%s min_y=%s max_x=%s max_y=%s",
                    snapped.min_x - bbox.min_x,
                    snapped.min_y - bbox.min_y,
                    snapped.max_x - bbox.max_x,
                    snapped.max_y - bbox.max_y,
                )

            # Index-walk on the snapped pixel grid so every request spans at
            # least one native pixel (no epsilon drop / sub-pixel leftover).
            n_px = max(1, int(round((snapped.max_x - snapped.min_x) / res_x)))
            n_py = max(1, int(round((snapped.max_y - snapped.min_y) / res_y)))

            y0 = 0
            while y0 < n_py:
                tile_h = min(height, n_py - y0)
                min_y = snapped.min_y + y0 * res_y
                max_y = snapped.min_y + (y0 + tile_h) * res_y
                x0 = 0
                while x0 < n_px:
                    tile_w = min(width, n_px - x0)
                    min_x = snapped.min_x + x0 * res_x
                    max_x = snapped.min_x + (x0 + tile_w) * res_x
                    yield TileGeometry(
                        bbox=BoundingBox(
                            min_x=min_x,
                            min_y=min_y,
                            max_x=max_x,
                            max_y=max_y,
                            crs=bbox.crs,
                        ),
                        width=tile_w,
                        height=tile_h,
                        crs=bbox.crs,
                    )
                    x0 += tile_w
                y0 += tile_h
            return

        grid_shape_option = options.get("grid_shape")
        if grid_shape_option is None:
            rows, cols = 1, 1
        elif isinstance(grid_shape_option, tuple):
            try:
                grid_tuple_raw = cast(tuple[Any, Any], grid_shape_option)
                row_raw, col_raw = grid_tuple_raw
            except ValueError as exc:  # pragma: no cover - guard
                raise ValidationError(
                    "grid_shape must be a tuple of two integers"
                ) from exc

            rows, cols = int(row_raw), int(col_raw)
        else:
            raise ValidationError("grid_shape must be a tuple of two integers")
        if rows <= 0 or cols <= 0:
            raise ValidationError("grid_shape dimensions must be positive integers")

        x_span = float(bbox.max_x - bbox.min_x)
        y_span = float(bbox.max_y - bbox.min_y)

        step_x = x_span / cols if cols > 0 else x_span
        step_y = y_span / rows if rows > 0 else y_span

        for row in range(rows):
            min_y = float(bbox.min_y + row * step_y)
            max_y = float(
                bbox.min_y + (row + 1) * step_y if row < rows - 1 else bbox.max_y
            )
            for col in range(cols):
                min_x = float(bbox.min_x + col * step_x)
                max_x = float(
                    bbox.min_x + (col + 1) * step_x if col < cols - 1 else bbox.max_x
                )
                yield TileGeometry(
                    bbox=BoundingBox(
                        min_x=min_x,
                        min_y=min_y,
                        max_x=max_x,
                        max_y=max_y,
                        crs=bbox.crs,
                    ),
                    width=width,
                    height=height,
                    crs=bbox.crs,
                )

    def request_headers(self, **options: object) -> dict[str, str]:
        """Merge service-level and per-call HTTP headers."""

        return merge_str_mappings(self.config.get("headers"), options.get("headers"))

    def compose_tile_request(
        self,
        tile: TileGeometry,
        *,
        url: str,
        params: dict[str, Any] | None = None,
        output_format: Format | None = None,
        crs: CRS | None = None,
        **options: object,
    ) -> TileRequest:
        """Build a tile request with config headers/params applied."""

        return _compose_tile_request(
            config=self.config,
            options=dict(options),
            url=url,
            params=params,
            bbox=tile.bbox,
            width=tile.width,
            height=tile.height,
            crs=crs or tile.crs,
            output_format=output_format,
        )

    @abstractmethod
    def build_tile_request(
        self,
        tile: TileGeometry,
        **options: object,
    ) -> TileRequest:
        """Build the HTTP request description for a tile."""


def _coerce_grid_origin(value: object) -> tuple[float, float]:
    """Return a ``(x, y)`` grid origin, defaulting to ``(0.0, 0.0)``."""

    if value is None:
        return (0.0, 0.0)
    if isinstance(value, tuple) and len(value) == 2:
        origin_x = value[0]
        origin_y = value[1]
        return (float(origin_x), float(origin_y))
    raise ValidationError("origin must be a tuple of two numbers")


# ----------------------------------------------------------------------
# Service registry utilities
# ----------------------------------------------------------------------

_SERVICE_REGISTRY: dict[ServiceTypeEnum, type[BaseService]] = {}


def register_service(
    service_type: ServiceTypeEnum,
) -> Callable[[type[BaseService]], type[BaseService]]:
    """Decorator for registering service implementations."""

    def decorator(cls: type[BaseService]) -> type[BaseService]:
        _SERVICE_REGISTRY[service_type] = cls
        cls.service_type = service_type
        return cls

    return decorator


def detect_service_type(
    url: str, fallback: ServiceTypeEnum | None = None
) -> ServiceTypeEnum:
    """Infer the service type from the URL or query string."""

    parsed = urlparse(url)
    lower_path = parsed.path.lower()
    query = parse_qs(parsed.query.lower())

    if "service" in query:
        try:
            return ServiceTypeEnum(query["service"][0].upper())
        except ValueError:
            pass

    if "wcs" in lower_path:
        return ServiceTypeEnum.WCS
    if "wms" in lower_path:
        return ServiceTypeEnum.WMS
    if "wmts" in lower_path:
        return ServiceTypeEnum.WMTS

    lower_url = url.lower()
    if all(token in lower_url for token in ("{x}", "{y}", "{z}")):
        return ServiceTypeEnum.XYZ

    if fallback is not None:
        return fallback

    raise ConfigurationError(f"Unable to detect service type from URL: {url}")


def get_service(
    url: str,
    *,
    service_type: ServiceTypeEnum | None = None,
    **config: object,
) -> BaseService:
    """Instantiate the appropriate service implementation for `url`."""

    detected_type = service_type or detect_service_type(url)

    try:
        service_cls = _SERVICE_REGISTRY[detected_type]
    except KeyError as exc:  # pragma: no cover - defensive path
        raise ConfigurationError(
            f"No service registered for type {detected_type}"
        ) from exc

    return service_cls.from_url(url, **config)
