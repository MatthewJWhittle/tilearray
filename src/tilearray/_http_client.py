"""Shared sync httpx client construction for plan-time GETs and tile fetch.

Lives outside :mod:`tilearray.service` so :mod:`tilearray.fetch` can use it
without a fetch → service → tiles → fetch import cycle.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

# Align with FetchPolicy.read_timeout / ServiceConfig.fetch_timeout defaults.
DEFAULT_HTTP_TIMEOUT = 30.0
DEFAULT_CONNECT_TIMEOUT = 10.0


def shape_httpx_timeout(
    *,
    connect: float = DEFAULT_CONNECT_TIMEOUT,
    read: float = DEFAULT_HTTP_TIMEOUT,
) -> httpx.Timeout:
    """Build fine-grained ``httpx.Timeout(connect=…, read=…)``.

    ``write`` follows ``read``; ``pool`` follows ``connect``. A single float
    passed to :func:`make_sync_client` still means “read budget” with the
    default connect ceiling (capped by that float when smaller).
    """

    return httpx.Timeout(
        connect=float(connect),
        read=float(read),
        write=float(read),
        pool=float(connect),
    )


def make_sync_client(
    *,
    timeout: float | httpx.Timeout | None = None,
    connect_timeout: float | None = None,
    read_timeout: float | None = None,
    limits: httpx.Limits | None = None,
) -> httpx.Client:
    """Build a sync ``httpx.Client`` with shared timeout / optional pool Limits."""

    if isinstance(timeout, httpx.Timeout):
        timeout_cfg = timeout
    elif connect_timeout is not None or read_timeout is not None:
        read = (
            float(read_timeout)
            if read_timeout is not None
            else (
                float(timeout)
                if isinstance(timeout, (int, float))
                else DEFAULT_HTTP_TIMEOUT
            )
        )
        connect = (
            float(connect_timeout)
            if connect_timeout is not None
            else min(DEFAULT_CONNECT_TIMEOUT, read)
        )
        timeout_cfg = shape_httpx_timeout(connect=connect, read=read)
    elif timeout is None:
        timeout_cfg = shape_httpx_timeout()
    else:
        read = float(timeout)
        timeout_cfg = shape_httpx_timeout(
            connect=min(DEFAULT_CONNECT_TIMEOUT, read),
            read=read,
        )
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
