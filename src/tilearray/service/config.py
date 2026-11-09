"""Configuration helpers for constructing service instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..error_messages import (
    coverage_build_service_config_message,
    coverage_build_service_network_message,
)
from ..errors import (
    ConfigurationError,
    NetworkError,
    TileArrayError,
    ValidationError,
)
from ..fetch import (
    _DEFAULT_MULTIPLICATIVE_DECREASE,
    FetchPolicy,
    HostRateLimiter,
)
from ..types import CRS, Format, ServiceTypeEnum
from .base import BaseService, get_service


class ServiceConfig(BaseModel):
    """Serializable configuration describing how to build a service instance."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    base_url: str = Field(..., description="Base endpoint URL for the service")
    service_type: ServiceTypeEnum = Field(
        ..., description="Type of service to instantiate"
    )
    crs: CRS | None = Field(
        None, description="Preferred coordinate reference system for requests"
    )
    output_format: Format | None = Field(
        None, description="Preferred data format for tile requests"
    )
    headers: dict[str, str] = Field(
        default_factory=dict, description="Additional HTTP headers to include"
    )
    params: dict[str, Any] = Field(
        default_factory=dict, description="Additional query parameters to include"
    )
    chunk_size: tuple[int, int] | None = Field(
        None,
        description="Default chunk size (width, height) to use when building arrays",
    )
    grid_shape: tuple[int, int] | None = Field(
        None,
        description="Default grid shape (rows, cols) to use when planning tiles",
    )

    @field_validator("chunk_size", "grid_shape")
    @classmethod
    def _positive_size_pair(
        cls, value: tuple[int, int] | None
    ) -> tuple[int, int] | None:
        if value is None:
            return None
        if len(value) != 2:
            raise ValidationError("size fields must be a pair of positive integers")
        a, b = int(value[0]), int(value[1])
        if a <= 0 or b <= 0:
            raise ValidationError("size fields must contain positive integers")
        return a, b

    cache_dir: str | Path | None = Field(
        None, description="Optional cache directory for fetched tiles"
    )
    resolution: tuple[float, float] | None = Field(
        None,
        description="Native resolution of the service responses (units per pixel in X and Y)",
    )
    max_concurrent_requests: int = Field(
        default=3,
        ge=1,
        description="Maximum in-flight tile HTTP requests for this service",
    )
    initial_concurrent_requests: int = Field(
        default=2,
        ge=1,
        description="Starting in-flight limit when adaptive_concurrency is enabled",
    )
    min_concurrent_requests: int = Field(
        default=1,
        ge=1,
        description="Floor in-flight limit after AIMD multiplicative decrease",
    )
    multiplicative_decrease: float = Field(
        default=_DEFAULT_MULTIPLICATIVE_DECREASE,
        gt=0.0,
        lt=1.0,
        description="AIMD multiplicative decrease factor on pressure (0–1 exclusive)",
    )
    adaptive_concurrency: bool = Field(
        default=False,
        description=(
            "Enable per-host AIMD concurrency (additive increase on success, "
            "multiplicative decrease on 429/timeouts)"
        ),
    )
    max_connections: int | None = Field(
        default=None,
        ge=1,
        description="httpx connection pool size (defaults to max_concurrent_requests)",
    )
    fetch_retries: int = Field(
        default=3,
        ge=0,
        description="Retry attempts for retryable HTTP/network tile fetch failures",
    )
    fetch_timeout: float = Field(
        default=30.0,
        gt=0,
        description="Per-tile HTTP read/connect timeout in seconds",
    )
    rate_limit_per_second: float | None = Field(
        default=2.0,
        description=(
            "Conservative per-host request rate for tile fetches (requests/s); "
            "set to None to disable the built-in limiter"
        ),
    )
    rate_limiter: HostRateLimiter | None = Field(
        default=None,
        description="Optional custom per-host rate limiter hook",
    )
    forbidden_circuit_breaker: bool = Field(
        default=False,
        description=(
            "Trip a pressure circuit breaker (freeze at floor) when classified "
            "breaker events (e.g. gateway 403 or 429) cluster within "
            "forbidden_window_seconds"
        ),
    )
    forbidden_window_seconds: float = Field(
        default=5.0,
        gt=0,
        description="Sliding window for counting clustered breaker-classified events",
    )
    forbidden_threshold: int = Field(
        default=6,
        ge=1,
        description="Breaker event count within the window that trips the circuit breaker",
    )
    forbidden_cooldown_seconds: float = Field(
        default=15.0,
        gt=0,
        description="Cooldown at floor after the circuit breaker trips",
    )

    def build_service(self) -> BaseService:
        """Create the appropriate service implementation for this configuration."""

        return get_service(
            self.base_url,
            service_type=self.service_type,
            **self.service_kwargs(),
        )

    # ------------------------------------------------------------------
    # Helper accessors
    # ------------------------------------------------------------------
    def fetch_policy(self) -> FetchPolicy:
        """Build a :class:`~tilearray.fetch.FetchPolicy` from this configuration."""

        return FetchPolicy(
            max_concurrent=self.max_concurrent_requests,
            max_connections=self.max_connections,
            retries=self.fetch_retries,
            timeout=self.fetch_timeout,
            rate_limit_per_second=self.rate_limit_per_second,
            rate_limiter=self.rate_limiter,
            adaptive_concurrency=self.adaptive_concurrency,
            initial_concurrent=self.initial_concurrent_requests,
            min_concurrent=self.min_concurrent_requests,
            multiplicative_decrease=self.multiplicative_decrease,
            forbidden_circuit_breaker=self.forbidden_circuit_breaker,
            forbidden_window_seconds=self.forbidden_window_seconds,
            forbidden_threshold=self.forbidden_threshold,
            forbidden_cooldown_seconds=self.forbidden_cooldown_seconds,
        )

    def service_kwargs(self) -> dict[str, Any]:
        """Keyword arguments used when instantiating the service."""

        kwargs: dict[str, Any] = {}
        if self.crs is not None:
            kwargs["crs"] = self.crs
        if self.output_format is not None:
            kwargs["output_format"] = self.output_format
        if self.headers:
            kwargs["headers"] = dict(self.headers)
        if self.params:
            kwargs["params"] = dict(self.params)
        # Plan-time caps / DescribeCoverage share the tile fetch timeout policy.
        kwargs["fetch_timeout"] = self.fetch_timeout
        return kwargs

    def tile_kwargs(self) -> dict[str, Any]:
        """Keyword arguments provided to the tile request planner."""

        kwargs: dict[str, Any] = {}
        if self.crs is not None:
            kwargs["crs"] = self.crs
        if self.output_format is not None:
            kwargs["output_format"] = self.output_format
        if self.params:
            kwargs["params"] = dict(self.params)
        if self.headers:
            kwargs["headers"] = dict(self.headers)
        if self.resolution is not None:
            kwargs["resolution"] = self.resolution
        return kwargs

    def array_defaults(self) -> dict[str, Any]:
        """Default array-level configuration supplied by the service."""

        defaults: dict[str, Any] = {}
        if self.chunk_size is not None:
            defaults["chunk_size"] = self.chunk_size
        if self.grid_shape is not None:
            defaults["grid_shape"] = self.grid_shape
        if self.cache_dir is not None:
            defaults["cache_dir"] = self.cache_dir
        if self.resolution is not None:
            defaults["resolution"] = self.resolution
        return defaults


