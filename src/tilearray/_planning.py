"""Shared planning helpers for :mod:`tilearray.array` and :mod:`tilearray.plan`.

Owns attr-key constants, fetch-policy snapshots, tile-grid organisation,
cache keys, and :func:`recommend_grid_shape` so ``plan_array`` does not reach
into private ``array`` symbols for those pieces.

``ArrayRequest``, decoder resolution, and band probing still live on
:mod:`tilearray.array` (they sit on the create / load path). Prefer importing
shared helpers from this module rather than adding new ``array`` privates.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from typing import Any

from .errors import ValidationError
from .fetch import FetchPolicy
from .service.config import ServiceConfig as ServiceConfigModel
from .tiles import snap_bbox_outward
from .types import CRS, BBoxTuple, BoundingBox, TileRequest

# Public mosaic attrs keys (also used by plan / fetch_summary).
FETCH_POLICY_ATTR = "tilearray_fetch_policy"
RECOMMENDED_NUM_WORKERS_ATTR = "tilearray_recommended_num_workers"
TILE_ERRORS_ATTR = "tilearray_tile_errors"
# Internal handle so compute_with_policy can refresh tile_errors after lazy compute.
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
    """Build the fetch policy for :func:`~tilearray.create_array` / plan.

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


def normalize_bbox(bbox: BoundingBox | BBoxTuple, crs: CRS) -> BoundingBox:
    if isinstance(bbox, BoundingBox):
        return bbox if bbox.crs == crs else bbox.to_crs(crs)
    return BoundingBox.from_tuple(bbox, crs)


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
    crs: CRS | str | int | None = None,
    origin: tuple[float, float] = (0.0, 0.0),
) -> tuple[int, int]:
    """Infer ``(rows, cols)`` covering ``bbox`` at ``resolution`` × ``chunk_size``.

    ``chunk_size`` matches :func:`~tilearray.create_array`: ``(width, height)`` in
    pixels. The bbox is snapped outward onto the resolution grid before counting
    tiles. Geometry only — no host contact and no client-side pixel resampling.
    """

    res_x, res_y = resolution
    if res_x <= 0 or res_y <= 0:
        raise ValidationError("resolution values must be positive")

    if isinstance(bbox, BoundingBox):
        if crs is None:
            normalized = bbox
        else:
            target_crs = CRS.parse(crs)
            normalized = bbox if bbox.crs == target_crs else bbox.to_crs(target_crs)
    else:
        if crs is None:
            raise ValidationError("crs is required when bbox is a coordinate tuple")
        normalized = normalize_bbox(bbox, CRS.parse(crs))

    chunk_height, chunk_width = validate_chunk_size(chunk_size)
    snapped = snap_bbox_outward(normalized, resolution, origin)
    span_x = snapped.max_x - snapped.min_x
    span_y = snapped.max_y - snapped.min_y
    cols = (
        max(1, int(math.ceil(span_x / (chunk_width * res_x)))) if chunk_width > 0 else 1
    )
    rows = (
        max(1, int(math.ceil(span_y / (chunk_height * res_y))))
        if chunk_height > 0
        else 1
    )
    return rows, cols


__all__ = [
    "FETCH_POLICY_ATTR",
    "PROGRESS_REF_ATTR",
    "RECOMMENDED_NUM_WORKERS_ATTR",
    "TILE_ERRORS_ATTR",
    "cache_key",
    "coerce_array_origin",
    "fetch_policy_from_summary",
    "fetch_policy_summary",
    "normalize_bbox",
    "organize_tiles",
    "recommend_grid_shape",
    "resolve_fetch_policy",
    "resolve_grid_shape",
    "validate_chunk_size",
    "validate_grid_shape",
]
