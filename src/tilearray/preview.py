"""AOI preview helpers and fetch summaries (#61).

Read-only preflight for large mosaics: inspect the planned grid and
concurrency ceiling before ``create_array`` / ``compute_mosaic``.
Does not invent wall-clock time, provider cost, or a second compute path.

Shared request / policy / grid / cache helpers live in
:mod:`tilearray._planning`; the create/preview preamble is
:func:`tilearray._entry.from_public_args`. This module imports nothing
private from :mod:`tilearray.array`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import xarray as xr

from ._entry import from_public_args
from ._planning import (
    FETCH_POLICY_ATTR,
    PROGRESS_REF_ATTR,
    TILE_ERRORS_ATTR,
    cache_key,
    fetch_policy_from_summary,
    fetch_policy_summary,
    organize_tiles,
    probe_tile_band_count,
    recommend_grid_shape,
    resolve_decoder,
)
from .decode import default_band_count_for_format
from .errors import ValidationError
from .fetch import TileFetcher, lookup_fetch_progress
from .matrix_resolution import (
    MatrixChoice,
    MatrixLevelSummary,
    choose_matrix_for_resolution,
)
from .service.config import ServiceConfig as ServiceConfigModel
from .types import (
    CRS,
    BBoxTuple,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
)

# Re-export for ``from tilearray.preview import recommend_grid_shape``.
__all__ = [
    "ArrayPreview",
    "FetchSummary",
    "MatrixChoice",
    "MatrixLevelSummary",
    "choose_matrix_for_resolution",
    "fetch_summary",
    "preview_array",
    "recommend_grid_shape",
]

BandsSource = Literal["format_policy", "probe", "unknown"]


@dataclass(frozen=True)
class ArrayPreview:
    """Read-only job preview produced by :func:`preview_array`."""

    requested_bbox: BoundingBox
    planned_bbox: BoundingBox
    bbox_snap_delta: dict[str, float] | None
    mosaic_crs: str
    bbox_crs: str
    chunk_size: tuple[int, int]
    grid_shape: tuple[int, int]
    resolution: tuple[float, float] | None
    planned_pixel_size: tuple[float, float] | None
    matrix_id: str | None
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
            "tilearray preview (preflight — no mosaic pixel fetch"
            + ("; band probe ran)" if self.bands_source == "probe" else ")"),
            (
                f"  service: {self.service_type}"
                + (f"  format: {self.output_format}" if self.output_format else "")
            ),
            f"  bbox_crs (query): {self.bbox_crs}",
            f"  mosaic_crs: {self.mosaic_crs}",
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
        if self.matrix_id is not None:
            lines.append(f"  matrix / zoom: {self.matrix_id}")
        if self.planned_pixel_size is not None:
            lines.append(
                f"  planned pixel size (CRS units/px): {self.planned_pixel_size}"
            )
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


def preview_array(
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
) -> ArrayPreview:
    """Preview the mosaic job without building a mosaic or fetching pixels.

    Mirrors the planning inputs of :func:`~tilearray.create_array`
    (including ``bbox_crs`` and optional WMS/WCS ``output_crs``). Capabilities /
    DescribeCoverage may still run for WCS (same as today). Mosaic tile bytes
    are not fetched unless ``probe_bands=True`` (opt-in band discovery).

    Use this to inspect grid size, concurrency ceiling, and cache warmth before
    ``create_array`` → ``compute_mosaic``.
    """

    entry = from_public_args(
        service_url,
        bbox,
        bbox_crs,
        service_type=service_type,
        chunk_size=chunk_size,
        grid_shape=grid_shape,
        output_format=output_format,
        cache_dir=cache_dir,
        output_crs=output_crs,
        materialize_tiles=False,
        **service_options,
    )
    request = entry.request
    service = entry.service
    fetch_policy = entry.fetch_policy
    tile_requests = list(entry.tile_requests)
    tile_options = dict(entry.tile_options)
    options = dict(request.service_options)
    chunk_height, chunk_width = request.chunk_size
    dtype_np = np.dtype(dtype)
    effective_format = request.effective_format(service)

    service_type_value = getattr(service, "service_type", None)
    service_type_str = (
        service_type_value.value
        if isinstance(service_type_value, ServiceTypeEnum)
        else str(service_type_value or "unknown")
    )
    requested = request.requested_bbox or request.bbox
    format_value = effective_format.value if effective_format is not None else None

    # Estimate-only path (#161): huge plans skip materialising TileRequests.
    if not tile_requests and entry.request_count > 0:
        return ArrayPreview(
            requested_bbox=requested,
            planned_bbox=request.bbox,
            bbox_snap_delta=request.bbox_snap_delta,
            mosaic_crs=request.target_crs.value,
            bbox_crs=request.bbox_crs.value,
            chunk_size=(chunk_width, chunk_height),
            grid_shape=request.grid_shape,
            resolution=request.resolution,
            planned_pixel_size=None,
            matrix_id=_matrix_or_zoom_id(service, tile_options),
            pixel_height=0,
            pixel_width=0,
            request_count=entry.request_count,
            dtype=str(dtype_np),
            bands=None,
            bands_source="unknown",
            estimated_nbytes=None,
            cache_dir=request.cache_path,
            cached_tiles=None,
            fetch_policy=fetch_policy_summary(fetch_policy),
            service_type=service_type_str,
            output_format=format_value,
            unknowns=(
                "pixel shape (tile requests not materialised for this estimate)",
                "in-memory array bytes (depends on band count and pixel shape)",
            ),
            notes=(
                "request_count is an O(1) estimate; tile_requests were not "
                "materialised because the plan exceeds max_tile_requests",
                "estimates exclude HTTP overhead, retries, and decode peaks; "
                "no transfer time or provider cost is claimed",
            ),
            tile_requests=(),
        )

    rows, cols = request.grid_shape
    tile_grid = organize_tiles(tile_requests, rows, cols)
    pixel_height, pixel_width = _mosaic_pixel_size(
        tile_grid, chunk_height=chunk_height, chunk_width=chunk_width
    )

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

    if request.bbox_snap_delta is not None:
        notes.append(
            "planned bbox was snapped onto the resolution grid "
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

    matrix_id = _matrix_or_zoom_id(service, tile_options)
    planned_pixel_size = _planned_pixel_size_from_extent(
        planned_bbox=request.bbox,
        pixel_height=pixel_height,
        pixel_width=pixel_width,
    )
    if planned_pixel_size is not None:
        notes.append(
            "planned_pixel_size is derived from the planned mosaic extent ÷ "
            "pixel shape (CRS units per pixel; capabilities-confirmed when WMTS "
            "matrix metadata drove the plan; estimated from the tile union "
            "otherwise — not ground metres for Web Mercator)"
        )

    return ArrayPreview(
        requested_bbox=requested,
        planned_bbox=request.bbox,
        bbox_snap_delta=request.bbox_snap_delta,
        mosaic_crs=request.target_crs.value,
        bbox_crs=request.bbox_crs.value,
        chunk_size=(chunk_width, chunk_height),
        grid_shape=request.grid_shape,
        resolution=request.resolution,
        planned_pixel_size=planned_pixel_size,
        matrix_id=matrix_id,
        pixel_height=pixel_height,
        pixel_width=pixel_width,
        request_count=entry.request_count or len(tile_requests),
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

    Reads design-time attrs and, when still present, the live
    :class:`~tilearray.fetch.FetchProgress` handle. AIMD / pressure counters come
    from the shared process-local fetch gate for the array's
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

    progress = lookup_fetch_progress(attrs.get(PROGRESS_REF_ATTR))
    if progress is not None:
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


def _matrix_or_zoom_id(service: Any, tile_options: dict[str, Any]) -> str | None:
    """WMTS TileMatrix id or XYZ zoom when the service exposes one."""

    for key in ("tile_matrix", "zoom"):
        if key in tile_options and tile_options[key] is not None:
            return str(tile_options[key])
    for attr in ("tile_matrix", "zoom"):
        value = getattr(service, attr, None)
        if value is not None:
            return str(value)
    return None


def _planned_pixel_size_from_extent(
    *,
    planned_bbox: BoundingBox,
    pixel_height: int,
    pixel_width: int,
) -> tuple[float, float] | None:
    """CRS units per pixel from planned extent ÷ mosaic shape (x, y)."""

    if pixel_height <= 0 or pixel_width <= 0:
        return None
    span_x = planned_bbox.max_x - planned_bbox.min_x
    span_y = planned_bbox.max_y - planned_bbox.min_y
    if span_x <= 0 or span_y <= 0:
        return None
    return (span_x / pixel_width, span_y / pixel_height)
