"""Configuration helpers for constructing service instances."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from requests import RequestException

from ..fetch import FetchPolicy, HostRateLimiter
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
    service_type: ServiceTypeEnum = Field(
        default=ServiceTypeEnum.WCS, init=False, description="Service type constant"
    )

    @classmethod
    def from_url(cls, url: str, coverage_id: str, **kwargs: Any) -> WCSConfig:
        """Convenience constructor mirroring high-level usage patterns."""

        return cls(base_url=url, coverage_id=coverage_id, **kwargs)

    @classmethod
    def for_ea_dsp(
        cls,
        url: str,
        coverage_id: str,
        **kwargs: Any,
    ) -> WCSConfig:
        """
        WCS preset for Environment Agency Data Service Platform endpoints.

        Enables per-host AIMD concurrency (starts at 8, ceiling 32) plus extra
        retries for Retry-After / 429 / 503 behaviour.
        """

        from ..fetch_presets import ea_dsp_fetch_defaults

        defaults = ea_dsp_fetch_defaults()
        defaults.update(kwargs)
        return cls.from_url(url, coverage_id=coverage_id, **defaults)

    def build_service(self) -> BaseService:
        """Construct a ``WCSService`` instance from this configuration."""

        from .wcs import WCSService

        kwargs = self.service_kwargs()
        kwargs.setdefault("coverage_id", self.coverage_id)
        kwargs.setdefault("version", self.version)
        service = WCSService(self.base_url, **kwargs)

        try:
            service.describe_coverage(self.coverage_id)
        except ValueError as exc:
            raise ValueError(
                f"Coverage '{self.coverage_id}' is not available from {self.base_url}"  # noqa: G004
            ) from exc
        except RequestException as exc:
            raise ValueError(
                f"Failed to validate coverage '{self.coverage_id}' against {self.base_url}"  # noqa: G004
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

    @classmethod
    def for_openstreetmap(
        cls,
        *,
        zoom: int,
        user_agent: str | None = None,
        contact_url: str | None = None,
        **kwargs: Any,
    ) -> XYZConfig:
        """
        XYZ preset for OpenStreetMap raster tiles (OSMF tile usage policy).

        Sets an identifiable User-Agent and polite concurrency / rate limits.
        """

        from ..fetch_presets import OSM_TILE_TEMPLATE, osm_fetch_defaults

        defaults = osm_fetch_defaults(
            user_agent=user_agent,
            contact_url=contact_url
            or "https://github.com/MatthewJWhittle/tilearray/issues",
        )
        defaults.update(kwargs)
        return cls.from_url(OSM_TILE_TEMPLATE, zoom=zoom, **defaults)

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
