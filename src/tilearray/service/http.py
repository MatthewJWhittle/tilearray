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
* **Retries:** bounded retries for transport errors and pressure-classified
  transient statuses (408 / 429 / 5xx). Honours ``Retry-After``, otherwise
  exponential backoff with jitter. Budget from ``fetch_retries`` on the service
  config (same semantics as tile fetch: attempts = retries + 1). Client errors
  (400 / 401 / 403 / 404) fail fast on the first response.
* **Headers / params:** library default User-Agent, then config overlays, then
  call-site extras — so private-server credentials on the config reach
  GetCapabilities / DescribeCoverage the same way they reach tiles
* **Caps memo:** GetCapabilities / DescribeCoverage bodies share a process-local
  TTL memo (see :mod:`tilearray.service.capabilities_memo`); tile GETs do not

No new public HTTP knobs; services call these helpers internally.
"""

from __future__ import annotations

import logging
import random
import time
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
from ..pressure import classify_response
from .capabilities_memo import cached_capabilities_text
from .requests import merge_params, merge_str_mappings

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT",
    "DEFAULT_HTTP_TIMEOUT",
    "DEFAULT_HTTP_RETRIES",
    "compose_http_headers",
    "compose_http_params",
    "default_headers",
    "http_get",
    "http_get_capabilities_text",
    "make_sync_client",
    "resolve_retries",
    "resolve_timeout",
    "shape_httpx_timeout",
]

logger = logging.getLogger(__name__)

# Match ServiceConfig.fetch_retries / FetchPolicy.retries defaults.
DEFAULT_HTTP_RETRIES = 3

# Plan-time auth/forbidden must fail fast even though tile fetch treats 403 as
# pressure (AIMD / circuit breaker). Reuse pressure classification for the rest.
_PLAN_TIME_FAIL_FAST_STATUSES = frozenset({403})


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


def resolve_retries(config: Mapping[str, object] | None = None) -> int:
    """Resolve plan-time retry budget from ``fetch_retries`` or the shared default."""

    if config is not None:
        value = config.get("fetch_retries")
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return DEFAULT_HTTP_RETRIES


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


def _parse_retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _is_plan_time_retryable(response: httpx.Response) -> bool:
    """True when pressure marks the response transient and it is not auth/forbidden."""

    if response.status_code in _PLAN_TIME_FAIL_FAST_STATUSES:
        return False
    return classify_response(response).retryable


def _backoff_seconds(attempt: int, retry_after: float | None) -> float:
    """Wait before the next attempt (1-based ``attempt`` that just failed)."""

    if retry_after is not None:
        return max(retry_after, 0.0)
    # Exponential with jitter: ~1 s → ~2 s → ~4 s (capped).
    base = float(min(2 ** (attempt - 1), 4))
    return base + random.uniform(0.0, base * 0.25)


def _raise_exhausted_status(response: httpx.Response, *, attempts: int) -> None:
    """Raise :class:`httpx.HTTPStatusError` naming how many attempts were made."""

    message = (
        f"Client error '{response.status_code} {response.reason_phrase}' "
        f"for url '{response.url}'"
        if response.is_client_error
        else (
            f"Server error '{response.status_code} {response.reason_phrase}' "
            f"for url '{response.url}'"
        )
    )
    if attempts > 1:
        message = f"{message} after {attempts} attempts"
    raise httpx.HTTPStatusError(
        message,
        request=response.request,
        response=response,
    )


def _raise_exhausted_transport(exc: httpx.TransportError, *, attempts: int) -> None:
    """Re-raise a transport error, naming the attempt count when retries ran."""

    if attempts <= 1:
        raise exc
    detail = f"{exc} (after {attempts} attempts)"
    new_exc = type(exc)(detail)
    raise new_exc from exc


def http_get(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    timeout: float | None = None,
    client: httpx.Client | None = None,
    retries: int | None = None,
) -> httpx.Response:
    """
    Perform a sync GET with the shared timeout and bounded transient-retry policy.

    Retries transport failures and pressure-classified transient statuses
    (408 / 429 / 500 / 502 / 503 / 504). Client errors fail on the first response.
    Exhausted errors name how many attempts were made.

    Raises :class:`httpx.HTTPStatusError` on non-success status codes and other
    :class:`httpx.HTTPError` subclasses on transport failures.
    """

    read = DEFAULT_HTTP_TIMEOUT if timeout is None else float(timeout)
    timeout_cfg = shape_httpx_timeout(
        connect=min(DEFAULT_CONNECT_TIMEOUT, read),
        read=read,
    )
    retry_budget = DEFAULT_HTTP_RETRIES if retries is None else max(0, int(retries))
    attempts = retry_budget + 1
    owns_client = client is None
    http = client or make_sync_client(timeout=timeout_cfg)
    bare_url, merged_params = peel_url_query(url, params)
    try:
        last_transport: httpx.TransportError | None = None
        last_response: httpx.Response | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = http.get(
                    bare_url,
                    params=merged_params or None,
                    headers=dict(headers) if headers is not None else None,
                    timeout=timeout_cfg,
                )
            except httpx.TransportError as exc:
                last_transport = exc
                last_response = None
                if attempt >= attempts:
                    _raise_exhausted_transport(exc, attempts=attempt)
                wait = _backoff_seconds(attempt, retry_after=None)
                logger.debug(
                    "Plan-time GET transport error on attempt %s/%s; "
                    "retrying in %.2fs: %s",
                    attempt,
                    attempts,
                    wait,
                    exc,
                )
                time.sleep(wait)
                continue

            if response.is_success:
                return response

            last_response = response
            last_transport = None
            if _is_plan_time_retryable(response) and attempt < attempts:
                retry_after = _parse_retry_after(response)
                wait = _backoff_seconds(attempt, retry_after=retry_after)
                logger.debug(
                    "Plan-time GET HTTP %s on attempt %s/%s; retrying in %.2fs",
                    response.status_code,
                    attempt,
                    attempts,
                    wait,
                )
                time.sleep(wait)
                continue

            _raise_exhausted_status(response, attempts=attempt)

        # Unreachable: loop always returns or raises. Keep mypy happy.
        if last_response is not None:
            _raise_exhausted_status(last_response, attempts=attempts)
        if last_transport is not None:
            _raise_exhausted_transport(last_transport, attempts=attempts)
        raise RuntimeError("plan-time http_get exhausted without a result")
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
    retries: int | None = None,
    refresh: bool = False,
) -> str:
    """GET a GetCapabilities / DescribeCoverage body via the process-local memo.

    Successful responses are memoised (~1h TTL) keyed by URL + params + auth
    identity. Pass ``refresh=True`` to bypass and replace a live entry. Failures
    are never stored. Transient failures are retried inside :func:`http_get`
    before the memo sees a result.
    """

    def _fetch() -> str:
        return http_get(
            url,
            params=params,
            headers=headers,
            timeout=timeout,
            client=client,
            retries=retries,
        ).text

    return cached_capabilities_text(
        url,
        params=params,
        headers=headers,
        refresh=refresh,
        fetch=_fetch,
    )
