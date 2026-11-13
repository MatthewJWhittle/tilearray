"""Shared sync httpx helpers for capabilities / DescribeCoverage / service GETs.

Tile mosaics already fetch via :class:`~tilearray.fetch.TileFetcher` (httpx).
WCS/WMTS planning requests historically used ``requests.Session`` with a
different User-Agent and no config ``headers`` / ``params``. This module is the
single policy for those plan-time GETs:

* **Client:** sync ``httpx`` (same stack as tile fetch)
* **Timeout:** ``ServiceConfig.fetch_timeout`` (idle-read) when present on the
  service config bag, otherwise :data:`DEFAULT_HTTP_TIMEOUT` (matches
  :class:`~tilearray.fetch.FetchPolicy.read_timeout` /
  ``ServiceConfig.fetch_timeout``). Shaped as ``httpx.Timeout(connect=, read=)``
  with a shorter default connect ceiling.
* **Headers / params:** library default User-Agent, then config overlays, then
  call-site extras — so private-server credentials on the config reach
  GetCapabilities / DescribeCoverage the same way they reach tiles
* **Caps memo:** GetCapabilities / DescribeCoverage bodies share a process-local
  TTL memo (see :mod:`tilearray.service.capabilities_memo`); tile GETs do not

No new public HTTP knobs; services call these helpers internally.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from .._http_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HTTP_TIMEOUT,
    make_sync_client,
    peel_url_query,
    shape_httpx_timeout,
)
from ..fetch_presets import tilearray_user_agent
from .capabilities_memo import cached_capabilities_text
from .requests import merge_params, merge_str_mappings

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_HTTP_TIMEOUT",
    "compose_http_headers",
    "compose_http_params",
    "default_headers",
    "http_get",
    "http_get_capabilities_text",
    "make_sync_client",
    "resolve_timeout",
    "shape_httpx_timeout",
]


def default_headers() -> dict[str, str]:
    """Return the library User-Agent used when config does not override it."""

    return {"User-Agent": tilearray_user_agent()}


def resolve_timeout(config: Mapping[str, object] | None = None) -> float:
    """Resolve HTTP timeout from the service config bag or the shared default."""

    if config is not None:
        value = config.get("fetch_timeout")
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
    return DEFAULT_HTTP_TIMEOUT


def compose_http_headers(
    config: Mapping[str, object] | None = None,
    *extras: object,
) -> dict[str, str]:
    """Merge default UA with config and call-site headers (later wins)."""

    return merge_str_mappings(
        default_headers(),
        None if config is None else config.get("headers"),
        *extras,
    )


def compose_http_params(
    base: Mapping[str, Any],
    config: Mapping[str, object] | None = None,
    *extras: object,
) -> dict[str, Any]:
    """Merge OGC / service query params with config and call-site params."""

    return merge_params(
        base,
        None if config is None else config.get("params"),
        *extras,
    )


def http_get(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
    client: httpx.Client | None = None,
) -> httpx.Response:
    """
    Perform a sync GET with the shared timeout policy.

    Raises :class:`httpx.HTTPStatusError` on non-success status codes and other
    :class:`httpx.HTTPError` subclasses on transport failures.
    """

    read = DEFAULT_HTTP_TIMEOUT if timeout is None else float(timeout)
    timeout_cfg = shape_httpx_timeout(
        connect=min(DEFAULT_CONNECT_TIMEOUT, read),
        read=read,
    )
    owns_client = client is None
    http = client or make_sync_client(timeout=timeout_cfg)
    bare_url, merged_params = peel_url_query(url, params)
    try:
        response = http.get(
            bare_url,
            params=merged_params or None,
            headers=dict(headers) if headers is not None else None,
            timeout=timeout_cfg,
        )
        response.raise_for_status()
        return response
    finally:
        if owns_client:
            http.close()


def http_get_capabilities_text(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
    client: httpx.Client | None = None,
    refresh: bool = False,
) -> str:
    """GET a GetCapabilities / DescribeCoverage body via the process-local memo.

    Successful responses are memoised (~1h TTL) keyed by URL + params + auth
    identity. Pass ``refresh=True`` to bypass and replace a live entry. Failures
    are never stored.
    """

    def _fetch() -> str:
        return http_get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            client=client,
        ).text

    return cached_capabilities_text(
        url,
        params=params,
        headers=headers,
        refresh=refresh,
        fetch=_fetch,
    )
