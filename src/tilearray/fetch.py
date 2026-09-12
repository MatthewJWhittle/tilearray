"""Centralised tile HTTP fetching with bounded concurrency and retries."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .types import TileRequest, TileResponse

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, TileRequest, TileResponse], None]

_RETRYABLE_STATUS_CODES = frozenset({429, 502, 503, 504})
_DEFAULT_MAX_CONCURRENT = 3
_DEFAULT_RATE_LIMIT_PER_SECOND = 2.0


@runtime_checkable
class HostRateLimiter(Protocol):
    """Hook invoked immediately before each HTTP request for a host."""

    def before_request(self, host: str) -> None:
        """Block until the request for ``host`` may proceed."""


class NoOpRateLimiter:
    """Rate limiter that imposes no delay."""

    def before_request(self, host: str) -> None:
        return None


class PerHostRateLimiter:
    """Conservative fixed-interval limiter keyed by request host."""

    def __init__(self, rate_per_second: float) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self._min_interval = 1.0 / rate_per_second
        self._last_request: dict[str, float] = {}
        self._lock = threading.Lock()

    def before_request(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            last = self._last_request.get(host, 0.0)
            sleep_for = self._min_interval - (now - last)
            if sleep_for > 0:
                time.sleep(sleep_for)
            self._last_request[host] = time.monotonic()


class TokenBucketRateLimiter:
    """Burst-friendly per-host limiter keyed by request host."""

    def __init__(self, rate_per_second: float, *, burst: int) -> None:
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self._rate_per_second = rate_per_second
        self._burst = float(burst)
        self._tokens: dict[str, float] = {}
        self._last_refill: dict[str, float] = {}
        self._lock = threading.Lock()

    def before_request(self, host: str) -> None:
        with self._lock:
            now = time.monotonic()
            last = self._last_refill.get(host, now)
            tokens = min(
                self._burst,
                self._tokens.get(host, self._burst)
                + (now - last) * self._rate_per_second,
            )
            if tokens < 1.0:
                sleep_for = (1.0 - tokens) / self._rate_per_second
                time.sleep(sleep_for)
                now = time.monotonic()
                last = self._last_refill.get(host, now)
                tokens = min(
                    self._burst,
                    self._tokens.get(host, self._burst)
                    + (now - last) * self._rate_per_second,
                )
            self._tokens[host] = tokens - 1.0
            self._last_refill[host] = now


class RetryableHTTPError(Exception):
    """Raised internally to trigger tenacity retries for retryable HTTP codes."""

    def __init__(self, response: httpx.Response, retry_after: float | None) -> None:
        self.response = response
        self.retry_after = retry_after
        super().__init__(f"HTTP {response.status_code}")


@dataclass
class FetchStats:
    """Runtime counters observed by :class:`TileFetcher` (for tests and benches)."""

    max_inflight: int = 0
    request_count: int = 0
    retry_count: int = 0
    _current_inflight: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reset(self) -> None:
        with self._lock:
            self.max_inflight = 0
            self.request_count = 0
            self.retry_count = 0
            self._current_inflight = 0

    def _enter_inflight(self) -> None:
        with self._lock:
            self._current_inflight += 1
            self.max_inflight = max(self.max_inflight, self._current_inflight)

    def _leave_inflight(self) -> None:
        with self._lock:
            self._current_inflight -= 1

    def _record_request(self) -> None:
        with self._lock:
            self.request_count += 1

    def _record_retry(self) -> None:
        with self._lock:
            self.retry_count += 1


@dataclass(frozen=True)
class FetchPolicy:
    """Configuration for :class:`TileFetcher`."""

    max_concurrent: int = _DEFAULT_MAX_CONCURRENT
    max_connections: int | None = None
    retries: int = 3
    timeout: float = 30.0
    rate_limit_per_second: float | None = _DEFAULT_RATE_LIMIT_PER_SECOND
    rate_limiter: HostRateLimiter | None = None

    def cache_key(self) -> tuple[Any, ...]:
        """Hashable key for sharing fetcher instances."""

        return (
            self.max_concurrent,
            self.max_connections,
            self.retries,
            self.timeout,
            self.rate_limit_per_second,
        )


@dataclass
class FetchProgress:
    """Thread-safe progress tracker for tile acquisition."""

    total: int
    on_progress: ProgressCallback | None = None
    done: int = field(default=0, init=False)
    errors: list[str] = field(default_factory=list, init=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def tick(self, request: TileRequest, response: TileResponse) -> None:
        with self._lock:
            self.done += 1
            if not response.success:
                message = response.error_message or f"HTTP {response.status_code}"
                self.errors.append(f"{request.url}: {message}")
                logger.warning(
                    "Tile fetch failed (%s/%s): %s", self.done, self.total, message
                )
            if self.on_progress is not None:
                self.on_progress(self.done, self.total, request, response)


def _parse_retry_after(response: httpx.Response) -> float | None:
    header = response.headers.get("Retry-After")
    if not header:
        return None
    try:
        return float(header)
    except ValueError:
        return None


def _retry_wait(retry_state: RetryCallState) -> float:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    if isinstance(exc, RetryableHTTPError) and exc.retry_after is not None:
        return max(exc.retry_after, 0.0)
    jitter_wait = wait_exponential_jitter(initial=1, max=60)
    return float(jitter_wait(retry_state))


def _response_from_httpx(response: httpx.Response, request_url: str) -> TileResponse:
    if response.status_code == 200:
        return TileResponse(
            data=response.content,
            content_type=response.headers.get("content-type", ""),
            status_code=response.status_code,
            headers=dict(response.headers),
            url=str(response.url),
            success=True,
        )

    error_msg = f"HTTP {response.status_code}: {response.text[:200]}"
    return TileResponse(
        data=b"",
        content_type=response.headers.get("content-type", ""),
        status_code=response.status_code,
        headers=dict(response.headers),
        url=str(response.url),
        success=False,
        error_message=error_msg,
    )


class TileFetcher:
    """Sync tile fetcher backed by a shared :class:`httpx.Client`."""

    _instances: dict[tuple[Any, ...], TileFetcher] = {}
    _instances_lock = threading.Lock()

    def __init__(self, policy: FetchPolicy | None = None) -> None:
        self._policy = policy or FetchPolicy()
        self.stats = FetchStats()
        max_connections = self._policy.max_connections or self._policy.max_concurrent
        self._client = httpx.Client(
            limits=httpx.Limits(
                max_connections=max_connections,
                max_keepalive_connections=self._policy.max_concurrent,
            ),
            timeout=httpx.Timeout(self._policy.timeout),
        )
        self._semaphore = threading.Semaphore(self._policy.max_concurrent)
        if self._policy.rate_limiter is not None:
            self._rate_limiter: HostRateLimiter = self._policy.rate_limiter
        elif self._policy.rate_limit_per_second is not None:
            self._rate_limiter = TokenBucketRateLimiter(
                self._policy.rate_limit_per_second,
                burst=self._policy.max_concurrent,
            )
        else:
            self._rate_limiter = NoOpRateLimiter()

    @classmethod
    def for_policy(cls, policy: FetchPolicy | None = None) -> TileFetcher:
        """Return a shared fetcher instance for the given policy."""

        effective = policy or FetchPolicy()
        key = effective.cache_key()
        with cls._instances_lock:
            fetcher = cls._instances.get(key)
            if fetcher is None:
                fetcher = cls(effective)
                cls._instances[key] = fetcher
            return fetcher

    def close(self) -> None:
        self._client.close()

    @classmethod
    def reset_instances(cls) -> None:
        """Close and discard cached fetcher instances (primarily for tests)."""

        with cls._instances_lock:
            for fetcher in cls._instances.values():
                fetcher.close()
            cls._instances.clear()

    def fetch(self, request: TileRequest) -> TileResponse:
        if not request.url:
            raise ValueError("URL is required")

        headers = dict(request.headers or {})
        if request.output_format:
            headers.setdefault("Accept", request.output_format.value)

        host = urlparse(request.url).netloc or request.url
        attempts = max(request.retries, self._policy.retries) + 1

        def _before_sleep(retry_state: RetryCallState) -> None:
            self.stats._record_retry()

        @retry(
            retry=retry_if_exception_type((httpx.TransportError, RetryableHTTPError)),
            wait=_retry_wait,
            stop=stop_after_attempt(attempts),
            reraise=True,
            before_sleep=_before_sleep,
        )
        def _perform_get() -> httpx.Response:
            with self._semaphore:
                self._rate_limiter.before_request(host)
                self.stats._enter_inflight()
                try:
                    self.stats._record_request()
                    logger.debug("Fetching tile: %s", request.url)
                    response = self._client.get(
                        request.url,
                        params=request.params or None,
                        headers=headers,
                    )
                finally:
                    self.stats._leave_inflight()
            if response.status_code in _RETRYABLE_STATUS_CODES:
                raise RetryableHTTPError(response, _parse_retry_after(response))
            return response

        try:
            response = _perform_get()
        except RetryableHTTPError as exc:
            return _response_from_httpx(exc.response, request.url)
        except httpx.TransportError as exc:
            return TileResponse(
                data=b"",
                content_type="",
                status_code=0,
                headers={},
                url=request.url,
                success=False,
                error_message=f"Network error: {exc}",
            )

        return _response_from_httpx(response, request.url)


_default_fetcher = TileFetcher.for_policy(FetchPolicy())


def get_fetcher(policy: FetchPolicy | None = None) -> TileFetcher:
    """Return the shared :class:`TileFetcher` for ``policy`` (or the default)."""

    if policy is None:
        return _default_fetcher
    return TileFetcher.for_policy(policy)


def fetch_tile_with_policy(
    request: TileRequest,
    policy: FetchPolicy | None = None,
) -> TileResponse:
    """Fetch a tile using the configured :class:`TileFetcher`."""

    return get_fetcher(policy).fetch(request)