class WCSConfig(ServiceConfig):
    """Configuration helper for Web Coverage Services."""

    coverage_id: str = Field(..., description="Coverage identifier to request")
    version: str = Field(default="2.0.1", description="WCS protocol version")
    output_crs: CRS | None = Field(
        default=None,
        description=(
            "Optional server-side GetCoverage outputCRS. When set, mosaic "
            "attrs['mosaic_crs'] matches this CRS. Not a client-side warp."
        ),
    )
    service_type: ServiceTypeEnum = Field(
        default=ServiceTypeEnum.WCS, init=False, description="Service type constant"
    )

    @classmethod
    def from_url(cls, url: str, coverage_id: str, **kwargs: Any) -> WCSConfig:
        """Convenience constructor mirroring high-level usage patterns."""

        return cls(base_url=url, coverage_id=coverage_id, **kwargs)

    def build_service(self) -> BaseService:
        """Construct a ``WCSService`` instance from this configuration."""

        from .wcs import WCSService

        kwargs = self.service_kwargs()
        kwargs.setdefault("coverage_id", self.coverage_id)
        kwargs.setdefault("version", self.version)
        service = WCSService(self.base_url, **kwargs)

        try:
            service.describe_coverage(self.coverage_id)
        except NetworkError as exc:
            raise NetworkError(
                coverage_build_service_network_message(
                    self.coverage_id, self.base_url, exc
                ),
                cause=exc,
            ) from exc
        except httpx.HTTPError as exc:
            raise NetworkError(
                coverage_build_service_network_message(
                    self.coverage_id, self.base_url, exc
                ),
                cause=exc,
            ) from exc
        except TileArrayError as exc:
            raise ConfigurationError(
                coverage_build_service_config_message(
                    self.coverage_id, self.base_url, exc
                ),
                cause=exc,
            ) from exc

        return service

    def service_kwargs(self) -> dict[str, Any]:
        kwargs = super().service_kwargs()
        kwargs.setdefault("coverage_id", self.coverage_id)
        kwargs.setdefault("version", self.version)
        return kwargs

    def tile_kwargs(self) -> dict[str, Any]:
        kwargs = super().tile_kwargs()
        kwargs["coverage_id"] = self.coverage_id
        if self.output_crs is not None:
            kwargs["output_crs"] = self.output_crs
        return kwargs


