"""Shared planning helpers for :mod:`tilearray.array` and :mod:`tilearray.preview`.

Owns :class:`ArrayRequest`, decoder resolution, band probing, attr-key
constants, fetch-policy snapshots, tile-grid organisation, cache keys, and
:func:`recommend_grid_shape` so ``preview_array`` does not reach into private
``array`` symbols.

Band probing needs the create-path tile loader. That is a documented cycle:
:func:`probe_tile_bands` / :func:`probe_tile_band_count` import
:func:`tilearray.array._load_tile_array` lazily inside the function body so
module import order stays acyclic. ``create_array`` may reuse the decoded
probe when fetch identity and shape/dtype match the mosaic tile.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .decode import (
    DEFAULT_BAND_POLICIES,
    BandPolicy,
    NDArrayAny,
    TileDecoder,
    band_count_from_array,
    build_format_decoder,
    default_band_count_for_format,
)
from .decode import (
    decoder_for_format as _decoder_for_format,
)
from .error_messages import missing_decoder_message
from .errors import ConfigurationError, ValidationError
from .fetch import FetchPolicy
from .service import get_service
from .service.base import BaseService
from .service.config import ServiceConfig as ServiceConfigModel
from .tiles import resolution_axis_tile_sizes, snap_bbox_outward
from .types import (
    CRS,
    BBoxTuple,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
)

# Public mosaic attrs keys (also used by plan / fetch_summary).
FETCH_POLICY_ATTR = "tilearray_fetch_policy"
RECOMMENDED_NUM_WORKERS_ATTR = "tilearray_recommended_num_workers"
TILE_ERRORS_ATTR = "tilearray_tile_errors"
# Internal handle so compute_mosaic can refresh tile_errors after lazy compute.
# Not part of the public attrs contract; stripped from eager/computed results.
PROGRESS_REF_ATTR = "_tilearray_fetch_progress"

# Back-compat aliases used inside array.py historically.
_FETCH_POLICY_ATTR = FETCH_POLICY_ATTR
_RECOMMENDED_NUM_WORKERS_ATTR = RECOMMENDED_NUM_WORKERS_ATTR
_TILE_ERRORS_ATTR = TILE_ERRORS_ATTR
_PROGRESS_REF_ATTR = PROGRESS_REF_ATTR


def fetch_policy_summary(policy: FetchPolicy) -> dict[str, Any]:
    """JSON-serializable fetch policy snapshot for xarray attrs."""

    return {
        "max_concurrent": policy.max_concurrent,
        "max_connections": policy.max_connections,
        "retries": policy.retries,
        "timeout": policy.timeout,
        "rate_limit_per_second": policy.rate_limit_per_second,
        "adaptive_concurrency": policy.adaptive_concurrency,
        "initial_concurrent": policy.initial_concurrent,
        "min_concurrent": policy.min_concurrent,
        "multiplicative_decrease": policy.multiplicative_decrease,
        "additive_increase": policy.additive_increase,
        "forbidden_circuit_breaker": policy.forbidden_circuit_breaker,
        "forbidden_window_seconds": policy.forbidden_window_seconds,
        "forbidden_threshold": policy.forbidden_threshold,
        "forbidden_cooldown_seconds": policy.forbidden_cooldown_seconds,
        "call_max_concurrent": policy.call_max_concurrent,
    }


def fetch_policy_from_summary(summary: dict[str, Any]) -> FetchPolicy:
    defaults = FetchPolicy()
    call_max = summary.get("call_max_concurrent", defaults.call_max_concurrent)
    return FetchPolicy(
        max_concurrent=int(summary.get("max_concurrent", defaults.max_concurrent)),
        max_connections=summary.get("max_connections", defaults.max_connections),
        retries=int(summary.get("retries", defaults.retries)),
        timeout=float(summary.get("timeout", defaults.timeout)),
        rate_limit_per_second=summary.get(
            "rate_limit_per_second", defaults.rate_limit_per_second
        ),
        adaptive_concurrency=bool(
            summary.get("adaptive_concurrency", defaults.adaptive_concurrency)
        ),
        initial_concurrent=int(
            summary.get("initial_concurrent", defaults.initial_concurrent)
        ),
        min_concurrent=int(summary.get("min_concurrent", defaults.min_concurrent)),
        multiplicative_decrease=float(
            summary.get("multiplicative_decrease", defaults.multiplicative_decrease)
        ),
        additive_increase=int(
            summary.get("additive_increase", defaults.additive_increase)
        ),
        forbidden_circuit_breaker=bool(
            summary.get("forbidden_circuit_breaker", defaults.forbidden_circuit_breaker)
        ),
        forbidden_window_seconds=float(
            summary.get("forbidden_window_seconds", defaults.forbidden_window_seconds)
        ),
        forbidden_threshold=int(
            summary.get("forbidden_threshold", defaults.forbidden_threshold)
        ),
        forbidden_cooldown_seconds=float(
            summary.get(
                "forbidden_cooldown_seconds", defaults.forbidden_cooldown_seconds
            )
        ),
        call_max_concurrent=int(call_max) if call_max is not None else None,
    )


def resolve_fetch_policy(
    service_config: ServiceConfigModel | None,
    service_options: dict[str, Any],
) -> FetchPolicy:
    """Build the fetch policy for :func:`~tilearray.create_array` / preview.

    ``max_concurrent`` on the returned policy is the **shared process-local
    gate ceiling** (host / preset). A lower ``max_concurrent_requests`` override
    on a configured service becomes ``call_max_concurrent`` so one caller's
    local politeness cannot shrink the shared gate for siblings. Raising the
    override (or setting it without a service config) still sets the shared
    ceiling. Separate OS processes do not share this gate.
    """

    base = (
        service_config.fetch_policy() if service_config is not None else FetchPolicy()
    )
    override = service_options.pop("max_concurrent_requests", None)
    call_max_concurrent: int | None = None
    if override is None:
        max_concurrent = base.max_concurrent
    elif service_config is not None and int(override) < base.max_concurrent:
        # Local politeness: keep host/preset ceiling as the shared gate.
        max_concurrent = base.max_concurrent
        call_max_concurrent = int(override)
    else:
        max_concurrent = int(override)

    return FetchPolicy(
        max_concurrent=max_concurrent,
        max_connections=service_options.pop("max_connections", base.max_connections),
        retries=service_options.pop("fetch_retries", base.retries),
        timeout=service_options.pop("fetch_timeout", base.timeout),
        rate_limit_per_second=service_options.pop(
            "rate_limit_per_second", base.rate_limit_per_second
        ),
        rate_limiter=service_options.pop("rate_limiter", base.rate_limiter),
        adaptive_concurrency=service_options.pop(
            "adaptive_concurrency", base.adaptive_concurrency
        ),
        initial_concurrent=service_options.pop(
            "initial_concurrent_requests", base.initial_concurrent
        ),
        min_concurrent=service_options.pop(
            "min_concurrent_requests", base.min_concurrent
        ),
        multiplicative_decrease=service_options.pop(
            "multiplicative_decrease", base.multiplicative_decrease
        ),
        forbidden_circuit_breaker=service_options.pop(
            "forbidden_circuit_breaker", base.forbidden_circuit_breaker
        ),
        forbidden_window_seconds=service_options.pop(
            "forbidden_window_seconds", base.forbidden_window_seconds
        ),
        forbidden_threshold=service_options.pop(
            "forbidden_threshold", base.forbidden_threshold
        ),
        forbidden_cooldown_seconds=service_options.pop(
            "forbidden_cooldown_seconds", base.forbidden_cooldown_seconds
        ),
        call_max_concurrent=call_max_concurrent,
        max_tile_requests=int(
            service_options.pop("max_tile_requests", base.max_tile_requests)
        ),
    )


def organize_tiles(
    tile_requests: Sequence[TileRequest],
    rows: int,
    cols: int,
) -> list[list[TileRequest]]:
    if len(tile_requests) != rows * cols:
        raise ValidationError(
            f"Service produced {len(tile_requests)} tile requests; expected {rows * cols}"
        )

    annotated: list[tuple[TileRequest, BoundingBox]] = []
    for request in tile_requests:
        if request.bbox is None:
            raise ValidationError("TileRequest is missing spatial metadata (bbox)")
        annotated.append((request, request.bbox))

    sorted_tiles = [
        request
        for request, _ in sorted(
            annotated,
            key=lambda item: (-item[1].max_y, item[1].min_x),
        )
    ]

    grid: list[list[TileRequest]] = []
    idx = 0
    for _ in range(rows):
        row_tiles: list[TileRequest] = []
        for _ in range(cols):
            row_tiles.append(sorted_tiles[idx])
            idx += 1
        grid.append(row_tiles)
    return grid


def cache_key(request: TileRequest) -> str:
    payload = {
        "url": request.url,
        "params": sorted((str(k), str(v)) for k, v in request.params.items()),
        "format": request.output_format.value if request.output_format else None,
        "bbox": request.bbox.model_dump() if request.bbox else None,
        "width": request.width,
        "height": request.height,
    }
    serialized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def coerce_array_origin(value: object) -> tuple[float, float]:
    if value is None:
        return (0.0, 0.0)
    if isinstance(value, tuple) and len(value) == 2:
        origin_x = value[0]
        origin_y = value[1]
        return (float(origin_x), float(origin_y))
    raise ValidationError("origin must be a tuple of two numbers")


def _crs_in_supported(crs: CRS, supported: Sequence[CRS | str]) -> bool:
    target = CRS.parse(crs).value
    for item in supported:
        try:
            if CRS.parse(item).value == target:
                return True
        except ValidationError:
            continue
    return False


def _validate_supported_output_crs(
    service: BaseService,
    mosaic_crs: CRS,
    tile_options: dict[str, Any],
) -> None:
    """Reject ``output_crs`` values the service metadata marks as unsupported."""

    explicit = tile_options.get("supported_crs")
    supported: list[CRS | str] = []
    if isinstance(explicit, (list, tuple)):
        supported.extend(explicit)

    if service.service_type == ServiceTypeEnum.WCS:
        coverage = tile_options.get("coverage_id") or getattr(
            service, "coverage_id", None
        )
        meta = None
        if hasattr(service, "coverage_metadata"):
            meta = service.coverage_metadata(coverage)
        if meta is not None:
            supported.extend(getattr(meta, "supported_crs", []) or [])
            native = getattr(meta, "native_crs", None)
            if native is not None:
                supported.append(native)

    if service.service_type == ServiceTypeEnum.WMS and hasattr(
        service, "supported_crs"
    ):
        layer_crs = getattr(service, "supported_crs", None)
        if isinstance(layer_crs, (list, tuple)):
            supported.extend(layer_crs)

    # De-dupe while preserving order for the error message.
    unique: list[CRS | str] = []
    seen: set[str] = set()
    for item in supported:
        try:
            key = CRS.parse(item).value
        except ValidationError:
            key = str(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)

    if unique and not _crs_in_supported(mosaic_crs, unique):
        labels: list[str] = []
        for item in unique[:12]:
            try:
                labels.append(CRS.parse(item).value)
            except ValidationError:
                labels.append(str(item))
        raise ValidationError(
            f"output_crs={mosaic_crs.value} is not supported by this "
            f"{service.service_type.value} service. Supported CRS values include: "
            f"{', '.join(labels)}."
        )


def normalize_bbox(bbox: BoundingBox | BBoxTuple, crs: CRS) -> BoundingBox:
    if isinstance(bbox, BoundingBox):
        bbox_crs = CRS.parse(bbox.crs)
        target = CRS.parse(crs)
        if bbox_crs != target:
            raise ValidationError(
                f"BoundingBox CRS ({bbox_crs.value}) conflicts with bbox_crs="
                f"({target.value}); they must match. Transform the bbox to "
                f"{target.value} yourself, or pass a coordinate tuple with "
                f"bbox_crs=."
            )
        return bbox
    try:
        return BoundingBox.from_tuple(bbox, crs)
    except ValidationError:
        raise
    except Exception as exc:
        # Pydantic / ValueError from zero-area or inverted corners → public boundary.
        raise ValidationError(str(exc), cause=exc) from exc


def validate_grid_shape(grid: tuple[int, int]) -> tuple[int, int]:
    rows, cols = grid
    if rows <= 0 or cols <= 0:
        raise ValidationError("grid_shape must contain positive integers")
    return rows, cols


def validate_chunk_size(chunk_size: tuple[int, int]) -> tuple[int, int]:
    width, height = chunk_size
    if width <= 0 or height <= 0:
        raise ValidationError("chunk_size dimensions must be positive integers")
    return height, width


def resolve_grid_shape(
    explicit: tuple[int, int] | None,
    fallback: tuple[int, int] | None,
) -> tuple[int, int]:
    if explicit is not None:
        return validate_grid_shape(explicit)
    if fallback is not None:
        return validate_grid_shape(fallback)
    return (1, 1)


def recommend_grid_shape(
    bbox: BoundingBox | BBoxTuple,
    *,
    chunk_size: tuple[int, int],
    resolution: tuple[float, float],
    bbox_crs: CRS | str | int | None = None,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[int, int]:
    """Infer ``(rows, cols)`` covering ``bbox`` at ``resolution`` × ``chunk_size``.

    ``chunk_size`` matches :func:`~tilearray.create_array`: ``(width, height)`` in
    pixels. The bbox is snapped onto the resolution grid (count-preserving) before
    counting tiles. Geometry only — no host contact and no client-side pixel
    resampling.
    """

    res_x, res_y = resolution
    if res_x <= 0 or res_y <= 0:
        raise ValidationError("resolution values must be positive")

    if isinstance(bbox, BoundingBox):
        if bbox_crs is None:
            normalized = bbox
        else:
            normalized = normalize_bbox(bbox, CRS.parse(bbox_crs))
    else:
        if bbox_crs is None:
            raise ValidationError(
                "bbox_crs is required when bbox is a coordinate tuple"
            )
        normalized = normalize_bbox(bbox, CRS.parse(bbox_crs))

    chunk_height, chunk_width = validate_chunk_size(chunk_size)
    snapped = snap_bbox_outward(normalized, resolution, origin)
    n_px = max(1, int(round((snapped.max_x - snapped.min_x) / res_x)))
    n_py = max(1, int(round((snapped.max_y - snapped.min_y) / res_y)))
    cols = len(resolution_axis_tile_sizes(n_px, chunk_width))
    rows = len(resolution_axis_tile_sizes(n_py, chunk_height))
    return rows, cols


def _positive_size_pair(value: tuple[int, int], *, label: str) -> tuple[int, int]:
    if len(value) != 2:
        raise ValidationError(f"{label} must be a pair of positive integers")
    a, b = int(value[0]), int(value[1])
    if a <= 0 or b <= 0:
        raise ValidationError(f"{label} must contain positive integers")
    return a, b


class ArrayRequest(BaseModel):
    """Validated mosaic request shared by :func:`create_array` and :func:`preview_array`."""

    service_config: ServiceConfigModel | None = None
    service_url: str
    bbox: BoundingBox
    target_crs: CRS
    bbox_crs: CRS
    chunk_size: tuple[int, int]
    grid_shape: tuple[int, int]
    resolution: tuple[float, float] | None = None
    output_format: Format | None = None
    cache_dir: Path | None = None
    service_options: dict[str, Any] = Field(default_factory=dict)
    requested_bbox: BoundingBox | None = None
    bbox_snap_delta: dict[str, float] | None = None
    resolution_grid_pending: bool = False

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_validator("chunk_size")
    @classmethod
    def _validate_chunk_size_field(cls, value: tuple[int, int]) -> tuple[int, int]:
        # Stored as (height, width) after from_inputs / validate_chunk_size.
        return _positive_size_pair(value, label="chunk_size")

    @field_validator("grid_shape")
    @classmethod
    def _validate_grid_shape_field(cls, value: tuple[int, int]) -> tuple[int, int]:
        return _positive_size_pair(value, label="grid_shape")

    @field_validator("resolution")
    @classmethod
    def _validate_resolution_field(
        cls, value: tuple[float, float] | None
    ) -> tuple[float, float] | None:
        if value is None:
            return None
        if len(value) != 2:
            raise ValidationError("resolution must be a pair of positive numbers")
        res_x, res_y = float(value[0]), float(value[1])
        if res_x <= 0 or res_y <= 0:
            raise ValidationError("resolution values must be positive")
        return res_x, res_y

    @property
    def chunk_height(self) -> int:
        return self.chunk_size[0]

    @property
    def chunk_width(self) -> int:
        return self.chunk_size[1]

    @property
    def chunk_pixels(self) -> tuple[int, int]:
        return self.chunk_width, self.chunk_height

    @property
    def cache_path(self) -> Path | None:
        return self.cache_dir

    @property
    def service_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.service_config is not None:
            return kwargs
        kwargs.update(self.service_options)
        kwargs.setdefault("crs", self.target_crs)
        if self.output_format is not None:
            kwargs.setdefault("output_format", self.output_format)
        return kwargs

    @classmethod
    def from_inputs(
        cls,
        *,
        service_url: str,
        service_config: ServiceConfigModel | None,
        bbox_input: BoundingBox | BBoxTuple,
        bbox_crs_input: CRS | str | int,
        chunk_size_input: tuple[int, int] | None,
        grid_shape_input: tuple[int, int] | None,
        output_format_input: Format | None,
        cache_dir_input: str | Path | None,
        service_options_input: dict[str, Any],
    ) -> ArrayRequest:
        query_crs = CRS.parse(bbox_crs_input)
        normalized_bbox = normalize_bbox(bbox_input, query_crs)

        defaults = service_config.array_defaults() if service_config else {}

        chunk_candidate = chunk_size_input or defaults.get("chunk_size") or (256, 256)
        chunk_height, chunk_width = validate_chunk_size(chunk_candidate)

        user_options = dict(service_options_input)
        user_resolution = user_options.pop("resolution", None)
        resolution = user_resolution or defaults.get("resolution")

        # Snap / recommend_grid_shape run in mosaic CRS after _prepare_crs_roles
        # (#160) — do not apply resolution maths in the query CRS here.
        if resolution is not None:
            res_x, res_y = resolution
            if res_x <= 0 or res_y <= 0:
                raise ValidationError("resolution values must be positive")
            # Validate origin at construction; snap still deferred to mosaic CRS.
            coerce_array_origin(user_options.get("origin"))

        fallback_grid = defaults.get("grid_shape")
        grid = resolve_grid_shape(grid_shape_input, fallback_grid)
        resolution_grid_pending = (
            grid_shape_input is None
            and fallback_grid is None
            and resolution is not None
        )

        cache_candidate = cache_dir_input or defaults.get("cache_dir")
        cache_path = (
            Path(cache_candidate).expanduser().resolve() if cache_candidate else None
        )

        effective_format = output_format_input
        if (
            effective_format is None
            and service_config
            and service_config.output_format is not None
        ):
            effective_format = service_config.output_format

        return cls(
            service_config=service_config,
            service_url=service_url,
            bbox=normalized_bbox,
            target_crs=query_crs,
            bbox_crs=query_crs,
            chunk_size=(chunk_height, chunk_width),
            grid_shape=grid,
            resolution=resolution,
            output_format=effective_format,
            cache_dir=cache_path,
            service_options=user_options,
            requested_bbox=normalized_bbox,
            bbox_snap_delta=None,
            resolution_grid_pending=resolution_grid_pending,
        )

    def build_service(self, service_type: ServiceTypeEnum | None) -> BaseService:
        if self.service_config is not None:
            return self.service_config.build_service()
        return get_service(
            self.service_url, service_type=service_type, **self.service_kwargs
        )

    def effective_format(self, service: BaseService) -> Format | None:
        if self.output_format is not None:
            return self.output_format
        service_format = getattr(service, "output_format", None)
        if isinstance(service_format, Format):
            return service_format
        if isinstance(service_format, str):
            try:
                return Format(service_format)
            except ValueError:
                return None
        return None

    def tile_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self.service_config is not None:
            options.update(self.service_config.tile_kwargs())
        options.update(self.service_options)
        options.setdefault("crs", self.bbox_crs)
        if self.output_format is not None:
            options.setdefault("output_format", self.output_format)
        options["grid_shape"] = self.grid_shape
        if self.resolution is not None:
            options.setdefault("resolution", self.resolution)
        return options

    def _prepare_crs_roles(
        self, service: BaseService, tile_options: dict[str, Any]
    ) -> None:
        """Resolve query / request / output CRS before generating tile requests.

        WMS/WCS may use an explicit server-side ``output_crs`` (never a client
        warp). XYZ/WMTS reject ``output_crs`` — mosaics stay on the native grid.
        """

        output_raw = tile_options.get("output_crs")
        service_type = service.service_type

        if service_type in {ServiceTypeEnum.XYZ, ServiceTypeEnum.WMTS}:
            if output_raw is not None:
                raise ValidationError(
                    "output_crs= is only for WMS/WCS server-side reprojection; "
                    "XYZ/WMTS mosaics stay on the native tile grid "
                    "(no client-side warp)."
                )
            return

        if service_type not in {ServiceTypeEnum.WMS, ServiceTypeEnum.WCS}:
            if output_raw is not None:
                raise ValidationError(
                    "output_crs= is only supported for WMS and WCS services"
                )
            return

        request_crs = CRS.parse(tile_options.get("crs") or self.bbox_crs)
        tile_options["crs"] = request_crs

        if output_raw is None:
            mosaic_crs = request_crs
        else:
            mosaic_crs = CRS.parse(output_raw)
            tile_options["output_crs"] = mosaic_crs
            if service_type == ServiceTypeEnum.WMS:
                # GetMap has one CRS for BBOX and rendered pixels.
                tile_options["crs"] = mosaic_crs
                request_crs = mosaic_crs

        _validate_supported_output_crs(service, mosaic_crs, tile_options)

        if self.requested_bbox is None:
            self.requested_bbox = self.bbox
        if self.bbox.crs != mosaic_crs:
            try:
                self.bbox = self.bbox.to_crs(mosaic_crs)
            except (ValidationError, ValueError, RuntimeError) as exc:
                raise ValidationError(
                    f"Cannot transform query bbox from {self.bbox.crs.value} to "
                    f"output CRS {mosaic_crs.value}: {exc}"
                ) from exc
        self.target_crs = mosaic_crs

    def _apply_mosaic_crs_resolution(self, tile_options: dict[str, Any]) -> None:
        """Snap bbox / infer grid_shape using resolution in the mosaic CRS (#160)."""

        if self.resolution is None:
            self.resolution_grid_pending = False
            return

        origin = coerce_array_origin(self.service_options.get("origin"))
        snapped_bbox = snap_bbox_outward(self.bbox, self.resolution, origin)
        bbox_snap_delta = {
            "min_x": snapped_bbox.min_x - self.bbox.min_x,
            "min_y": snapped_bbox.min_y - self.bbox.min_y,
            "max_x": snapped_bbox.max_x - self.bbox.max_x,
            "max_y": snapped_bbox.max_y - self.bbox.max_y,
        }
        if any(abs(delta) > 0.0 for delta in bbox_snap_delta.values()):
            if self.requested_bbox is None:
                self.requested_bbox = self.bbox
            self.bbox = snapped_bbox
            self.bbox_snap_delta = bbox_snap_delta
        else:
            self.bbox_snap_delta = None

        if self.resolution_grid_pending:
            self.grid_shape = recommend_grid_shape(
                self.bbox,
                chunk_size=(self.chunk_width, self.chunk_height),
                resolution=self.resolution,
                origin=origin,
            )
            self.resolution_grid_pending = False

        tile_options["grid_shape"] = self.grid_shape
        tile_options.setdefault("resolution", self.resolution)

    def plan_tile_requests(
        self,
        service: BaseService,
        *,
        max_tile_requests: int | None = None,
        allow_estimate_only: bool = False,
    ) -> tuple[list[TileRequest], dict[str, Any], int]:
        tile_options = self.tile_options()
        self._prepare_crs_roles(service, tile_options)
        self._apply_mosaic_crs_resolution(tile_options)

        estimate_fn = getattr(service, "estimate_tile_count", None)
        estimated = (
            estimate_fn(self.bbox, self.chunk_pixels, **dict(tile_options))
            if callable(estimate_fn)
            else None
        )
        if (
            max_tile_requests is not None
            and estimated is not None
            and estimated > max_tile_requests
        ):
            if allow_estimate_only:
                return [], tile_options, int(estimated)
            raise ValidationError(
                f"Tile plan of {estimated} requests exceeds max_tile_requests="
                f"{max_tile_requests} (too many tiles for this AOI / zoom); "
                "shrink the bbox, lower zoom, or raise max_tile_requests"
            )

        plan = service.generate_tile_requests(
            self.bbox,
            self.chunk_pixels,
            **dict(tile_options),
        )
        tile_requests = list(plan.tiles)
        request_count = len(tile_requests)

        if max_tile_requests is not None and request_count > max_tile_requests:
            if allow_estimate_only:
                return [], tile_options, request_count
            raise ValidationError(
                f"Tile plan of {request_count} requests exceeds max_tile_requests="
                f"{max_tile_requests} (too many tiles for this AOI / zoom); "
                "shrink the bbox, lower zoom, or raise max_tile_requests"
            )

        if (
            plan.grid_shape is not None
            and self.grid_shape == (1, 1)
            and len(tile_requests) > 1
        ):
            self.grid_shape = plan.grid_shape

        # Fixed grids (metadata WMTS + XYZ / slippy WMTS): mosaic extent/CRS
        # follow the planned native tile union, not the caller's query bbox /
        # query CRS label (#93; XYZ/slippy native 3857).
        if plan.mosaic_bbox is not None:
            if self.requested_bbox is None:
                self.requested_bbox = self.bbox
            self.bbox = plan.mosaic_bbox
            self.target_crs = plan.mosaic_bbox.crs

        return tile_requests, tile_options, request_count

    def array_attrs(
        self,
        service: BaseService,
        tile_options: dict[str, Any],
        effective_format: Format | None,
    ) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "mosaic_crs": self.target_crs.value,
            "bbox_crs": self.bbox_crs.value,
            "service_url": self.service_url,
            "service_type": service.service_type.value,
        }
        if effective_format is not None:
            attrs["output_format"] = effective_format.value
        coverage_id = tile_options.get("coverage_id") or getattr(
            service, "coverage_id", None
        )
        if coverage_id:
            attrs["coverage_id"] = coverage_id
        if self.bbox_snap_delta is not None:
            attrs["bbox_snap_delta"] = dict(self.bbox_snap_delta)
        if self.requested_bbox is not None and (
            self.bbox_snap_delta is not None
            or self.requested_bbox.crs != self.bbox.crs
            or self.requested_bbox.min_x != self.bbox.min_x
            or self.requested_bbox.min_y != self.bbox.min_y
            or self.requested_bbox.max_x != self.bbox.max_x
            or self.requested_bbox.max_y != self.bbox.max_y
        ):
            attrs["bbox_requested"] = {
                "min_x": self.requested_bbox.min_x,
                "min_y": self.requested_bbox.min_y,
                "max_x": self.requested_bbox.max_x,
                "max_y": self.requested_bbox.max_y,
                "crs": self.requested_bbox.crs.value,
            }
        return attrs


