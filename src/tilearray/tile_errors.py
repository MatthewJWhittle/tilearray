"""Plan-time tile error policy for mosaic fetches (#65).

Default mosaics still hard-fail after retries. Opt in with
``on_tile_error=soft_fail_exhausted_tiles`` (or ``soft_fail=True``) so
tile-local exhausted fetch failures become fill blocks instead of aborting
siblings.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable

from .fetch import FetchProgress
from .types import TileRequest, TileResponse

# Statuses safe to hole-fill after the fetcher has finished (retries exhausted
# or non-retryable tile-local HTTP). Auth, rate-limit storms, and bans stay hard.
_SOFT_FAIL_STATUSES = frozenset({0, 404, 408, 500, 502, 503, 504})
_DEFAULT_DENY_STATUSES = frozenset({401})


class TileErrorAction(Enum):
    """Decision returned by a :data:`TileErrorHandler`."""

    SOFT_FAIL = "soft_fail"
    RERAISE = "reraise"
    ABORT_MOSAIC = "abort_mosaic"


@dataclass(frozen=True)
class TileErrorContext:
    """Snapshot passed to :data:`TileErrorHandler` after a tile-level failure."""

    request: TileRequest
    response: TileResponse | None
    exc: BaseException | None
    progress: FetchProgress
    attempt_policy_retries: int
    soft_failed_so_far: int
    total_tiles: int


TileErrorHandler = Callable[[TileErrorContext], TileErrorAction]


def soft_fail_exhausted_tiles(
    ctx: TileErrorContext,
    *,
    max_fraction: float | None = 0.1,
    max_count: int | None = None,
    deny_statuses: frozenset[int] = _DEFAULT_DENY_STATUSES,
) -> TileErrorAction:
    """Library preset: hole-fill exhausted tile-local fetch failures.

    Soft-fills transport (status 0), plain/OGC 404, 408, and 5xx after the
    fetcher returns. Never skips retries (decision is post-fetch). Hard-aborts
    on auth (401 by default), decode/library exceptions, statuses outside the
    soft allowlist (including exhausted 429 / 403), and when soft-fail fraction
    or count would exceed the configured threshold.
    """

    if ctx.exc is not None:
        return TileErrorAction.ABORT_MOSAIC

    response = ctx.response
    if response is None:
        return TileErrorAction.ABORT_MOSAIC

    status = int(response.status_code)
    if status in deny_statuses:
        return TileErrorAction.ABORT_MOSAIC
    if status not in _SOFT_FAIL_STATUSES:
        return TileErrorAction.ABORT_MOSAIC

    next_soft = ctx.soft_failed_so_far + 1
    if max_count is not None and next_soft > max_count:
        return TileErrorAction.ABORT_MOSAIC
    if max_fraction is not None and ctx.total_tiles > 0:
        # ceil so a single hole in a small mosaic is still allowed when the
        # fraction budget is tiny (e.g. 1 tile @ 0.1 → allow 1, not abort).
        max_allowed = math.ceil(max_fraction * ctx.total_tiles)
        if next_soft > max_allowed:
            return TileErrorAction.ABORT_MOSAIC

    return TileErrorAction.SOFT_FAIL