class XYZConfig(ServiceConfig):
    """Configuration helper for XYZ / slippy-map tile templates."""

    zoom: int = Field(..., ge=0, description="Fixed zoom level for tile requests")
    tile_size: int = Field(default=256, gt=0, description="Tile edge length in pixels")
    service_type: ServiceTypeEnum = Field(
        default=ServiceTypeEnum.XYZ, init=False, description="Service type constant"
    )

    @classmethod
    def from_url(cls, url: str, *, zoom: int, **kwargs: Any) -> XYZConfig:
        """Convenience constructor for XYZ tile templates."""

        return cls(base_url=url, zoom=zoom, **kwargs)

    def build_service(self) -> BaseService:
        """Construct an ``XYZService`` instance from this configuration."""

        from .xyz import XYZService

        kwargs = self.service_kwargs()
        kwargs.setdefault("zoom", self.zoom)
        kwargs.setdefault("tile_size", self.tile_size)
        return XYZService(self.base_url, **kwargs)

    def service_kwargs(self) -> dict[str, Any]:
        kwargs = super().service_kwargs()
        kwargs.setdefault("zoom", self.zoom)
        kwargs.setdefault("tile_size", self.tile_size)
        return kwargs

    def tile_kwargs(self) -> dict[str, Any]:
        kwargs = super().tile_kwargs()
        kwargs["zoom"] = self.zoom
        kwargs["tile_size"] = self.tile_size
        return kwargs


