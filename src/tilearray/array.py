# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

"""High-level array construction utilities built on service abstractions."""

from __future__ import annotations

import json
import os
import warnings
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    cast,
)

import numpy as np
import xarray as xr
from dask.array import block as da_block
from dask.array import concatenate as da_concatenate
from dask.array import from_delayed as da_from_delayed
from dask.delayed import Delayed, delayed

from ._planning import (
    FETCH_POLICY_ATTR as _FETCH_POLICY_ATTR,
)
from ._planning import (
    PROGRESS_REF_ATTR as _PROGRESS_REF_ATTR,
)
from ._planning import (
    RECOMMENDED_NUM_WORKERS_ATTR as _RECOMMENDED_NUM_WORKERS_ATTR,
)
from ._planning import (
    TILE_ERRORS_ATTR as _TILE_ERRORS_ATTR,
)
from ._planning import (
    ArrayRequest,
)
from ._planning import (
    cache_key as _cache_key,
)
from ._planning import (
    fetch_policy_from_summary as _fetch_policy_from_summary,
)
from ._planning import (
    fetch_policy_summary as _fetch_policy_summary,
)
from ._planning import (
    organize_tiles as _organize_tiles,
)
from ._planning import (
    probe_tile_band_count as _probe_tile_band_count,
)
from ._planning import (
    resolve_decoder as _resolve_decoder,
)
from ._planning import (
    resolve_fetch_policy as _resolve_fetch_policy,
)
from .decode import (
    DEFAULT_BAND_POLICIES,
    BandPolicy,
    NDArrayFloat,
    TileDecoder,
    sniff_effective_content_type,
)
from .error_messages import tile_fetch_failure_message
from .errors import NetworkError, ValidationError
from .fetch import FetchPolicy, FetchProgress, ProgressCallback
from .placement import fit_tile_to_request
from .progress import resolve_on_progress
from .service.config import ServiceConfig as ServiceConfigModel
from .tile_errors import (
    TileErrorAction,
    TileErrorContext,
    TileErrorHandler,
    soft_fail_exhausted_tiles,
)
from .tiles import fetch_tile
from .types import (
    CRS,
    BBoxTuple,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

if TYPE_CHECKING:
    from dask.array.core import Array as DaskArray
else:  # pragma: no cover - typing aid
    DaskArray = Any


def _delayed_call(func: Callable[..., Any], *args: Any) -> Delayed:
    """Typed helper around ``dask.delayed`` to satisfy static analysis."""

    return cast(Delayed, delayed(func)(*args))


def _attrs_from_compute_target(obj: Any) -> dict[str, Any] | None:
    attrs = getattr(obj, "attrs", None)
    if isinstance(attrs, dict):
        return attrs
    return None


def _policy_from_attrs_or_default(obj: Any) -> FetchPolicy:
    attrs = _attrs_from_compute_target(obj)
    if attrs is not None:
        summary = attrs.get(_FETCH_POLICY_ATTR)
        if isinstance(summary, dict):
            return _fetch_policy_from_summary(summary)
    return FetchPolicy()


def _resolve_fetch_policy_for_compute(
    obj: Any,
    *,
    fetch_policy: FetchPolicy | None = None,
) -> FetchPolicy:
    if fetch_policy is not None:
        return fetch_policy
    return _policy_from_attrs_or_default(obj)


def compute_thread_pool_size(
    fetch_policy: FetchPolicy,
    *,
    override: int | None = None,
) -> int:
    """
    Thread-pool size for Dask's ``threads`` scheduler when computing tile arrays.

    Dask defaults to ``cpu_count()`` workers, which can cap in-flight tile fetches
    below :attr:`~tilearray.fetch.FetchPolicy.max_concurrent`. Pass the result as
    ``num_workers`` when calling :meth:`xarray.DataArray.compute` manually.
    """

    if override is not None:
        return max(1, override)
    cpu_count = os.cpu_count() or 1
    return max(cpu_count, fetch_policy.max_concurrent)


def compute_mosaic(
    obj: Any,
    fetch_policy: FetchPolicy | None = None,
    *,
    num_workers: int | None = None,
    **compute_kwargs: Any,
) -> Any:
    """
    Compute a lazy mosaic with fetch-aligned Dask thread workers.

    Prefer this over plain ``.compute()``. Dask's default ``num_workers=cpu_count()``
    can cap in-flight tile fetches below
    :attr:`~tilearray.fetch.FetchPolicy.max_concurrent` when AIMD allows more
    parallel HTTP. This helper sizes ``num_workers`` from the fetch policy
    (override with ``num_workers=``).

    Always uses ``scheduler="threads"`` so tile loads share the **process-local**
    fetch gate. Passing another ``scheduler=`` in ``compute_kwargs`` is ignored
    with a :class:`UserWarning` — multi-process / distributed schedulers do not
    share one host ceiling.

    Policy resolution: explicit ``fetch_policy``; else ``tilearray_fetch_policy``
    on ``obj.attrs`` (set by :func:`create_array`); else
    :class:`~tilearray.fetch.FetchPolicy` defaults. The HTTP host ceiling was
    fixed at :func:`create_array` time — pass ``num_workers=`` only to size
    Dask threads, not to change the fetch gate.

    After compute, refreshes ``attrs["tilearray_tile_errors"]`` from the mosaic's
    :class:`~tilearray.fetch.FetchProgress` when present (same counters as the
    ``create_array(..., compute=True)`` path).
    """

    if "max_concurrent" in compute_kwargs:
        raise ValidationError(
            "compute_mosaic no longer accepts max_concurrent=; pass "
            "num_workers= to size Dask thread workers. Host ceilings stay on "
            "create_array / FetchPolicy / max_concurrent_requests."
        )

    policy = _resolve_fetch_policy_for_compute(obj, fetch_policy=fetch_policy)

    if num_workers is None and fetch_policy is None:
        attrs = _attrs_from_compute_target(obj)
        if attrs is not None:
            recommended = attrs.get(_RECOMMENDED_NUM_WORKERS_ATTR)
            if recommended is not None:
                num_workers = int(recommended)

    workers = compute_thread_pool_size(policy, override=num_workers)
    requested_scheduler = compute_kwargs.get("scheduler")
    if requested_scheduler is not None and requested_scheduler != "threads":
        warnings.warn(
            "compute_mosaic ignores scheduler="
            f"{requested_scheduler!r} and uses scheduler='threads' so the "
            "process-local fetch gate can keep host ceilings polite. "
            "Multi-process / distributed workers do not share that gate.",
            UserWarning,
            stacklevel=2,
        )
    merged = dict(compute_kwargs)
    merged["scheduler"] = "threads"
    merged["num_workers"] = workers

    # Capture before compute: result attrs are a copy; progress is shared in the graph.
    progress = _progress_from_attrs(obj)
    result = obj.compute(**merged)

    if progress is not None:
        # Preferred lazy path: refresh soft-fail / tile-error observability (#73).
        for target in (result, obj):
            target_attrs = _attrs_from_compute_target(target)
            if target_attrs is None:
                continue
            if _TILE_ERRORS_ATTR in target_attrs:
                _refresh_tile_errors_attr_from_progress(target_attrs, progress)
            # Drop the internal handle from computed results; keep on lazy source
            # only when the result is a distinct object (re-inspect after compute).
            if target is result:
                target_attrs.pop(_PROGRESS_REF_ATTR, None)

    return result


def _resolve_on_tile_error(
    on_tile_error: TileErrorHandler | None,
    *,
    soft_fail: bool,
    soft_fail_max_fraction: float | None,
) -> TileErrorHandler | None:
    """Bind the plan-time tile error handler (``None`` = hard-fail default)."""

    if on_tile_error is not None:
        if soft_fail:
            raise ValidationError(
                "Pass either on_tile_error=... or soft_fail=True, not both"
            )
        return on_tile_error
    if not soft_fail:
        return None

    # Sugar for the library preset; ``soft_fail_max_fraction`` overrides the
    # preset default (``0.1``). Pass ``None`` for no fraction threshold.
    def _preset(ctx: TileErrorContext) -> TileErrorAction:
        return soft_fail_exhausted_tiles(ctx, max_fraction=soft_fail_max_fraction)

    return _preset


def _tile_error_policy_name(handler: TileErrorHandler | None) -> str | None:
    if handler is None:
        return None
    if handler is soft_fail_exhausted_tiles:
        return "soft_fail_exhausted_tiles"
    name = getattr(handler, "__name__", "")
    if name in {"soft_fail_exhausted_tiles", "_preset"}:
        return "soft_fail_exhausted_tiles"
    return "custom"


def _tile_errors_attr_summary(
    *,
    handler: TileErrorHandler | None,
    soft_fill_value: float,
    soft_fail_max_fraction: float | None,
    progress: FetchProgress | None = None,
) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "policy": _tile_error_policy_name(handler),
        "soft_fill_value": soft_fill_value,
        "soft_fail_max_fraction": soft_fail_max_fraction,
        "armed": handler is not None,
        "soft_failed": 0,
        "aborted": False,
        "examples": [],
    }
    if progress is not None:
        summary["soft_failed"] = progress.soft_failed_count()
        summary["aborted"] = progress.is_aborted()
        summary["examples"] = list(progress.errors[:5])
    return summary


