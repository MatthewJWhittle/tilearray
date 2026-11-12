"""Shared sync httpx client construction for plan-time GETs and tile fetch.

Lives outside :mod:`tilearray.service` so :mod:`tilearray.fetch` can use it
without a fetch → service → tiles → fetch import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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
    kwargs: dict[str, Any] = {
        "timeout": timeout_cfg,
        "follow_redirects": True,
    }
    if limits is not None:
        kwargs["limits"] = limits
    return httpx.Client(**kwargs)


def peel_url_query(
    url: str,
    params: Mapping[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Move any query string on ``url`` into a params dict.

    URL query keys are applied first; entries already in ``params`` win so
    callers can override host defaults (e.g. ``map=`` on a MapServer base URL).
    Returns the bare URL (no query) and the merged params mapping.
    """

    parts = urlsplit(url)
    query_params = dict(parse_qsl(parts.query, keep_blank_values=True))
    merged: dict[str, Any] = {**query_params, **dict(params or {})}
    bare = urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))
    return bare, merged