def resolve_decoder(
    request: ArrayRequest,
    service: BaseService,
    explicit: TileDecoder | None,
    *,
    band_policy: BandPolicy | None = None,
    expand_palette: bool = False,
) -> TileDecoder:
    if explicit is not None:
        return explicit

    fmt = request.effective_format(service)
    if fmt is not None:
        default_policy = DEFAULT_BAND_POLICIES.get(fmt, "preserve")
        effective_policy = band_policy or default_policy
        # Rebuild when the caller opts into a non-default band/palette policy;
        # otherwise prefer the process registry (tests and custom installs).
        needs_custom = expand_palette or (
            band_policy is not None and band_policy != default_policy
        )
        if needs_custom:
            built = build_format_decoder(
                fmt,
                band_policy=effective_policy,
                expand_palette=expand_palette,
            )
            if built is not None:
                return built
        decoder = _decoder_for_format(fmt)
        if decoder is not None:
            return decoder
        built = build_format_decoder(
            fmt,
            band_policy=effective_policy,
            expand_palette=expand_palette,
        )
        if built is not None:
            return built

    raise ConfigurationError(missing_decoder_message(fmt))


def first_fetchable_tile_request(
    tile_requests: Sequence[TileRequest],
) -> TileRequest:
    """Pick the first tile that needs an HTTP fetch (skip coverage-empty pads).

    Preserve-policy band probes must sample an in-extent tile. When every
    planned tile is ``coverage_empty``, raise :class:`ValidationError`.
    """

    for request in tile_requests:
        if not request.coverage_empty:
            return request
    raise ValidationError(
        "Requested area does not intersect the coverage spatial extent; "
        "no tiles to fetch. Check the coverage envelope from DescribeCoverage "
        "and your bbox / bbox_crs."
    )