def _progress_from_attrs(obj: Any) -> FetchProgress | None:
    attrs = _attrs_from_compute_target(obj)
    if attrs is None:
        return None
    progress = attrs.get(_PROGRESS_REF_ATTR)
    if isinstance(progress, FetchProgress):
        return progress
    return None


def _refresh_tile_errors_attr_from_progress(
    attrs: dict[str, Any],
    progress: FetchProgress,
) -> None:
    """Update public ``tilearray_tile_errors`` counters from a live FetchProgress."""

    existing = attrs.get(_TILE_ERRORS_ATTR)
    if not isinstance(existing, dict):
        return
    attrs[_TILE_ERRORS_ATTR] = {
        "policy": existing.get("policy"),
        "soft_fill_value": existing.get("soft_fill_value"),
        "soft_fail_max_fraction": existing.get("soft_fail_max_fraction"),
        "armed": existing.get("armed", False),
        "soft_failed": progress.soft_failed_count(),
        "aborted": progress.is_aborted(),
        "examples": list(progress.errors[:5]),
    }


def create_array(
    service_url: str | ServiceConfigModel,
    bbox: BoundingBox | BBoxTuple,
    bbox_crs: CRS | str | int,
    *,
    service_type: ServiceTypeEnum | None = None,
    chunk_size: tuple[int, int] | None = None,
    grid_shape: tuple[int, int] | None = None,
    output_format: Format | None = None,
    cache_dir: str | Path | None = None,
    compute: bool = False,
    compute_num_workers: int | None = None,
    dtype: str | np.dtype[Any] | None = None,
    band_policy: BandPolicy | None = None,
    expand_palette: bool = False,
    tile_decoder: TileDecoder | None = None,
    on_progress: ProgressCallback | None = None,
    show_progress: bool | None = None,
    on_tile_error: TileErrorHandler | None = None,
    soft_fail: bool = False,
    soft_fill_value: float = float("nan"),
    soft_fail_max_fraction: float | None = 0.1,
    output_crs: CRS | str | int | None = None,
    **service_options: Any,
) -> xr.DataArray:
    """
    Create an xarray ``DataArray`` backed by Dask from a remote service.

    Builds a lazy mosaic by default (``compute=False``). Prefer
    :func:`compute_mosaic` over plain ``.compute()`` so Dask workers match
    the fetch ceiling. Fetch-policy metadata is stored on ``DataArray.attrs``
    for that path. With ``compute=True``, tile fetches run on Dask's threaded
    scheduler with ``num_workers=compute_thread_pool_size(fetch_policy)``
    (override via ``compute_num_workers``).

    CRS contract
    ------------
    ``bbox_crs`` is the **query** CRS (how the bbox is described). There is
    **no** client-side raster warp onto another CRS.
    ``attrs["bbox_crs"]`` records that query CRS; ``attrs["crs"]`` is the
    mosaic / output CRS (actual pixel grid).

    * **WCS / WMS** — service config ``crs=`` is the request / subsetting CRS
      (defaults to ``bbox_crs``). Optional ``output_crs=`` asks the **server**
      to return pixels in that CRS (WCS ``outputCRS`` / WMS GetMap CRS).
      ``attrs["crs"]`` matches ``output_crs`` when set, else the request CRS.
      Unsupported ``output_crs`` values fail loud. Rejected on XYZ / WMTS.
    * **XYZ / slippy WMTS** — pixels stay on the Web Mercator grid;
      ``attrs["crs"]`` is ``EPSG:3857`` and the planned extent is the
      tile-union footprint (``bbox_crs`` interprets the query bbox only;
      ``bbox_requested`` records the caller's window). No client-side warp.
    * **WMTS with TileMatrixSet metadata** — pixels stay on the native matrix
      grid; ``attrs["crs"]`` and the planned extent follow the matrix
      SupportedCRS (query bbox / ``bbox_crs`` may differ; ``bbox_requested``
      records the caller's window). Auto-loads GetCapabilities for non-slippy
      matrices (or pass ``matrix=`` via ``**service_options``).

    A :class:`~tilearray.types.BoundingBox` whose CRS conflicts with
    ``bbox_crs=`` raises :class:`~tilearray.errors.ValidationError` (no silent
    reproject of the caller's bbox declaration).

    Raster fidelity: ``band_policy`` defaults to ``\"preserve\"`` (keep all
    bands; pass ``\"first_band\"`` to reduce explicitly). Palette PNGs stay as
    index arrays unless ``expand_palette=True``. GeoTIFF decode promotes to
    float32 so nodata can be NaN. Imagery (PNG/JPEG) defaults to **uint8** so
    ``DataArray.plot.imshow(rgb="band")`` works without an ``astype`` cast;
    pass ``dtype="float32"`` explicitly when you need float mosaics (required
    automatically when soft-fail needs NaN fill). Clipped WCS GeoTIFF
    responses are placed by their tags onto the requested grid and padded
    with NaN — never stretched.

    Parameters
    ----------
    service_url:
        Service **config** (``WCSConfig`` / ``WMSConfig`` / ``WMTSConfig`` /
        ``XYZConfig``) or, where supported, a base URL string. Prefer a typed
        config (often ``from_url`` + ``fetch_presets``) over a bare string.
    bbox:
        Area of interest as ``(minx, miny, maxx, maxy)`` or a
        :class:`~tilearray.types.BoundingBox`, in the units of ``bbox_crs``.
    bbox_crs:
        **Query** CRS for the bbox (preset, EPSG string/int, or
        ``pyproj.CRS``). Must match a :class:`~tilearray.types.BoundingBox`'s
        CRS when both are provided. For XYZ / WMTS see the CRS contract
        above (native-grid mosaic label).
    output_crs:
        Optional **server-side** output CRS for WMS / WCS only. Labels
        ``attrs["crs"]`` when set. Rejected for XYZ / WMTS.
    service_type:
        Optional protocol override when ``service_url`` is a bare string.
        Must match the config when a config is passed.
    chunk_size:
        Tile / chunk height × width in pixels. Often taken from the config
        (e.g. 256×256 XYZ/WMTS; WCS coverage chunks).
    grid_shape:
        Mosaic layout as ``(rows, cols)`` of chunks. XYZ/WMTS may infer this
        from the planned tile coverage when left at the default single cell.
    output_format:
        Preferred decode format (``Format.PNG`` / ``JPEG`` / ``GEOTIFF``, …).
        Defaults come from the config / service when omitted.
    cache_dir:
        Optional on-disk tile cache (``{sha}.tile`` + ``.meta``). Warm hits
        skip HTTP; clear after decode/credential changes.
    compute:
        If ``True``, materialise immediately on the threaded scheduler.
        Default ``False`` (lazy). For lazy arrays use
        :func:`compute_mosaic`.
    compute_num_workers:
        Override Dask thread workers when ``compute=True``. Default sizes
        from the fetch-policy ceiling.
    dtype:
        Array dtype after decode. Default ``None`` selects **uint8** for
        PNG/JPEG (plot-ready) and **float32** for GeoTIFF / soft-fail paths.
        Pass an explicit numpy dtype / string to override.
    band_policy:
        ``\"preserve\"`` (default for PNG/JPEG/GeoTIFF) keeps all bands;
        ``\"first_band\"`` reduces explicitly. ``None`` uses the format
        default from :data:`~tilearray.decode.DEFAULT_BAND_POLICIES`.
    expand_palette:
        If ``True``, expand palette-mode PNG to RGB. Default keeps indices.
    tile_decoder:
        Optional custom tile→array callable (plan-time bind).
    on_progress:
        Progress callback ``(done, total, request, response)``. Replaces the
        default tqdm-style bar when provided.
    show_progress:
        Default ``None`` auto-shows a bar for multi-tile computes on TTY /
        Jupyter. Pass ``False`` (or set ``TILEARRAY_PROGRESS=0``) for quiet
        CI; ``True`` forces a bar.
    on_tile_error:
        Plan-time tile error handler (e.g.
        :func:`~tilearray.soft_fail_exhausted_tiles`). Bound into each
        delayed load before the graph is built.
    soft_fail:
        Convenience switch for the library soft-fill preset. Default remains
        hard-fail + sibling abort after retries. Prefer an explicit
        ``on_tile_error=`` when you need a custom policy.
    soft_fill_value:
        Fill value for soft-failed tiles (default NaN).
    soft_fail_max_fraction:
        Abort if soft-filled tiles would exceed this fraction of the mosaic
        (default ``0.1``). ``None`` disables the fraction cap.
    **service_options:
        Protocol / planner / fetch knobs (``resolution``,
        ``max_concurrent_requests``, ``headers``, ``params``, WMTS
        ``matrix=``, …). Same three-family bag as documented in how-it-works;
        soft-fail and progress stay as named parameters above.

    Returns
    -------
    xarray.DataArray
        North-up mosaic with ``y``/``x`` pixel-centre coords and attrs
        (``crs``, ``transform``, ``band_policy``, fetch/error summaries, …).
        Lazy Dask-backed unless ``compute=True``.
    """

    service_config = (
        service_url if isinstance(service_url, ServiceConfigModel) else None
    )
    base_service_url = (
        service_config.base_url if service_config else cast(str, service_url)
    )

    if (
        service_config
        and service_type is not None
        and service_type != service_config.service_type
    ):
        raise ValidationError(
            "Provided service_type does not match the ServiceConfig service_type"
        )

    tile_error_handler = _resolve_on_tile_error(
        on_tile_error,
        soft_fail=soft_fail,
        soft_fail_max_fraction=soft_fail_max_fraction,
    )

    options = dict(service_options)
    if "crs" in options:
        raise ValidationError(
            "create_array no longer accepts crs=; pass bbox_crs= for the query "
            "CRS (service config crs= for request defaults is unchanged)."
        )
    if output_crs is not None:
        if "output_crs" in options:
            raise ValidationError(
                "pass output_crs= once (named argument or service option, not both)"
            )
        options["output_crs"] = output_crs
    fetch_policy = _resolve_fetch_policy(service_config, options)

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
    effective_format = request.effective_format(service)
    resolved_band_policy: BandPolicy | None = band_policy
    if resolved_band_policy is None and effective_format is not None:
        resolved_band_policy = DEFAULT_BAND_POLICIES.get(effective_format)
    decoder = _resolve_decoder(
        request,
        service,
        tile_decoder,
        band_policy=resolved_band_policy,
        expand_palette=expand_palette,
    )

    tile_requests, tile_options = request.plan_tile_requests(service)

    rows, cols = request.grid_shape
    tile_grid = _organize_tiles(tile_requests, rows, cols)

    cache_path = request.cache_path
    chunk_height, chunk_width = request.chunk_size
    dtype_np = _resolve_mosaic_dtype(
        dtype,
        effective_format=effective_format,
        needs_nan_soft_fill=tile_error_handler is not None,
    )
    if tile_error_handler is not None and not np.issubdtype(dtype_np, np.floating):
        if soft_fill_value != soft_fill_value:  # NaN
            raise ValidationError(
                f"soft_fail / on_tile_error with dtype={dtype_np} cannot use "
                "NaN soft_fill_value (would become 0). Omit dtype= to keep the "
                "float32 soft-fail default, pass dtype='float32', or set a "
                "finite soft_fill_value."
            )
    progress_callback = resolve_on_progress(
        on_progress,
        total_tiles=len(tile_requests),
        show_progress=show_progress,
    )
    progress = FetchProgress(
        total=len(tile_requests),
        on_progress=progress_callback,
        call_max_concurrent=fetch_policy.call_max_concurrent,
    )

    # Probe ignores soft-fail: shape discovery must stay hard.
    n_bands = _probe_tile_band_count(
        tile_requests[0],
        cache_path,
        decoder,
        dtype_np,
        fetch_policy,
        effective_format,
        band_policy=resolved_band_policy,
    )

    blocks: list[list[DaskArray]] = []
    for row_tiles in tile_grid:
        row_blocks: list[DaskArray] = []
        for tile_request in row_tiles:
            height = tile_request.height or chunk_height
            width = tile_request.width or chunk_width
            delayed_tile = _delayed_call(
                _load_tile_array,
                tile_request,
                cache_path,
                decoder,
                dtype_np,
                fetch_policy,
                progress,
                tile_error_handler,
                soft_fill_value,
                n_bands,
            )
            block_shape = (height, width, n_bands) if n_bands > 1 else (height, width)
            row_blocks.append(
                da_from_delayed(
                    delayed_tile,
                    shape=block_shape,
                    dtype=dtype_np,
                )
            )
        blocks.append(row_blocks)

    data = _assemble_tile_mosaic(blocks, n_bands)

    attrs = request.array_attrs(service, tile_options, effective_format)
    attrs[_FETCH_POLICY_ATTR] = _fetch_policy_summary(fetch_policy)
    attrs[_RECOMMENDED_NUM_WORKERS_ATTR] = compute_thread_pool_size(fetch_policy)
    attrs[_TILE_ERRORS_ATTR] = _tile_errors_attr_summary(
        handler=tile_error_handler,
        soft_fill_value=soft_fill_value,
        soft_fail_max_fraction=soft_fail_max_fraction
        if soft_fail or tile_error_handler is not None
        else None,
        progress=None,
    )
    # Shared with delayed tile loads; compute_mosaic refreshes counters from it.
    attrs[_PROGRESS_REF_ATTR] = progress

    normalized_bbox = request.bbox
    height = int(data.shape[0])
    width = int(data.shape[1])
    attrs.update(
        _geospatial_attrs(
            bbox=normalized_bbox,
            height=height,
            width=width,
            output_format=effective_format,
            band_policy=resolved_band_policy,
            expand_palette=expand_palette,
            mosaic_dtype=dtype_np,
        )
    )
    # Pixel centers from mosaic Affine; row 0 = north (_organize_tiles sorts by -max_y).
    y_coords, x_coords = _pixel_center_coords(normalized_bbox, height, width)

    coords: dict[str, Any] = {"y": y_coords, "x": x_coords}
    dims: tuple[str, ...] = ("y", "x")
    if n_bands > 1:
        coords["band"] = np.arange(n_bands)
        dims = ("y", "x", "band")

    data_array = xr.DataArray(
        data,
        coords=coords,
        dims=dims,
        attrs=attrs,
    )

    if compute:
        num_workers = compute_thread_pool_size(
            fetch_policy, override=compute_num_workers
        )
        computed = data_array.compute(
            scheduler="threads",
            num_workers=num_workers,
        )
        computed.attrs[_TILE_ERRORS_ATTR] = _tile_errors_attr_summary(
            handler=tile_error_handler,
            soft_fill_value=soft_fill_value,
            soft_fail_max_fraction=soft_fail_max_fraction
            if soft_fail or tile_error_handler is not None
            else None,
            progress=progress,
        )
        computed.attrs.pop(_PROGRESS_REF_ATTR, None)
        return computed
    return data_array


