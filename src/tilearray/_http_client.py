"""Shared sync httpx client construction for plan-time GETs and tile fetch.

Lives outside :mod:`tilearray.service` so :mod:`tilearray.fetch` can use it
without a fetch → service → tiles → fetch import cycle.
"""

from __future__ import annotations

from typing import Any

import httpx

# Align with FetchPolicy.timeout / ServiceConfig.fetch_timeout defaults.
DEFAULT_HTTP_TIMEOUT = 30.0


def make_sync_client(
    *,
    timeout: float | httpx.Timeout | None = None,
    limits: httpx.Limits | None = None,
) -> httpx.Client:
    """Build a sync ``httpx.Client`` with shared timeout / optional pool Limits."""

    if timeout is None:
        timeout_cfg: httpx.Timeout = httpx.Timeout(DEFAULT_HTTP_TIMEOUT)
    elif isinstance(timeout, httpx.Timeout):
        timeout_cfg = timeout
    else:
        timeout_cfg = httpx.Timeout(float(timeout))
    kwargs: dict[str, Any] = {"timeout": timeout_cfg}
    if limits is not None:
        kwargs["limits"] = limits
    return httpx.Client(**kwargs)