def tile_fetch_identity(request: TileRequest) -> tuple[Any, ...]:
    """Stable identity for whether two tile requests are the same fetch.

    Matches the planning fields used by :func:`cache_key`: URL, params, format,
    bbox, and requested pixel size. Shared auth headers are not part of tile
    identity. Including bbox avoids treating distinct planned tiles that share a
    stub URL as interchangeable.
    """

    params = tuple(sorted((str(k), str(v)) for k, v in request.params.items()))
    bbox: tuple[Any, ...] | None = None
    if request.bbox is not None:
        b = request.bbox
        crs_value = str(b.crs) if b.crs is not None else None
        bbox = (b.min_x, b.min_y, b.max_x, b.max_y, crs_value)
    fmt = request.output_format.value if request.output_format else None
    return (request.url, params, fmt, bbox, request.width, request.height)


@dataclass(frozen=True)
class BandProbeResult:
    """Band-count probe outcome; may carry a decoded sample for mosaic reuse."""

    n_bands: int
    array: NDArrayAny | None = None
    request: TileRequest | None = None


def probe_array_fits_tile(
    array: NDArrayAny,
    probe_request: TileRequest,
    tile_request: TileRequest,
    *,
    dtype: np.dtype[Any],
    n_bands: int,
    chunk_height: int,
    chunk_width: int,
) -> bool:
    """True when a probed array can be placed as ``tile_request`` without refetch."""

    if tile_fetch_identity(probe_request) != tile_fetch_identity(tile_request):
        return False
    if array.dtype != dtype:
        return False
    height = tile_request.height or chunk_height
    width = tile_request.width or chunk_width
    expected = (height, width, n_bands) if n_bands > 1 else (height, width)
    return array.shape == expected