def load_array(*args: Any, compute: bool = True, **kwargs: Any) -> xr.DataArray:
    """Eager sugar for :func:`create_array`.

    Defaults to ``compute=True`` so a small window materialises immediately with
    fetch-aligned Dask workers. For multi-tile mosaics prefer lazy
    ``create_array`` (default ``compute=False``) plus :func:`compute_mosaic`.
    """

    return create_array(*args, compute=compute, **kwargs)


def _mosaic_affine_transform(
    bbox: BoundingBox, height: int, width: int
) -> tuple[float, float, float, float, float, float]:
    """
    North-up Affine ``(a, b, c, d, e, f)`` for the assembled mosaic.

    Maps pixel ``(col, row)`` → geographic ``(x, y)`` with upper-left of the
    upper-left pixel at ``(bbox.min_x, bbox.max_y)``. Derived only from the
    planned mosaic bbox + shape — never from per-tile GeoTIFF tags.
    """

    if height < 1 or width < 1:
        raise ValueError(f"Mosaic shape must be positive; got {(height, width)}")
    pixel_width = (bbox.max_x - bbox.min_x) / width
    pixel_height = (bbox.max_y - bbox.min_y) / height
    return (pixel_width, 0.0, bbox.min_x, 0.0, -pixel_height, bbox.max_y)


