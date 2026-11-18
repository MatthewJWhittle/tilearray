"""Thin persist lifecycle: temp write path → atomic publish on clean exit.

Callers still use xarray ``Dataset.to_zarr`` (or later ``rio.to_raster``).
tilearray owns incomplete→complete *close of process* only — not pixel IO.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal

from .errors import ValidationError

__all__ = ["staging"]

_Kind = Literal["zarr", "raster"]


def _remove_path(path: Path) -> None:
    """Remove a file or directory if it exists."""
    if not path.exists() and not path.is_symlink():
        return
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _partial_path(final: Path) -> Path:
    """Sibling write target: ``out.zarr`` → ``out.zarr.partial``."""
    return final.with_name(final.name + ".partial")


def _trash_path(final: Path) -> Path:
    """Sibling used only while swapping an existing final store aside."""
    return final.with_name(final.name + ".staging-trash")


def _atomic_publish(temp: Path, final: Path) -> None:
    """Publish ``temp`` onto ``final`` via ``os.replace`` (same-filesystem).

    When ``final`` already exists as a non-empty directory, POSIX ``replace``
    cannot overwrite it in one step, so the old store is moved aside first
    and removed only after the new store is in place.
    """
    if not temp.exists():
        raise ValidationError(
            f"staging write path does not exist at publish time: {temp}. "
            "Write the store (e.g. Dataset.to_zarr) before leaving the "
            "staging context."
        )

    final.parent.mkdir(parents=True, exist_ok=True)

    if not final.exists() and not final.is_symlink():
        os.replace(temp, final)
        return

    trash = _trash_path(final)
    _remove_path(trash)
    os.replace(final, trash)
    try:
        os.replace(temp, final)
    except OSError:
        # Best-effort restore so a failed publish does not leave final absent
        # when the previous store was still available.
        if trash.exists() and not final.exists():
            try:
                os.replace(trash, final)
            except OSError:
                pass
        raise
    _remove_path(trash)


@contextmanager
def staging(
    path: str | Path,
    *,
    kind: _Kind = "zarr",
) -> Iterator[Path]:
    """Yield a temp write path; on clean exit atomically publish to ``path``.

    Whole-store rewrite only (``mode="w"`` story). On clean context exit the
    temp store is published onto ``path`` with ``os.replace``. On exception
    (or process kill before exit) the final path stays absent — the temp is
    discarded when possible, or may be left as sibling ``*.partial`` junk.

    Tile-fetch resume remains ``cache_dir`` on ``create_array``; it is
    orthogonal to this persist publish.

    Args:
        path: Final store path (present ⇒ complete for this MVP).
        kind: Persist format. ``"zarr"`` is supported; ``"raster"`` / COG is
            not implemented yet.

    Yields:
        Temporary path to pass to ``Dataset.to_zarr(..., mode="w")``.

    Raises:
        ValidationError: Unsupported ``kind``, or publish with no written temp.
    """
    if kind == "raster":
        raise ValidationError(
            "staging(..., kind='raster') is not implemented yet. "
            "Use kind='zarr' (default) for whole-store Zarr writes."
        )
    if kind != "zarr":
        raise ValidationError(f"Unsupported staging kind={kind!r}. Use kind='zarr'.")

    final = Path(path)
    temp = _partial_path(final)
    final.parent.mkdir(parents=True, exist_ok=True)
    _remove_path(temp)

    published = False
    try:
        yield temp
        _atomic_publish(temp, final)
        published = True
    finally:
        if not published:
            _remove_path(temp)
