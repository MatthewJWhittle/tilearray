"""Service registry and base abstractions for OGC tile services."""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import Any, Callable, cast
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from ..types import CRS, BoundingBox, ServiceTypeEnum, TileRequest

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
                raise ValueError("resolution values must be positive")

            tile_width_units = width * res_x
            tile_height_units = height * res_y
            epsilon = min(res_x, res_y) / 10.0

            current_min_y = float(bbox.min_y)
            while current_min_y < bbox.max_y - epsilon:
                current_max_y = min(bbox.max_y, current_min_y + tile_height_units)
                span_y = current_max_y - current_min_y
                pixel_height = max(1, int(math.ceil(span_y / res_y)))

                current_min_x = float(bbox.min_x)
                while current_min_x < bbox.max_x - epsilon:
                    current_max_x = min(bbox.max_x, current_min_x + tile_width_units)
                    span_x = current_max_x - current_min_x
                    pixel_width = max(1, int(math.ceil(span_x / res_x)))

                    yield TileGeometry(
                        bbox=BoundingBox(
                            min_x=current_min_x,
                            min_y=current_min_y,
                            max_x=current_max_x,
                            max_y=current_max_y,
                            crs=bbox.crs,
                        ),
                        width=pixel_width,
                        height=pixel_height,
                        crs=bbox.crs,
                    )

                    current_min_x = current_max_x
                current_min_y = current_max_y
            return

        grid_shape_option = options.get("grid_shape")
        if grid_shape_option is None:
            rows, cols = 1, 1
        elif isinstance(grid_shape_option, tuple):
            try:
                grid_tuple_raw = cast(tuple[Any, Any], grid_shape_option)
                row_raw, col_raw = grid_tuple_raw
            except ValueError as exc:  # pragma: no cover - guard
                raise ValueError("grid_shape must be a tuple of two integers") from exc

            rows, cols = int(row_raw), int(col_raw)
        else:
            raise ValueError("grid_shape must be a tuple of two integers")
        if rows <= 0 or cols <= 0:
            raise ValueError("grid_shape dimensions must be positive integers")

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

    @abstractmethod
    def build_tile_request(
        self,
        tile: TileGeometry,
        **options: object,
    ) -> TileRequest:
        """Build the HTTP request description for a tile."""


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

    raise ValueError(f"Unable to detect service type from URL: {url}")


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
        raise ValueError(f"No service registered for type {detected_type}") from exc

    return service_cls.from_url(url, **config)
