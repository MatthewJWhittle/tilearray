"""AOI planning helpers and fetch summaries (#61).

Read-only preflight for large mosaics: inspect the planned grid and
concurrency ceiling before ``create_array`` / ``compute_with_policy``.
Does not invent wall-clock time, provider cost, or a second compute path.

Shared request / policy / grid / cache helpers live in
:mod:`tilearray._planning` — this module imports nothing private from
:mod:`tilearray.array`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import xarray as xr

from ._planning import (
    FETCH_POLICY_ATTR,
    PROGRESS_REF_ATTR,
    TILE_ERRORS_ATTR,
    ArrayRequest,
    cache_key,
    fetch_policy_from_summary,
    fetch_policy_summary,
    organize_tiles,
    probe_tile_band_count,
    recommend_grid_shape,
    resolve_decoder,
    resolve_fetch_policy,
)
from .decode import default_band_count_for_format
from .errors import ValidationError
from .fetch import FetchProgress, TileFetcher
from .service.config import ServiceConfig as ServiceConfigModel
from .types import (
    CRS,
    BBoxTuple,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
)

# Re-export for ``from tilearray.plan import recommend_grid_shape``.
__all__ = [
    "ArrayPlan",
    "FetchSummary",
    "fetch_summary",
    "plan_array",
    "recommend_grid_shape",
]

BandsSource = Literal["format_policy", "probe", "unknown"]


@dataclass(frozen=True)
class ArrayPlan:
    """Read-only request plan produced by :func:`plan_array`."""

    requested_bbox: BoundingBox
    planned_bbox: BoundingBox
    bbox_snap_delta: dict[str, float] | None
    crs: str
    bbox_crs: str
    chunk_size: tuple[int, int]
    grid_shape: tuple[int, int]
    resolution: tuple[float, float] | None
    pixel_height: int
    pixel_width: int
    request_count: int
    dtype: str
    bands: int | None
    bands_source: BandsSource
    estimated_nbytes: int | None
    cache_dir: Path | None
    cached_tiles: int | None
    fetch_policy: dict[str, Any]
    service_type: str
    output_format: str | None
    unknowns: tuple[str, ...]
    notes: tuple[str, ...]
    tile_requests: tuple[TileRequest, ...]

    def summary(self) -> str:
        """Human-readable preflight text (estimated vs confirmed called out)."""

        lines: list[str] = [
            "tilearray plan (preflight — no mosaic pixel fetch"
            + ("; band probe ran)" if self.bands_source == "probe" else ")"),
            (
                f"  service: {self.service_type}"
                + (f"  format: {self.output_format}" if self.output_format else "")
            ),
            f"  bbox_crs (query): {self.bbox_crs}",
            f"  crs (output): {self.crs}",
            (
                "  requested bbox: "
                f"({self.requested_bbox.min_x}, {self.requested_bbox.min_y}, "
                f"{self.requested_bbox.max_x}, {self.requested_bbox.max_y})"
            ),
            (
                "  planned bbox: "
                f"({self.planned_bbox.min_x}, {self.planned_bbox.min_y}, "
                f"{self.planned_bbox.max_x}, {self.planned_bbox.max_y})"
            ),
        ]
        if self.bbox_snap_delta is not None:
            lines.append(f"  bbox snap delta: {self.bbox_snap_delta}")
        if self.resolution is not None:
            lines.append(f"  resolution: {self.resolution}")
        lines.extend(
            [
                (
                    f"  grid: {self.grid_shape[0]}×{self.grid_shape[1]} tiles "
                    f"(chunk {self.chunk_size[0]}×{self.chunk_size[1]} px) → "
                    f"{self.request_count} request(s)"
                ),
                f"  mosaic pixels: {self.pixel_height}×{self.pixel_width}",
                f"  dtype: {self.dtype} ({_bands_phrase(self.bands, self.bands_source)})",
            ]
        )
        if self.estimated_nbytes is not None:
            lines.append(
                f"  estimated array bytes: ~{self.estimated_nbytes}"
                " (dtype × pixels × bands; not transfer size)"
            )
        else:
            lines.append(
                "  estimated array bytes: unknown "
                "(band count not confirmed — not invented)"
            )
        policy = self.fetch_policy
        ceiling = policy.get("max_concurrent")
        call_local = policy.get("call_max_concurrent")
        lines.append(
            f"  concurrency ceiling: max_concurrent={ceiling}"
            + (f", call_max_concurrent={call_local}" if call_local is not None else "")
        )
        if self.cache_dir is None:
            lines.append("  cache: disabled")
        else:
            warm = self.cached_tiles if self.cached_tiles is not None else 0
            cold = self.request_count - warm
            lines.append(
                f"  cache: {self.cache_dir} — {warm} warm / {cold} cold "
                f"of {self.request_count}"
            )
        if self.unknowns:
            lines.append("  unknowns: " + "; ".join(self.unknowns))
        if self.notes:
            lines.append("  notes:")
            lines.extend(f"    - {note}" for note in self.notes)
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary()


@dataclass(frozen=True)
class FetchSummary:
    """Observability snapshot after planning or during/after compute."""

    total: int | None
    done: int | None
    soft_failed: int | None
    aborted: bool | None
    errors: tuple[str, ...]
    request_count: int | None
    retry_count: int | None
    max_inflight: int | None
    current_limit: int | None
    pressure_event_count: int | None
    circuit_breaker_trips: int | None
    circuit_breaker_frozen: bool | None
    unknowns: tuple[str, ...]

    def summary(self) -> str:
        """Human-readable fetch report; missing fields stay explicit."""

        lines = ["tilearray fetch summary"]
        if self.total is not None and self.done is not None:
            lines.append(f"  tiles: {self.done}/{self.total} completed")
        else:
            lines.append("  tiles: unknown (no FetchProgress on array)")
        if self.soft_failed is not None:
            lines.append(f"  soft-failed: {self.soft_failed}")
        if self.aborted is not None:
            lines.append(f"  aborted: {self.aborted}")
        if self.errors:
            lines.append(f"  recent errors ({len(self.errors)}):")
            for err in self.errors[:5]:
                lines.append(f"    - {err}")
        stats_bits = [
            ("HTTP requests", self.request_count),
            ("retries", self.retry_count),
            ("peak in-flight", self.max_inflight),
            ("current AIMD limit", self.current_limit),
            ("pressure events", self.pressure_event_count),
            ("breaker trips", self.circuit_breaker_trips),
        ]
        known_stats = [
            (label, value) for label, value in stats_bits if value is not None
        ]
        if known_stats:
            lines.append(
                "  fetcher stats: "
                + ", ".join(f"{label}={value}" for label, value in known_stats)
            )
            if self.circuit_breaker_frozen is not None:
                lines.append(f"  circuit breaker frozen: {self.circuit_breaker_frozen}")
        if self.unknowns:
            lines.append("  unknowns: " + "; ".join(self.unknowns))
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary()


def plan_array(
    service_url: str | ServiceConfigModel,
    bbox: BoundingBox | BBoxTuple,
    bbox_crs: CRS | str | int,
    *,
    service_type: ServiceTypeEnum | None = None,
    chunk_size: tuple[int, int] | None = None,
    grid_shape: tuple[int, int] | None = None,
    output_format: Format | None = None,
    cache_dir: str | Path | None = None,
    dtype: str | np.dtype[Any] = np.dtype("float32"),
    probe_bands: bool = False,
    output_crs: CRS | str | int | None = None,
    **service_options: Any,
) -> ArrayPlan:
    """Plan tile requests without building a mosaic or fetching pixels.

    Mirrors the planning inputs of :func:`~tilearray.create_array`
    (including ``bbox_crs`` and optional WMS/WCS ``output_crs``). Capabilities /
    DescribeCoverage may still run for WCS (same as today). Mosaic tile bytes
    are not fetched unless ``probe_bands=True`` (opt-in band discovery).

    Use this to inspect grid size, concurrency ceiling, and cache warmth before
    ``create_array`` → ``compute_with_policy``.
    """

    service_config = (
        service_url if isinstance(service_url, ServiceConfigModel) else None
    )
    base_service_url = service_config.base_url if service_config else str(service_url)

    if (
        service_config
        and service_type is not None
        and service_type != service_config.service_type
    ):
        raise ValidationError(
            "Provided service_type does not match the ServiceConfig service_type"
        )

    options = dict(service_options)
    if "crs" in options:
        raise ValidationError(
            "plan_array no longer accepts crs=; pass bbox_crs= for the query "
            "CRS (service config crs= for request defaults is unchanged)."
        )
    if output_crs is not None:
        if "output_crs" in options:
            raise ValidationError(
                "pass output_crs= once (named argument or service option, not both)"
            )
        options["output_crs"] = output_crs
    fetch_policy = resolve_fetch_policy(service_config, options)

    request = ArrayRequest.from_inputs(
        service_url=base_service_url,
        service_config=service_config,
        bbox_input=bbox,
        bbox_crs_input=bbox_crs,
        chunk_size_input=chunk_size,
        grid_shape_input=grid_shape,
        output_format_input=output_format,
        cache_dir_input=cache_dir,
        service_options_input=options,
    )

    service = request.build_service(service_type)
    tile_requests, _tile_options = request.plan_tile_requests(service)
    if not tile_requests:
        raise ValidationError("Planning produced no tile requests for this AOI")

    rows, cols = request.grid_shape
    tile_grid = organize_tiles(tile_requests, rows, cols)
    chunk_height, chunk_width = request.chunk_size
    pixel_height, pixel_width = _mosaic_pixel_size(
        tile_grid, chunk_height=chunk_height, chunk_width=chunk_width
    )

    dtype_np = np.dtype(dtype)
    effective_format = request.effective_format(service)
    bands: int | None
    bands_source: BandsSource
    unknowns: list[str] = []
    notes: list[str] = []

    band_policy = options.get("band_policy")
    fixed_bands = default_band_count_for_format(
        effective_format, band_policy=band_policy
    )
    if probe_bands:
        decoder = resolve_decoder(request, service, None)
        bands = probe_tile_band_count(
            tile_requests[0],
            request.cache_path,
            decoder,
            dtype_np,
            fetch_policy,
            effective_format,
        )
        bands_source = "probe"
    elif fixed_bands is not None:
        bands = fixed_bands
        bands_source = "format_policy"
    else:
        bands = None
        bands_source = "unknown"
        unknowns.append(
            "band count (preserve-policy formats need probe_bands=True "
            "or create_array; pass band_policy='first_band' for a fixed 1)"
        )

    estimated_nbytes: int | None
    if bands is not None:
        estimated_nbytes = int(pixel_height * pixel_width * bands * dtype_np.itemsize)
    else:
        estimated_nbytes = None
        unknowns.append("in-memory array bytes (depends on band count)")

    cache_path = request.cache_path
    cached_tiles: int | None
    if cache_path is None:
        cached_tiles = None
    else:
        cached_tiles = sum(
            1 for tile in tile_requests if _cache_tile_exists(cache_path, tile)
        )

    service_type_value = getattr(service, "service_type", None)
    service_type_str = (
        service_type_value.value
        if isinstance(service_type_value, ServiceTypeEnum)
        else str(service_type_value or "unknown")
    )

    requested = request.requested_bbox or request.bbox
    if request.bbox_snap_delta is not None:
        notes.append(
            "planned bbox was snapped outward onto the resolution grid "
            "(requested extent preserved in requested_bbox)"
        )
    if service_type_str in {ServiceTypeEnum.XYZ.value, ServiceTypeEnum.WMTS.value}:
        notes.append(
            "XYZ/WMTS uses the native zoom / tile matrix; tilearray does not "
            "resample pixels to an arbitrary client resolution"
        )
    if request.resolution is not None and service_type_str == ServiceTypeEnum.WCS.value:
        notes.append(
            "WCS resolution= sizes GetCoverage requests; the server may resample "
            "— this is not a client-side warp"
        )
    if (
        request.grid_shape == (1, 1)
        and request.resolution is None
        and len(tile_requests) == 1
    ):
        notes.append(
            "single-request plan (default grid_shape); pass resolution= or "
            "grid_shape= / rely on XYZ·WMTS inference to tile a large AOI"
        )
    notes.append(
        "estimates exclude HTTP overhead, retries, and decode peaks; "
        "no transfer time or provider cost is claimed"
    )

    format_value = effective_format.value if effective_format is not None else None

    return ArrayPlan(
        requested_bbox=requested,
        planned_bbox=request.bbox,
        bbox_snap_delta=request.bbox_snap_delta,
        crs=request.target_crs.value,
        bbox_crs=request.bbox_crs.value,
        chunk_size=(chunk_width, chunk_height),
        grid_shape=request.grid_shape,
        resolution=request.resolution,
        pixel_height=pixel_height,
        pixel_width=pixel_width,
        request_count=len(tile_requests),
        dtype=str(dtype_np),
        bands=bands,
        bands_source=bands_source,
        estimated_nbytes=estimated_nbytes,
        cache_dir=cache_path,
        cached_tiles=cached_tiles,
        fetch_policy=fetch_policy_summary(fetch_policy),
        service_type=service_type_str,
        output_format=format_value,
        unknowns=tuple(unknowns),
        notes=tuple(notes),
        tile_requests=tuple(tile_requests),
    )


def fetch_summary(array: xr.DataArray) -> FetchSummary:
    """Summarise fetch progress / pressure for a mosaic ``DataArray``.

    Reads plan-time attrs and, when still present, the live
    :class:`~tilearray.fetch.FetchProgress` handle. Fetcher AIMD counters come
    from the shared :class:`~tilearray.fetch.TileFetcher` for the array's
    ``tilearray_fetch_policy`` when one has been constructed. Missing pieces
    stay ``None`` / listed under ``unknowns`` — never invented.
    """

    attrs = dict(getattr(array, "attrs", {}) or {})
    unknowns: list[str] = []
    errors: list[str] = []

    total: int | None = None
    done: int | None = None
    soft_failed: int | None = None
    aborted: bool | None = None

    progress = attrs.get(PROGRESS_REF_ATTR)
    if isinstance(progress, FetchProgress):
        total = progress.total
        done = progress.done
        soft_failed = progress.soft_failed_count()
        aborted = progress.is_aborted()
        errors.extend(progress.errors[:5])
    else:
        tile_errors = attrs.get(TILE_ERRORS_ATTR)
        if isinstance(tile_errors, dict):
            soft_raw = tile_errors.get("soft_failed")
            soft_failed = int(soft_raw) if soft_raw is not None else None
            aborted_raw = tile_errors.get("aborted")
            aborted = bool(aborted_raw) if aborted_raw is not None else None
            examples = tile_errors.get("examples")
            if isinstance(examples, list):
                errors.extend(str(item) for item in examples[:5])
            unknowns.append(
                "live done/total (progress handle cleared after eager compute)"
            )
        else:
            unknowns.append("FetchProgress / tilearray_tile_errors attrs")

    request_count: int | None = None
    retry_count: int | None = None
    max_inflight: int | None = None
    current_limit: int | None = None
    pressure_event_count: int | None = None
    circuit_breaker_trips: int | None = None
    circuit_breaker_frozen: bool | None = None

    policy_summary = attrs.get(FETCH_POLICY_ATTR)
    if isinstance(policy_summary, dict):
        policy = fetch_policy_from_summary(policy_summary)
        fetcher = TileFetcher.get_if_cached(policy)
        if fetcher is not None:
            stats = fetcher.stats
            request_count = stats.request_count
            retry_count = stats.retry_count
            max_inflight = stats.max_inflight
            current_limit = stats.current_limit or None
            pressure_event_count = stats.pressure_event_count
            circuit_breaker_trips = stats.circuit_breaker_trips
            circuit_breaker_frozen = stats.circuit_breaker_frozen
        else:
            unknowns.append(
                "TileFetcher stats (no shared fetcher instance yet for this policy)"
            )
    else:
        unknowns.append("tilearray_fetch_policy attrs")

    return FetchSummary(
        total=total,
        done=done,
        soft_failed=soft_failed,
        aborted=aborted,
        errors=tuple(errors),
        request_count=request_count,
        retry_count=retry_count,
        max_inflight=max_inflight,
        current_limit=current_limit,
        pressure_event_count=pressure_event_count,
        circuit_breaker_trips=circuit_breaker_trips,
        circuit_breaker_frozen=circuit_breaker_frozen,
        unknowns=tuple(unknowns),
    )


def _mosaic_pixel_size(
    tile_grid: list[list[TileRequest]],
    *,
    chunk_height: int,
    chunk_width: int,
) -> tuple[int, int]:
    if not tile_grid or not tile_grid[0]:
        raise ValidationError("empty tile grid")
    height = sum(int(row[0].height or chunk_height) for row in tile_grid)
    width = sum(int(tile.width or chunk_width) for tile in tile_grid[0])
    return height, width


def _cache_tile_exists(cache_dir: Path, request: TileRequest) -> bool:
    return (cache_dir / f"{cache_key(request)}.tile").exists()


def _bands_phrase(bands: int | None, source: BandsSource) -> str:
    if bands is None:
        return "bands unknown"
    if source == "format_policy":
        return f"{bands} band(s) from format policy"
    if source == "probe":
        return f"{bands} band(s) from probe"
    return f"{bands} band(s)"
