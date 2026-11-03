"""Default tile-fetch progress for multi-tile mosaics (#86).

Composes a tqdm-style bar through the existing ``on_progress`` /
:class:`~tilearray.fetch.FetchProgress` spine — same mechanism for CLI and
notebooks. Quiet opt-out for CI / redirected scripts.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any, TextIO

from tqdm.auto import tqdm

from .fetch import ProgressCallback
from .types import TileRequest, TileResponse

_ipython_get_ipython: Callable[[], Any] | None = None
try:
    from IPython import get_ipython as _imported_get_ipython

    _ipython_get_ipython = _imported_get_ipython
except ImportError:  # pragma: no cover - IPython is optional
    _ipython_get_ipython = None


PROGRESS_ENV = "TILEARRAY_PROGRESS"
_FALSEY = frozenset({"0", "false", "off", "no", "quiet"})
_TRUTHY = frozenset({"1", "true", "on", "yes"})


def _in_jupyter_kernel() -> bool:
    getter = _ipython_get_ipython
    if getter is None:
        return False
    ip = getter()
    if ip is None:
        return False
    # ZMQInteractiveShell = notebook / JupyterLab / ipykernel.
    return type(ip).__name__ == "ZMQInteractiveShell"


def _display_supports_progress() -> bool:
    """True for interactive TTY stderr or a Jupyter kernel (often non-TTY)."""

    err = sys.stderr
    if hasattr(err, "isatty") and err.isatty():
        return True
    return _in_jupyter_kernel()


def resolve_show_progress(
    *,
    total_tiles: int,
    show_progress: bool | None,
) -> bool:
    """Whether to attach the default tqdm-style tile progress bar.

    * ``show_progress=False`` — always quiet.
    * ``show_progress=True`` — always on (even single-tile / non-TTY).
    * ``show_progress=None`` (default) — on for multi-tile when the display
      supports progress (TTY or Jupyter), unless ``TILEARRAY_PROGRESS`` disables
      it. Env ``0``/``false``/``off``/``no``/``quiet`` forces quiet; ``1``/
      ``true``/``on``/``yes`` forces on (including single-tile / non-TTY CI).
    """

    if show_progress is False:
        return False
    if show_progress is True:
        return True

    env = os.environ.get(PROGRESS_ENV, "").strip().lower()
    if env in _FALSEY:
        return False
    if env in _TRUTHY:
        return True
    if total_tiles <= 1:
        return False
    return _display_supports_progress()


def make_tile_progress_callback(
    total: int,
    *,
    desc: str = "tiles",
    file: TextIO | None = None,
    disable: bool | None = False,
) -> ProgressCallback:
    """Build an ``on_progress`` callback that advances a tqdm bar per tile.

    Uses ``tqdm.auto`` so scripts get a CLI bar and notebooks get the notebook
    widget when available. Callers who already pass ``on_progress=`` are
    unchanged — this helper is what ``create_array`` binds by default.
    """

    bar = tqdm(
        total=total,
        desc=desc,
        unit="tile",
        file=file if file is not None else sys.stderr,
        disable=disable,
        dynamic_ncols=True,
        miniters=1,
    )

    def _on_progress(
        done: int,
        total_tiles: int,
        request: TileRequest,
        response: TileResponse,
    ) -> None:
        del request, response  # unused — signature matches ProgressCallback
        if done > bar.n:
            bar.update(done - bar.n)
        if done >= total_tiles:
            bar.close()

    return _on_progress


def resolve_on_progress(
    on_progress: ProgressCallback | None,
    *,
    total_tiles: int,
    show_progress: bool | None,
) -> ProgressCallback | None:
    """Return the user callback, a default tqdm bar, or ``None`` (quiet)."""

    if on_progress is not None:
        return on_progress
    if not resolve_show_progress(total_tiles=total_tiles, show_progress=show_progress):
        return None
    return make_tile_progress_callback(total_tiles)
