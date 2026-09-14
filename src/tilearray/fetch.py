"""Centralised tile HTTP fetching with bounded concurrency and retries."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
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

from .errors import NetworkError
from .types import TileRequest, TileResponse

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, TileRequest, TileResponse], None]

# Gateway throttling (403), rate limits (429), client timeout (408), upstream 5xx.
_RETRYABLE_STATUS_CODES = frozenset({403, 408, 429, 500, 502, 503, 504})
def _is_ogc_transient_404(response: httpx.Response) -> bool:
    """ArcGIS WCS intermittently returns 404 + OGC InvalidParameterValue on retryable tiles."""

    if response.status_code != 404:
        return False
    body = response.content[:4096]
    if not body:
        return False
    lowered = body.lower()
    return (
        b"invalidparametervalue" in lowered
        or b"subsettingcrs" in lowered
        or b"exceptionreport" in lowered
    )


def _is_retryable_http_response(response: httpx.Response) -> bool:
    if response.status_code in _RETRYABLE_STATUS_CODES:
        return True
    return _is_ogc_transient_404(response)
_DEFAULT_MAX_CONCURRENT = 3
_DEFAULT_INITIAL_CONCURRENT = 2
_DEFAULT_MIN_CONCURRENT = 1
_DEFAULT_RATE_LIMIT_PER_SECOND = 2.0
_DEFAULT_MULTIPLICATIVE_DECREASE = 0.5
_DEFAULT_ADDITIVE_INCREASE = 2


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


@dataclass
class _HostConcurrencyState:
    limit: int
    inflight: int = 0
    peak_limit: int = field(init=False)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    condition: threading.Condition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.peak_limit = self.limit
        self.condition = threading.Condition(self.lock)


class AdaptiveConcurrencyGate:
    """Per-host AIMD in-flight limiter (TCP-style additive increase / multiplicative decrease)."""

    def __init__(
        self,
        *,
        initial: int,
        minimum: int,
        maximum: int,
        additive_increase: int = _DEFAULT_ADDITIVE_INCREASE,
        multiplicative_decrease: float = _DEFAULT_MULTIPLICATIVE_DECREASE,
        forbidden_window_seconds: float | None = None,
        forbidden_threshold: int = 6,
        forbidden_cooldown_seconds: float = 15.0,
    ) -> None:
        if initial <= 0 or minimum <= 0 or maximum <= 0:
            raise ValueError("concurrency limits must be positive")
        if minimum > initial:
            raise ValueError("minimum cannot exceed initial")
        if initial > maximum:
            raise ValueError("initial cannot exceed maximum")
        if not 0.0 < multiplicative_decrease < 1.0:
            raise ValueError("multiplicative_decrease must be between 0 and 1")
        self._initial = initial
        self._minimum = minimum
        self._maximum = maximum
        self._additive_increase = additive_increase
        self._multiplicative_decrease = multiplicative_decrease
        self._forbidden_window_seconds = forbidden_window_seconds
        self._forbidden_threshold = forbidden_threshold
        self._forbidden_cooldown_seconds = forbidden_cooldown_seconds
        self._hosts: dict[str, _HostConcurrencyState] = {}
        self._forbidden_events: dict[str, deque[float]] = {}
        self._frozen_until: dict[str, float] = {}
        self._map_lock = threading.Lock()
        self.decrease_count = 0
        self.peak_limit = initial
        self.pressure_403_count = 0
        self.circuit_breaker_trips = 0
        self._policy_key: tuple[Any, ...] | None = None

    def _remembered_limit(self, host: str) -> int | None:
        if self._policy_key is None:
            return None
        return _HOST_REMEMBERED_LIMITS.get(self._policy_key, {}).get(host)

    def _remember_limit(self, host: str, limit: int) -> None:
        if self._policy_key is None:
            return
        _HOST_REMEMBERED_LIMITS.setdefault(self._policy_key, {})[host] = limit

    def _seed_limit(self, host: str) -> int:
        remembered = self._remembered_limit(host)
        if remembered is not None:
            return max(self._minimum, min(self._maximum, remembered))
        return self._initial

    def _state_for(self, host: str) -> _HostConcurrencyState:
        with self._map_lock:
            state = self._hosts.get(host)
            if state is None:
                state = _HostConcurrencyState(limit=self._seed_limit(host))
                self._hosts[host] = state
            return state

    def _is_frozen(self, host: str) -> bool:
        frozen_until = self._frozen_until.get(host, 0.0)
        return frozen_until > time.monotonic()

    def _wait_for_freeze(self, host: str, state: _HostConcurrencyState) -> None:
        while True:
            frozen_until = self._frozen_until.get(host, 0.0)
            now = time.monotonic()
            if frozen_until <= now:
                return
            state.condition.wait(timeout=min(0.05, frozen_until - now))

    def acquire(self, host: str) -> None:
        state = self._state_for(host)
        with state.condition:
            while True:
                if self._is_frozen(host):
                    self._wait_for_freeze(host, state)
                if state.inflight < state.limit:
                    state.inflight += 1
                    return
                state.condition.wait(timeout=0.05)

    def _increase_limit(self, host: str, state: _HostConcurrencyState) -> None:
        if self._is_frozen(host):
            return
        if state.limit >= self._maximum:
            return
        state.limit = min(
            self._maximum,
            state.limit + self._additive_increase,
        )
        state.peak_limit = max(state.peak_limit, state.limit)
        self.peak_limit = max(self.peak_limit, state.limit)
        self._remember_limit(host, state.limit)

    def finish_success(self, host: str) -> None:
        """Release a slot and apply per-success AIMD increase."""

        state = self._state_for(host)
        with state.condition:
            state.inflight -= 1
            self._increase_limit(host, state)
            state.condition.notify_all()

    def finish_pressure(self, host: str) -> None:
        """Release a slot and apply multiplicative decrease atomically."""

        state = self._state_for(host)
        with state.condition:
            state.inflight -= 1
            new_limit = max(
                self._minimum,
                int(state.limit * self._multiplicative_decrease),
            )
            if new_limit < state.limit:
                state.limit = new_limit
                self.decrease_count += 1
                self._remember_limit(host, state.limit)
            state.condition.notify_all()

    def record_success(self, host: str) -> None:
        state = self._state_for(host)
        with state.condition:
            self._increase_limit(host, state)
            state.condition.notify_all()

    def record_pressure(self, host: str) -> None:
        state = self._state_for(host)
        with state.condition:
            new_limit = max(
                self._minimum,
                int(state.limit * self._multiplicative_decrease),
            )
            if new_limit < state.limit:
                state.limit = new_limit
                self.decrease_count += 1
                self._remember_limit(host, state.limit)
            state.condition.notify_all()

    def record_forbidden(self, host: str) -> bool:
        """Record a 403 and trip the circuit breaker when pressure clusters."""

        if self._forbidden_window_seconds is None:
            return False

        now = time.monotonic()
        with self._map_lock:
            events = self._forbidden_events.setdefault(host, deque())
            events.append(now)
            self.pressure_403_count += 1
            cutoff = now - self._forbidden_window_seconds
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) < self._forbidden_threshold:
                return False
            events.clear()
            self._frozen_until[host] = now + self._forbidden_cooldown_seconds
            self.circuit_breaker_trips += 1

        state = self._state_for(host)
        with state.condition:
            if state.limit > self._minimum:
                state.limit = self._minimum
                self.decrease_count += 1
                self._remember_limit(host, state.limit)
            state.condition.notify_all()
        return True

    def circuit_breaker_frozen(self, host: str) -> bool:
        return self._is_frozen(host)

    def current_limit(self, host: str) -> int:
        state = self._state_for(host)
        with state.condition:
            return state.limit


_ADAPTIVE_GATES: dict[tuple[Any, ...], AdaptiveConcurrencyGate] = {}
_HOST_REMEMBERED_LIMITS: dict[tuple[Any, ...], dict[str, int]] = {}
_ADAPTIVE_GATES_LOCK = threading.Lock()


def _adaptive_gate_key(policy: FetchPolicy) -> tuple[Any, ...]:
    """Stable key for sharing AIMD state (initial seed is not part of identity)."""

    return (
        policy.min_concurrent,
        policy.max_concurrent,
        policy.multiplicative_decrease,
        policy.additive_increase,
        policy.forbidden_circuit_breaker,
        policy.forbidden_window_seconds,
        policy.forbidden_threshold,
        policy.forbidden_cooldown_seconds,
    )


def _shared_adaptive_gate(policy: FetchPolicy) -> AdaptiveConcurrencyGate:
    key = _adaptive_gate_key(policy)
    with _ADAPTIVE_GATES_LOCK:
        gate = _ADAPTIVE_GATES.get(key)
        if gate is None:
            gate = AdaptiveConcurrencyGate(
                initial=policy.initial_concurrent,
                minimum=policy.min_concurrent,
                maximum=policy.max_concurrent,
                additive_increase=policy.additive_increase,
                multiplicative_decrease=policy.multiplicative_decrease,
                forbidden_window_seconds=(
                    policy.forbidden_window_seconds
                    if policy.forbidden_circuit_breaker
                    else None
                ),
                forbidden_threshold=policy.forbidden_threshold,
                forbidden_cooldown_seconds=policy.forbidden_cooldown_seconds,
            )
            gate._policy_key = key
            _ADAPTIVE_GATES[key] = gate
        return gate


def reset_adaptive_gates() -> None:
    """Discard shared AIMD gate state (primarily for tests)."""

    with _ADAPTIVE_GATES_LOCK:
        _ADAPTIVE_GATES.clear()
        _HOST_REMEMBERED_LIMITS.clear()


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
    peak_concurrency_limit: int = 0
    concurrency_decreases: int = 0
    current_limit: int = 0
    pressure_403_count: int = 0
    circuit_breaker_trips: int = 0
    circuit_breaker_frozen: bool = False
    _current_inflight: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def reset(self) -> None:
        with self._lock:
            self.max_inflight = 0
            self.request_count = 0
            self.retry_count = 0
            self.peak_concurrency_limit = 0
            self.concurrency_decreases = 0
            self.current_limit = 0
            self.pressure_403_count = 0
            self.circuit_breaker_trips = 0
            self.circuit_breaker_frozen = False
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

    def _sync_adaptive_stats(
        self, gate: AdaptiveConcurrencyGate, host: str | None = None
    ) -> None:
        with self._lock:
            self.peak_concurrency_limit = max(
                self.peak_concurrency_limit, gate.peak_limit
            )
            self.concurrency_decreases = gate.decrease_count
            self.pressure_403_count = gate.pressure_403_count
            self.circuit_breaker_trips = gate.circuit_breaker_trips
            if host is not None:
                self.current_limit = gate.current_limit(host)
                self.circuit_breaker_frozen = gate.circuit_breaker_frozen(host)


@dataclass(frozen=True)
class FetchPolicy:
    """Configuration for :class:`TileFetcher`."""

    max_concurrent: int = _DEFAULT_MAX_CONCURRENT
    max_connections: int | None = None
    retries: int = 3
    timeout: float = 30.0
    rate_limit_per_second: float | None = _DEFAULT_RATE_LIMIT_PER_SECOND
    rate_limiter: HostRateLimiter | None = None
    adaptive_concurrency: bool = False
    initial_concurrent: int = _DEFAULT_INITIAL_CONCURRENT
    min_concurrent: int = _DEFAULT_MIN_CONCURRENT
    multiplicative_decrease: float = _DEFAULT_MULTIPLICATIVE_DECREASE
    additive_increase: int = _DEFAULT_ADDITIVE_INCREASE
    forbidden_circuit_breaker: bool = False
    forbidden_window_seconds: float = 5.0
    forbidden_threshold: int = 6
    forbidden_cooldown_seconds: float = 15.0

    def cache_key(self) -> tuple[Any, ...]:
        """Hashable key for sharing fetcher instances."""

        return (
            self.max_concurrent,
            self.max_connections,
            self.retries,
            self.timeout,
            self.rate_limit_per_second,
            self.adaptive_concurrency,
            self.initial_concurrent,
            self.min_concurrent,
            self.multiplicative_decrease,
            self.additive_increase,
            self.forbidden_circuit_breaker,
            self.forbidden_window_seconds,
            self.forbidden_threshold,
            self.forbidden_cooldown_seconds,
        )


@dataclass
class FetchProgress:
    """Thread-safe progress tracker for tile acquisition."""

    total: int
    on_progress: ProgressCallback | None = None
    done: int = field(default=0, init=False)
    errors: list[str] = field(default_factory=list, init=False)
    _aborted: bool = field(default=False, init=False)
    _abort_reason: str | None = field(default=None, init=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def abort(self, reason: str) -> None:
        """Stop sibling tile fetches after a hard failure."""

        with self._lock:
            if not self._aborted:
                self._aborted = True
                self._abort_reason = reason

    def is_aborted(self) -> bool:
        with self._lock:
            return self._aborted

    def check_not_aborted(self) -> None:
        """Raise :class:`~tilearray.errors.NetworkError` when the session aborted."""

        with self._lock:
            if self._aborted:
                raise NetworkError(
                    self._abort_reason or "Tile fetch aborted after earlier failure"
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
        pool_size = self._policy.max_connections or self._policy.max_concurrent
        self._client = httpx.Client(
            limits=httpx.Limits(
                max_connections=pool_size,
                max_keepalive_connections=self._policy.max_concurrent,
            ),
            timeout=httpx.Timeout(self._policy.timeout),
        )
        self._adaptive_gate: AdaptiveConcurrencyGate | None = None
        self._semaphore: threading.Semaphore | None = None
        if self._policy.adaptive_concurrency:
            self._adaptive_gate = _shared_adaptive_gate(self._policy)
            self.stats.peak_concurrency_limit = self._adaptive_gate.peak_limit
        else:
            self._semaphore = threading.Semaphore(self._policy.max_concurrent)
        if self._policy.rate_limiter is not None:
            self._rate_limiter: HostRateLimiter = self._policy.rate_limiter
        elif self._policy.rate_limit_per_second is not None:
            burst = (
                self._policy.initial_concurrent
                if self._policy.adaptive_concurrency
                else self._policy.max_concurrent
            )
            self._rate_limiter = TokenBucketRateLimiter(
                self._policy.rate_limit_per_second,
                burst=burst,
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

        global _default_fetcher
        with cls._instances_lock:
            for fetcher in cls._instances.values():
                fetcher.close()
            cls._instances.clear()
        reset_adaptive_gates()
        _default_fetcher = TileFetcher.for_policy(FetchPolicy())

    def _acquire_concurrency(self, host: str) -> None:
        if self._adaptive_gate is not None:
            self._adaptive_gate.acquire(host)
            return
        assert self._semaphore is not None
        self._semaphore.acquire()

    def _finish_success(self, host: str) -> None:
        if self._adaptive_gate is not None:
            self._adaptive_gate.finish_success(host)
            self.stats._sync_adaptive_stats(self._adaptive_gate, host)
            return
        assert self._semaphore is not None
        self._semaphore.release()

    def _finish_pressure(self, host: str) -> None:
        if self._adaptive_gate is not None:
            self._adaptive_gate.finish_pressure(host)
            self.stats._sync_adaptive_stats(self._adaptive_gate, host)
            return
        assert self._semaphore is not None
        self._semaphore.release()

    def _record_pressure(self, host: str) -> None:
        if self._adaptive_gate is not None:
            self._adaptive_gate.record_pressure(host)
            self.stats._sync_adaptive_stats(self._adaptive_gate, host)

    def _record_forbidden(self, host: str) -> None:
        if self._adaptive_gate is not None:
            self._adaptive_gate.record_forbidden(host)
            self.stats._sync_adaptive_stats(self._adaptive_gate, host)

    def fetch(
        self,
        request: TileRequest,
        *,
        progress: FetchProgress | None = None,
    ) -> TileResponse:
        if not request.url:
            raise ValueError("URL is required")

        if progress is not None:
            progress.check_not_aborted()

        headers = dict(request.headers or {})
        if request.output_format:
            headers.setdefault("Accept", request.output_format.value)

        host = urlparse(request.url).netloc or request.url
        attempts = max(request.retries, self._policy.retries) + 1

        def _before_sleep(retry_state: RetryCallState) -> None:
            if progress is not None:
                progress.check_not_aborted()
            self.stats._record_retry()

        @retry(
            retry=retry_if_exception_type((httpx.TransportError, RetryableHTTPError)),
            wait=_retry_wait,
            stop=stop_after_attempt(attempts),
            reraise=True,
            before_sleep=_before_sleep,
        )
        def _perform_get() -> httpx.Response:
            if progress is not None:
                progress.check_not_aborted()
            self._acquire_concurrency(host)
            try:
                if progress is not None:
                    progress.check_not_aborted()
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
            except httpx.TransportError:
                self._finish_pressure(host)
                raise

            if _is_retryable_http_response(response):
                self._finish_pressure(host)
                if response.status_code == 403:
                    self._record_forbidden(host)
                raise RetryableHTTPError(response, _parse_retry_after(response))

            self._finish_success(host)
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
    *,
    progress: FetchProgress | None = None,
) -> TileResponse:
    """Fetch a tile using the configured :class:`TileFetcher`."""

    return get_fetcher(policy).fetch(request, progress=progress)
