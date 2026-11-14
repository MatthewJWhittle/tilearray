"""Mark Zarr mosaic writes complete only after a successful compute."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import zarr  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - optional dependency
    zarr = None

from .array import (
    _RECOMMENDED_NUM_WORKERS_ATTR,
    _attrs_from_compute_target,
    _progress_from_attrs,
    _resolve_fetch_policy_for_compute,
    compute_mosaic,
)
from .errors import ConfigurationError, ValidationError
from .fetch import FetchPolicy

COMPLETE_ATTR = "tilearray_complete"
COMPLETE_AT_ATTR = "tilearray_complete_at"
COMPLETE_ERROR_ATTR = "tilearray_complete_error"
TILE_COUNT_ATTR = "tilearray_tile_count"

__all__ = [
    "COMPLETE_ATTR",
    "COMPLETE_AT_ATTR",
    "COMPLETE_ERROR_ATTR",
    "TILE_COUNT_ATTR",
    "write_zarr",
]


def write_zarr(
    obj: Any,
    store: str | Path,
    *,
    name: str | None = None,
    fetch_policy: FetchPolicy | None = None,
    num_workers: int | None = None,
    **to_zarr_kwargs: Any,
) -> Path:
    """
    Write a lazy mosaic to Zarr and mark the store complete only after success.

    Plain ``DataArray.to_zarr`` / ``Dataset.to_zarr`` (even with
    ``compute_mosaic`` on the delayed graph) writes array attrs when the store
    is created — before any tile is fetched. An aborted or killed run therefore
    leaves a full-shape store that looks finished. This helper:

    1. Builds the delayed write with ``compute=False``.
    2. Sets group attr ``tilearray_complete=false`` (and planned tile count).
    3. Runs :func:`~tilearray.compute_mosaic` on the delayed graph.
    4. On success sets ``tilearray_complete=true`` plus finish time; on failure
       sets ``tilearray_complete=false`` with the error and re-raises.

    Readers should treat a missing or false ``tilearray_complete`` group attr
    as incomplete. Re-run into the same path with the same ``cache_dir`` to
    resume warm tiles.

    Parameters
    ----------
    obj:
        Lazy :class:`xarray.DataArray` from :func:`~tilearray.create_array`, or
        an :class:`xarray.Dataset` already holding the mosaic.
    store:
        Zarr path (directory store).
    name:
        Variable name when ``obj`` is a DataArray without ``.name``. Ignored for
        Dataset inputs.
    fetch_policy, num_workers:
        Forwarded to :func:`~tilearray.compute_mosaic` (policy also resolved
        from mosaic attrs when omitted).
    **to_zarr_kwargs:
        Passed to ``Dataset.to_zarr``. ``compute`` is always forced to
        ``False``; default ``mode`` is ``"w"``.

    Returns
    -------
    pathlib.Path
        The store path.

    Raises
    ------
    ConfigurationError
        If the optional ``zarr`` package is not installed.
    ValidationError
        If ``obj`` is not a DataArray or Dataset, or ``compute=`` is passed.
    """

    if zarr is None:
        raise ConfigurationError(
            "write_zarr requires the optional 'zarr' package "
            "(pip install 'zarr>=2.18,<3')."
        )
    if "compute" in to_zarr_kwargs:
        raise ValidationError(
            "write_zarr always uses compute=False then compute_mosaic; "
            "do not pass compute=."
        )

    path = Path(store)
    dataset = _as_dataset(obj, name=name)
    policy = _resolve_fetch_policy_for_compute(obj, fetch_policy=fetch_policy)
    workers = num_workers
    if workers is None:
        attrs = _attrs_from_compute_target(obj)
        if attrs is not None:
            recommended = attrs.get(_RECOMMENDED_NUM_WORKERS_ATTR)
            if recommended is not None:
                workers = int(recommended)

    tile_count = _tile_count_from_obj(obj)
    kwargs = dict(to_zarr_kwargs)
    kwargs.setdefault("mode", "w")
    delayed = dataset.to_zarr(path, compute=False, **kwargs)

    _set_completion_attrs(path, complete=False, tile_count=tile_count)
    try:
        compute_mosaic(delayed, fetch_policy=policy, num_workers=workers)
    except Exception as exc:
        _set_completion_attrs(
            path,
            complete=False,
            tile_count=tile_count,
            error=_error_text(exc),
        )
        raise

    _set_completion_attrs(
        path,
        complete=True,
        tile_count=tile_count,
        completed_at=datetime.now(timezone.utc).isoformat(),
    )
    return path


def _as_dataset(obj: Any, *, name: str | None) -> Any:
    data_vars = getattr(obj, "data_vars", None)
    if data_vars is not None and hasattr(obj, "to_zarr"):
        return obj
    to_dataset = getattr(obj, "to_dataset", None)
    if callable(to_dataset):
        var_name = name or getattr(obj, "name", None) or "data"
        return to_dataset(name=str(var_name))
    raise ValidationError(
        f"write_zarr expects an xarray DataArray or Dataset (got {type(obj).__name__})"
    )


def _tile_count_from_obj(obj: Any) -> int | None:
    progress = _progress_from_attrs(obj)
    if progress is not None:
        return int(progress.total)
    return None


def _error_text(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if len(text) > 500:
        return text[:497] + "..."
    return text


def _set_completion_attrs(
    path: Path,
    *,
    complete: bool,
    tile_count: int | None = None,
    completed_at: str | None = None,
    error: str | None = None,
) -> None:
    assert zarr is not None
    group = zarr.open_group(str(path), mode="a")
    group.attrs[COMPLETE_ATTR] = bool(complete)
    if tile_count is not None:
        group.attrs[TILE_COUNT_ATTR] = int(tile_count)
    if complete:
        if completed_at is not None:
            group.attrs[COMPLETE_AT_ATTR] = completed_at
        if COMPLETE_ERROR_ATTR in group.attrs:
            del group.attrs[COMPLETE_ERROR_ATTR]
    else:
        if COMPLETE_AT_ATTR in group.attrs:
            del group.attrs[COMPLETE_AT_ATTR]
        if error is not None:
            group.attrs[COMPLETE_ERROR_ATTR] = error
        elif COMPLETE_ERROR_ATTR in group.attrs:
            del group.attrs[COMPLETE_ERROR_ATTR]
    zarr.consolidate_metadata(str(path))