def _pixel_center_coords(
    bbox: BoundingBox, height: int, width: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return ``(y, x)`` coordinate arrays at pixel centers (north-up)."""

    a, _b, c, _d, e, f = _mosaic_affine_transform(bbox, height, width)
    cols = np.arange(width, dtype=np.float64) + 0.5
    rows = np.arange(height, dtype=np.float64) + 0.5
    x_coords = c + a * cols
    y_coords = f + e * rows
    return y_coords, x_coords


def _resolve_mosaic_dtype(
    dtype: str | np.dtype[Any] | None,
    *,
    effective_format: Format | None,
    needs_nan_soft_fill: bool,
) -> np.dtype[Any]:
    """Pick mosaic dtype: uint8 for plot-ready imagery, float32 otherwise.

    Soft-fail needs NaN fill, so imagery with soft-fail stays float32 unless
    the caller passes an explicit dtype.
    """

    if dtype is not None:
        return np.dtype(dtype)
    if needs_nan_soft_fill:
        return np.dtype("float32")
    if effective_format in {Format.PNG, Format.JPEG}:
        return np.dtype("uint8")
    return np.dtype("float32")


def _geospatial_attrs(
    *,
    bbox: BoundingBox,
    height: int,
    width: int,
    output_format: Format | None,
    band_policy: BandPolicy | None = None,
    expand_palette: bool = False,
    mosaic_dtype: np.dtype[Any] | None = None,
) -> dict[str, Any]:
    """
    Analysis-ready CRS / transform / nodata / band-policy attrs for handoff.

    CRS on the DataArray remains the mosaic request CRS (set separately).
    """

    transform = _mosaic_affine_transform(bbox, height, width)
    attrs: dict[str, Any] = {
        "transform": transform,
        "transform_source": "mosaic_bbox",
    }
    if output_format is not None:
        policy = band_policy or DEFAULT_BAND_POLICIES.get(output_format)
        if policy is not None:
            attrs["band_policy"] = policy
        if output_format == Format.JPEG:
            # JPEG has no palette — never claim index mode (misleading for RGB).
            attrs["palette_mode"] = "rgb"
        elif output_format == Format.PNG:
            # expand_palette=True forces RGB; False keeps native (indices for
            # palette PNGs, already-RGB for truecolor — consumers must check ndim).
            attrs["palette_mode"] = "rgb" if expand_palette else "indices"
        if output_format == Format.GEOTIFF:
            # Decode maps GDAL_NODATA / sentinels → NaN in pixels (#53/#77).
            attrs["nodata"] = float("nan")
            attrs["_FillValue"] = float("nan")
            attrs["dtype_policy"] = "float32_nodata_nan"
        elif output_format in {Format.PNG, Format.JPEG}:
            if mosaic_dtype is not None and mosaic_dtype == np.dtype("uint8"):
                attrs["dtype_policy"] = "plot_ready_uint8"
            else:
                attrs["dtype_policy"] = "native_then_array_dtype"
    return attrs


def _assemble_tile_mosaic(
    blocks: list[list[DaskArray]],
    n_bands: int,
) -> DaskArray:
    """
    Stitch decoded tile blocks into a single mosaic array.

    ``da.block`` maps the tile grid onto the leading axes of each block. For
    multi-band tiles ``(y, x, band)`` that incorrectly concatenates along
    ``band`` instead of ``x``. Concatenate explicitly on spatial axes instead.
    """

    if n_bands > 1:
        row_arrays = [
            cast(DaskArray, da_concatenate(row_blocks, axis=1)) for row_blocks in blocks
        ]
        return cast(DaskArray, da_concatenate(row_arrays, axis=0))
    return cast(DaskArray, da_block(blocks))


def _soft_fill_array(
    request: TileRequest,
    dtype: np.dtype[Any],
    soft_fill_value: float,
    n_bands: int,
) -> NDArrayFloat:
    height = request.height or 1
    width = request.width or 1
    shape: tuple[int, ...]
    if n_bands > 1:
        shape = (height, width, n_bands)
    else:
        shape = (height, width)
    # NaN into integer dtypes silently becomes 0 — looks like valid imagery.
    if not np.issubdtype(dtype, np.floating) and (
        soft_fill_value != soft_fill_value  # NaN
        or soft_fill_value is None
    ):
        raise ValidationError(
            f"soft_fill_value={soft_fill_value!r} cannot fill dtype={dtype} "
            "(integer/uint mosaics would coerce NaN to 0 and hide soft-fails as "
            "data). Pass dtype='float32' (or another floating dtype), or a "
            "finite soft_fill_value that fits the dtype."
        )
    return cast(NDArrayFloat, np.full(shape, soft_fill_value, dtype=dtype))


def _tile_error_context(
    request: TileRequest,
    *,
    response: TileResponse | None,
    exc: BaseException | None,
    progress: FetchProgress,
    fetch_policy: FetchPolicy | None,
) -> TileErrorContext:
    policy_retries = fetch_policy.retries if fetch_policy is not None else 0
    return TileErrorContext(
        request=request,
        response=response,
        exc=exc,
        progress=progress,
        attempt_policy_retries=policy_retries,
        soft_failed_so_far=progress.soft_failed_count(),
        total_tiles=progress.total,
    )


def _apply_tile_error_action(
    action: TileErrorAction,
    *,
    request: TileRequest,
    message: str,
    progress: FetchProgress | None,
    dtype: np.dtype[Any],
    soft_fill_value: float,
    n_bands: int,
    exc: BaseException | None = None,
) -> NDArrayFloat:
    if action is TileErrorAction.SOFT_FAIL:
        if progress is not None:
            progress.record_soft_fail(request)
        return _soft_fill_array(request, dtype, soft_fill_value, n_bands)
    if action is TileErrorAction.ABORT_MOSAIC:
        if progress is not None:
            progress.abort(message)
        if exc is not None:
            raise exc
        raise NetworkError(tile_fetch_failure_message(request.url, message))
    if action is TileErrorAction.RERAISE:
        # Fetch failures keep today's abort; decode/library errors re-raise as-is.
        if exc is not None:
            raise exc
        if progress is not None:
            progress.abort(message)
        raise NetworkError(tile_fetch_failure_message(request.url, message))
    # Exhaustiveness guard for newly added TileErrorAction variants.
    _exhaustive: TileErrorAction = action
    raise AssertionError(f"Unhandled TileErrorAction: {_exhaustive!r}")


def _load_tile_array(
    request: TileRequest,
    cache_dir: Path | None,
    decoder: TileDecoder,
    dtype: np.dtype[Any],
    fetch_policy: FetchPolicy | None = None,
    progress: FetchProgress | None = None,
    on_tile_error: TileErrorHandler | None = None,
    soft_fill_value: float = float("nan"),
    n_bands: int = 1,
) -> NDArrayFloat:
    if progress is not None:
        progress.check_not_aborted()
    response = _fetch_with_cache(request, cache_dir, fetch_policy, progress)
    if progress is not None:
        progress.tick(request, response)
    if not response.success:
        message = response.error_message or f"HTTP {response.status_code}"
        if on_tile_error is None or progress is None:
            if progress is not None:
                progress.abort(message)
            raise NetworkError(
                tile_fetch_failure_message(
                    request.url,
                    message,
                    content_type=response.content_type or None,
                    body=response.data or None,
                )
            )
        action = on_tile_error(
            _tile_error_context(
                request,
                response=response,
                exc=None,
                progress=progress,
                fetch_policy=fetch_policy,
            )
        )
        return _apply_tile_error_action(
            action,
            request=request,
            message=message,
            progress=progress,
            dtype=dtype,
            soft_fill_value=soft_fill_value,
            n_bands=n_bands,
        )

    try:
        array = decoder(response, request)
        if array.ndim not in (2, 3):
            raise ValidationError(
                "tile_decoder must return a 2D or 3D (y, x[, band]) array"
            )

        array = fit_tile_to_request(array, request, response)
        return np.asarray(array, dtype=dtype)
    except Exception as exc:
        if on_tile_error is None or progress is None:
            raise
        message = f"{type(exc).__name__}: {exc}"
        action = on_tile_error(
            _tile_error_context(
                request,
                response=response,
                exc=exc,
                progress=progress,
                fetch_policy=fetch_policy,
            )
        )
        return _apply_tile_error_action(
            action,
            request=request,
            message=message,
            progress=progress,
            dtype=dtype,
            soft_fill_value=soft_fill_value,
            n_bands=n_bands,
            exc=exc,
        )


def _fetch_with_cache(
    request: TileRequest,
    cache_dir: Path | None,
    fetch_policy: FetchPolicy | None = None,
    progress: FetchProgress | None = None,
) -> TileResponse:
    if progress is not None:
        progress.check_not_aborted()
    if cache_dir is not None:
        cached = _read_cache(cache_dir, request)
        if cached is not None:
            cached_data, cached_content_type = cached
            return TileResponse(
                data=cached_data,
                content_type=cached_content_type,
                status_code=200,
                headers={},
                url=request.url,
                success=True,
                error_message=None,
            )

    response = fetch_tile(request, policy=fetch_policy, progress=progress)
    if cache_dir is not None and response.success:
        cached_data = bytes(response.data)
        if cached_data:
            _write_cache(cache_dir, request, cached_data, response.content_type)
    return response


def _read_cache(cache_dir: Path, request: TileRequest) -> tuple[bytes, str] | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(request)
    path = cache_dir / f"{key}.tile"
    if not path.exists():
        return None

    data = path.read_bytes()
    meta_path = cache_dir / f"{key}.meta"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        stored_type = str(meta.get("content_type", ""))
    elif request.output_format is not None:
        stored_type = request.output_format.value
    else:
        stored_type = ""
    content_type = sniff_effective_content_type(stored_type, data)
    return data, content_type


def _write_cache(
    cache_dir: Path,
    request: TileRequest,
    data: bytes,
    content_type: str,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = _cache_key(request)
    path = cache_dir / f"{key}.tile"
    path.write_bytes(data)
    meta_path = cache_dir / f"{key}.meta"
    meta_path.write_text(
        json.dumps({"content_type": content_type}, sort_keys=True),
        encoding="utf-8",
    )
