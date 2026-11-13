"""Process-local GetCapabilities / DescribeCoverage response memo.

Caches successful plan-time XML bodies in-process with a ~1 hour TTL so
inspect → preview → create (and repeated ``get_capabilities`` /
``describe_coverage`` calls) share one HTTP fetch when the key matches.

Key = bare URL + query params + auth identity (credential headers). Disk /
``cache_dir`` persistence is intentionally out of scope.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .._http_client import peel_url_query
from ..error_messages import looks_like_html, looks_like_ogc_exception_report

__all__ = [
    "DEFAULT_CAPABILITIES_TTL_SECONDS",
    "cached_capabilities_text",
    "capabilities_memo_key",
    "clear_capabilities_memo",
    "get_cached_capabilities_text",
    "set_capabilities_memo_ttl",
    "store_capabilities_text",
]

DEFAULT_CAPABILITIES_TTL_SECONDS = 3600.0

# Headers that identify the caller but must not partition the memo by UA noise.
_NON_AUTH_HEADERS = frozenset(
    {
        "user-agent",
        "accept",
        "accept-encoding",
        "accept-language",
        "connection",
        "content-type",
        "host",
    }
)


@dataclass(frozen=True)
class _MemoEntry:
    text: str
    stored_at: float


_LOCK = threading.Lock()
_MEMO: dict[tuple[Any, ...], _MemoEntry] = {}
_TTL_SECONDS = DEFAULT_CAPABILITIES_TTL_SECONDS
_CLOCK: Callable[[], float] = time.monotonic


def set_capabilities_memo_ttl(seconds: float | None = None) -> float:
    """Set the process-local TTL (seconds). ``None`` restores the default.

    Returns the active TTL. Intended for tests; production callers should
    leave the ~1h default.
    """

    global _TTL_SECONDS
    with _LOCK:
        _TTL_SECONDS = (
            DEFAULT_CAPABILITIES_TTL_SECONDS if seconds is None else float(seconds)
        )
        return _TTL_SECONDS


def clear_capabilities_memo() -> None:
    """Discard all memoised capabilities / DescribeCoverage bodies."""

    with _LOCK:
        _MEMO.clear()


def capabilities_memo_key(
    url: str,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> tuple[Any, ...]:
    """Stable cache key: bare URL + sorted params + auth identity.

    Bare URLs match :class:`~tilearray.service.base.BaseService` normalisation
    (trailing ``/`` and stray ``?`` stripped) so inspect and service clients
    share entries for the same endpoint.
    """

    bare_url, merged_params = peel_url_query(url, params)
    bare_url = bare_url.rstrip("/?") or bare_url
    param_items = tuple(
        sorted((str(key), str(value)) for key, value in merged_params.items())
    )
    auth_items = _auth_identity(headers)
    return (bare_url, param_items, auth_items)


def _is_cacheable_capabilities_body(text: str) -> bool:
    """Reject HTML portals and OGC ExceptionReports (do not poison the memo)."""

    if not text or not text.strip():
        return False
    if looks_like_html(text) or looks_like_ogc_exception_report(text):
        return False
    return True


def _auth_identity(headers: Mapping[str, str] | None) -> tuple[tuple[str, str], ...]:
    if not headers:
        return ()
    items: list[tuple[str, str]] = []
    for key, value in headers.items():
        lower = str(key).lower()
        if lower in _NON_AUTH_HEADERS:
            continue
        items.append((lower, str(value)))
    items.sort()
    return tuple(items)


def get_cached_capabilities_text(key: tuple[Any, ...]) -> str | None:
    """Return memoised body text when present and within TTL, else ``None``."""

    now = _CLOCK()
    with _LOCK:
        entry = _MEMO.get(key)
        if entry is None:
            return None
        if now - entry.stored_at > _TTL_SECONDS:
            del _MEMO[key]
            return None
        return entry.text


def store_capabilities_text(key: tuple[Any, ...], text: str) -> None:
    """Store a successful capabilities / DescribeCoverage body under ``key``."""

    with _LOCK:
        _MEMO[key] = _MemoEntry(text=text, stored_at=_CLOCK())


def cached_capabilities_text(
    url: str,
    *,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
    refresh: bool = False,
    fetch: Callable[[], str],
) -> str:
    """Return cached text or call ``fetch``, then memoize the result.

    ``refresh=True`` bypasses a live hit and replaces the entry after fetch.
    """

    key = capabilities_memo_key(url, params, headers)
    if not refresh:
        hit = get_cached_capabilities_text(key)
        if hit is not None:
            return hit
    text = fetch()
    if _is_cacheable_capabilities_body(text):
        store_capabilities_text(key, text)
    return text