class WMSConfig(ServiceConfig):
    """Configuration helper for Web Map Services."""

    layers: str = Field(..., description="Comma-separated WMS layer names")
    version: str = Field(default="1.3.0", description="WMS protocol version")
    styles: str = Field(default="", description="WMS style names")
    output_crs: CRS | None = Field(
        default=None,
        description=(
            "Optional GetMap CRS when the server should render pixels in a CRS "
            "other than the query. GetMap has one CRS for BBOX and image — "
            "not a client-side warp."
        ),
    )
    service_type: ServiceTypeEnum = Field(
        default=ServiceTypeEnum.WMS, init=False, description="Service type constant"
    )

    @classmethod
    def from_url(cls, url: str, layers: str, **kwargs: Any) -> WMSConfig:
        """Convenience constructor for WMS endpoints."""

        return cls(base_url=url, layers=layers, **kwargs)

    def build_service(self) -> BaseService:
        """Construct a ``WMSService`` instance from this configuration."""

        from .wms import WMSService

        kwargs = self.service_kwargs()
        kwargs.setdefault("layers", self.layers)
        kwargs.setdefault("version", self.version)
        kwargs.setdefault("styles", self.styles)
        return WMSService(self.base_url, **kwargs)

    def service_kwargs(self) -> dict[str, Any]:
        kwargs = super().service_kwargs()
        kwargs.setdefault("layers", self.layers)
        kwargs.setdefault("version", self.version)
        kwargs.setdefault("styles", self.styles)
        return kwargs

    def tile_kwargs(self) -> dict[str, Any]:
        kwargs = super().tile_kwargs()
        kwargs["layers"] = self.layers
        kwargs["styles"] = self.styles
        if self.output_crs is not None:
            kwargs["output_crs"] = self.output_crs
        return kwargs


class WMTSConfig(ServiceConfig):
    """Configuration helper for Web Map Tile Services."""

    layer: str = Field(..., description="WMTS layer identifier")
    tile_matrix_set: str = Field(..., description="Tile matrix set identifier")
    tile_matrix: int | str = Field(
        ...,
        description="TileMatrix identifier (numeric zoom or non-numeric matrix id)",
    )
    style: str = Field(default="", description="WMTS style identifier")
    url_template: str | None = Field(
        default=None,
        description="REST GetTile URL template from GetCapabilities",
    )
    version: str = Field(default="1.0.0", description="WMTS protocol version")
    service_type: ServiceTypeEnum = Field(
        default=ServiceTypeEnum.WMTS, init=False, description="Service type constant"
    )

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        layer: str,
        tile_matrix_set: str,
        tile_matrix: int | str,
        **kwargs: Any,
    ) -> WMTSConfig:
        """Convenience constructor for WMTS endpoints."""

        return cls(
            base_url=url,
            layer=layer,
            tile_matrix_set=tile_matrix_set,
            tile_matrix=tile_matrix,
            **kwargs,
        )

    def build_service(self) -> BaseService:
        """Construct a ``WMTSService`` instance from this configuration."""

        from .wmts import WMTSService

        kwargs = self.service_kwargs()
        kwargs.setdefault("layer", self.layer)
        kwargs.setdefault("tile_matrix_set", self.tile_matrix_set)
        kwargs.setdefault("tile_matrix", self.tile_matrix)
        kwargs.setdefault("style", self.style)
        kwargs.setdefault("version", self.version)
        if self.url_template is not None:
            kwargs.setdefault("url_template", self.url_template)
        return WMTSService(self.base_url, **kwargs)

    def service_kwargs(self) -> dict[str, Any]:
        kwargs = super().service_kwargs()
        kwargs.setdefault("layer", self.layer)
        kwargs.setdefault("tile_matrix_set", self.tile_matrix_set)
        kwargs.setdefault("tile_matrix", self.tile_matrix)
        kwargs.setdefault("style", self.style)
        kwargs.setdefault("version", self.version)
        if self.url_template is not None:
            kwargs.setdefault("url_template", self.url_template)
        return kwargs

    def tile_kwargs(self) -> dict[str, Any]:
        kwargs = super().tile_kwargs()
        kwargs["layer"] = self.layer
        kwargs["tile_matrix_set"] = self.tile_matrix_set
        kwargs["tile_matrix"] = self.tile_matrix
        kwargs["style"] = self.style
        if self.url_template is not None:
            kwargs["url_template"] = self.url_template
        return kwargs