def probe_tile_bands(
    sample_request: TileRequest,
    cache_dir: Path | None,
    decoder: TileDecoder,
    dtype: np.dtype[Any],
    fetch_policy: FetchPolicy | None,
    effective_format: Format | None,
    *,
    band_policy: BandPolicy | None = None,
) -> BandProbeResult:
    """Determine band count before building the Dask graph.

    When format policy fixes the band count, no HTTP probe runs. Otherwise the
    sample tile is loaded once; callers may reuse ``array`` for the matching
    mosaic tile when :func:`probe_array_fits_tile` is true.
    """

    fixed = default_band_count_for_format(effective_format, band_policy=band_policy)
    if fixed is not None:
        return BandProbeResult(n_bands=fixed)

    if sample_request.coverage_empty:
        raise ValidationError(
            "Band-count probe cannot use a coverage-empty tile. Pass an "
            "in-extent sample request (or band_policy='first_band')."
        )

    # Circular: probe needs the create-path loader; array imports ArrayRequest here.
    from .array import _load_tile_array  # noqa: PLC0415 — documented cycle

    sample = _load_tile_array(
        sample_request,
        cache_dir,
        decoder,
        dtype,
        fetch_policy,
    )
    return BandProbeResult(
        n_bands=band_count_from_array(sample),
        array=sample,
        request=sample_request,
    )


def probe_tile_band_count(
    sample_request: TileRequest,
    cache_dir: Path | None,
    decoder: TileDecoder,
    dtype: np.dtype[Any],
    fetch_policy: FetchPolicy | None,
    effective_format: Format | None,
    *,
    band_policy: BandPolicy | None = None,
) -> int:
    """Determine band count before building the Dask graph."""

    return probe_tile_bands(
        sample_request,
        cache_dir,
        decoder,
        dtype,
        fetch_policy,
        effective_format,
        band_policy=band_policy,
    ).n_bands


# Historical underscore aliases for array.py / tests.
_resolve_decoder = resolve_decoder
_probe_tile_band_count = probe_tile_band_count
_probe_tile_bands = probe_tile_bands


__all__ = [
    "FETCH_POLICY_ATTR",
    "PROGRESS_REF_ATTR",
    "RECOMMENDED_NUM_WORKERS_ATTR",
    "TILE_ERRORS_ATTR",
    "ArrayRequest",
    "BandProbeResult",
    "cache_key",
    "coerce_array_origin",
    "fetch_policy_from_summary",
    "fetch_policy_summary",
    "first_fetchable_tile_request",
    "normalize_bbox",
    "organize_tiles",
    "probe_array_fits_tile",
    "probe_tile_band_count",
    "probe_tile_bands",
    "recommend_grid_shape",
    "resolve_decoder",
    "resolve_fetch_policy",
    "resolve_grid_shape",
    "tile_fetch_identity",
    "validate_chunk_size",
    "validate_grid_shape",
]
