"""Centralised tile HTTP fetching with bounded concurrency and retries."""

from __future__ import annotations

import logging
import math
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ._http_client import (
    DEFAULT_CONNECT_TIMEOUT,
    DEFAULT_HTTP_TIMEOUT,
    make_sync_client,
    peel_url_query,
    shape_httpx_timeout,
)
from .error_messages import (
    looks_like_html,
    looks_like_ogc_exception_report,
    redact_url,
)
from .errors import NetworkError, ValidationError
from .pressure import (
    DEFAULT_PRESSURE_CLASSIFIER,
    PressureClassifier,
    ResponseClassification,
    classify_response,
)
from .types import TileRequest, TileResponse

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, TileRequest, TileResponse], None]


_DEFAULT_MAX_CONCURRENT = 3
_DEFAULT_INITIAL_CONCURRENT = 2
_DEFAULT_MIN_CONCURRENT = 1
_DEFAULT_RATE_LIMIT_PER_SECOND = 2.0
_DEFAULT_MULTIPLICATIVE_DECREASE = 0.5
_DEFAULT_ADDITIVE_INCREASE = 2
_DEFAULT_CONNECT_TIMEOUT = DEFAULT_CONNECT_TIMEOUT
_DEFAULT_READ_TIMEOUT = DEFAULT_HTTP_TIMEOUT
_DEFAULT_TILE_DEADLINE = DEFAULT_HTTP_TIMEOUT
# Reference tile size for modest deadline scaling (256×256 XYZ / legacy WMS).
_REF_TILE_PIXELS = 256 * 256
_MAX_DEADLINE_SCALE = 4.0


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
            raise ValidationError("rate_per_second must be positive")
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
            raise ValidationError("rate_per_second must be positive")
        if burst <= 0:
            raise ValidationError("burst must be positive")
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
            raise ValidationError("concurrency limits must be positive")
        if minimum > initial:
            raise ValidationError("minimum cannot exceed initial")
        if initial > maximum:
            raise ValidationError("initial cannot exceed maximum")
        if not 0.0 < multiplicative_decrease < 1.0:
            raise ValidationError("multiplicative_decrease must be between 0 and 1")
        self._initial = initial
        self._minimum = minimum
        self._maximum = maximum
        self._additive_increase = additive_increase
        self._multiplicative_decrease = multiplicative_decrease
        self._forbidden_window_seconds = forbidden_window_seconds
        self._forbidden_threshold = forbidden_threshold
        self._forbidden_cooldown_seconds = forbidden_cooldown_seconds
        self._hosts: dict[str, _HostConcurrencyState] = {}
        self._pressure_events: dict[str, deque[float]] = {}
        self._frozen_until: dict[str, float] = {}
        self._map_lock = threading.Lock()
        self.decrease_count = 0
        self.peak_limit = initial
        self.pressure_event_count = 0
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

    def record_circuit_breaker_pressure(self, host: str) -> bool:
        """Record classified pressure and trip the breaker when events cluster."""

        if self._forbidden_window_seconds is None:
            return False

        now = time.monotonic()
        with self._map_lock:
            events = self._pressure_events.setdefault(host, deque())
            events.append(now)
            self.pressure_event_count += 1
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
    pressure_event_count: int = 0
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
            self.pressure_event_count = 0
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
            self.pressure_event_count = gate.pressure_event_count
            self.circuit_breaker_trips = gate.circuit_breaker_trips
            if host is not None:
                self.current_limit = gate.current_limit(host)
                self.circuit_breaker_frozen = gate.circuit_breaker_frozen(host)


class FetchPolicy(BaseModel):
    """Concurrency, retries, timeouts, rate limits, and optional AIMD / circuit-breaker
    settings for polite tile HTTP.

    ``max_concurrent`` is the **shared process-local gate ceiling** (host / preset
    limit). Threads in one Python process that resolve the same
    :meth:`cache_key` share one concurrency gate. Separate OS processes and Dask
    distributed workers each get their own gate — there is no cross-process host
    ceiling.

    ``call_max_concurrent`` is an optional **per-call / per-session** limit. It is
    not part of :meth:`cache_key`, so one caller's local politeness cannot shrink
    the shared gate for siblings. Apply it via :class:`FetchProgress` (see
    ``call_max_concurrent`` there) so each mosaic / call gets its own limiter.

    Field bounds match :class:`~tilearray.service.config.ServiceConfig` fetch
    knobs so invalid policies fail at construction instead of later in the gate.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    max_concurrent: int = Field(default=_DEFAULT_MAX_CONCURRENT, ge=1)
    max_connections: int | None = Field(default=None, ge=1)
    retries: int = Field(
        default=3,
        ge=0,
        description=(
            "Retry budget for retryable HTTP/network failures (attempts = "
            "retries + 1). Authoritative over TileRequest.retries."
        ),
    )
    connect_timeout: float = Field(
        default=_DEFAULT_CONNECT_TIMEOUT,
        gt=0,
        description="TCP connect timeout in seconds (httpx connect=)",
    )
    read_timeout: float = Field(
        default=_DEFAULT_READ_TIMEOUT,
        gt=0,
        description=(
            "Idle time between response body chunks (httpx read=). Not a "
            "whole-response wall clock — see tile_deadline."
        ),
    )
    tile_deadline: float = Field(
        default=_DEFAULT_TILE_DEADLINE,
        gt=0,
        description=(
            "Hard wall-clock deadline per tile attempt in seconds (application "
            "timer on top of httpx; scaled modestly with request width×height)"
        ),
    )
    rate_limit_per_second: float | None = _DEFAULT_RATE_LIMIT_PER_SECOND
    rate_limiter: HostRateLimiter | None = None
    adaptive_concurrency: bool = False
    initial_concurrent: int = Field(default=_DEFAULT_INITIAL_CONCURRENT, ge=1)
    min_concurrent: int = Field(default=_DEFAULT_MIN_CONCURRENT, ge=1)
    multiplicative_decrease: float = Field(
        default=_DEFAULT_MULTIPLICATIVE_DECREASE, gt=0.0, lt=1.0
    )
    additive_increase: int = Field(default=_DEFAULT_ADDITIVE_INCREASE, ge=1)
    forbidden_circuit_breaker: bool = False
    forbidden_window_seconds: float = Field(default=5.0, gt=0)
    forbidden_threshold: int = Field(default=6, ge=1)
    forbidden_cooldown_seconds: float = Field(default=15.0, gt=0)
    pressure_classifier: PressureClassifier | None = None
    call_max_concurrent: int | None = Field(default=None, ge=1)
    max_tile_requests: int = Field(
        default=10_000,
        ge=1,
        description=(
            "Refuse (create) or estimate-only (preview) when a tile plan would "
            "exceed this many requests"
        ),
    )

    def cache_key(self) -> tuple[Any, ...]:
        """Hashable key for sharing fetcher instances (shared gate identity).

        Excludes ``call_max_concurrent`` so per-call limits do not create a
        separate process-wide gate or collide siblings onto a Semaphore(1).
        Excludes ``max_tile_requests`` (planning guard, not HTTP identity).
        """

        classifier = self.pressure_classifier
        classifier_key = tuple(classifier.signatures) if classifier is not None else ()
        return (
            self.max_concurrent,
            self.max_connections,
            self.retries,
            self.connect_timeout,
            self.read_timeout,
            self.tile_deadline,
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
            classifier_key,
        )


def tile_deadline_seconds(policy: FetchPolicy, request: TileRequest) -> float:
    """Wall-clock deadline for one tile attempt, scaled by request pixel size.

    Larger WMS/WCS tiles get up to :data:`_MAX_DEADLINE_SCALE` × the base
    ``tile_deadline`` via ``sqrt(pixels / 256²)``. Missing or non-positive
    width/height keep the base deadline.
    """

    base = float(policy.tile_deadline)
    width = request.width
    height = request.height
    if width is None or height is None or width <= 0 or height <= 0:
        return base
    scale = math.sqrt((width * height) / float(_REF_TILE_PIXELS))
    return base * min(_MAX_DEADLINE_SCALE, max(1.0, scale))


# Lazy mosaics store a JSON-safe token in attrs; the live session lives here so
# DataArray copy / pickle / to_zarr never have to serialise a Lock.
_PROGRESS_REGISTRY: dict[str, FetchProgress] = {}


def register_fetch_progress(progress: FetchProgress) -> str:
    """Register ``progress`` and return a JSON-safe lookup token."""

    token = uuid.uuid4().hex
    _PROGRESS_REGISTRY[token] = progress
    return token


def lookup_fetch_progress(token: object) -> FetchProgress | None:
    """Resolve a progress token (or legacy in-attrs handle) to a live session."""

    if isinstance(token, FetchProgress):
        return token
    if isinstance(token, str):
        return _PROGRESS_REGISTRY.get(token)
    return None


def unregister_fetch_progress(token: object) -> None:
    """Drop a registry entry when the mosaic no longer needs the live session."""

    if isinstance(token, str):
        _PROGRESS_REGISTRY.pop(token, None)


# Exhausted pressure failures eligible for one deferred mosaic retry pass.
# Permanent client errors (401/403/404) and decode/parse stay fail-fast.
_DEFERRED_RETRY_STATUSES = frozenset({0, 408, 429, 500, 502, 503, 504})
DEFAULT_DEFERRED_PARK_FRACTION = 0.1


def is_deferred_retry_failure(response: TileResponse) -> bool:
    """Return True when an exhausted tile failure is transient host pressure."""

    if response.success:
        return False
    return int(response.status_code) in _DEFERRED_RETRY_STATUSES


def deferred_park_cap(
    total_tiles: int, *, fraction: float = DEFAULT_DEFERRED_PARK_FRACTION
) -> int:
    """Maximum tiles that may park for the deferred retry pass."""

    if total_tiles < 1:
        return 0
    return max(1, math.ceil(fraction * total_tiles))


def retry_after_seconds(response: TileResponse) -> float:
    """Parse ``Retry-After`` from a failed tile response (0 when absent/invalid)."""

    header = None
    if response.headers:
        header = response.headers.get("Retry-After") or response.headers.get(
            "retry-after"
        )
    if not header:
        return 0.0
    try:
        return max(float(header), 0.0)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class FetchProgress:
    """Thread-safe progress tracker for tile acquisition.

    Optional ``call_max_concurrent`` limits in-flight fetches for **this**
    progress session only (one mosaic / one logical call). It does not redefine
    the process-wide :class:`TileFetcher` gate.

    ``deferred_max_concurrent`` caps in-flight HTTP during the one deferred
    retry pass for parked pressure failures (AIMD floor). ``deferred_park_fraction``
    budgets how many tiles may park before the pass (default 10%).

    Not meant to live on ``DataArray.attrs``: use :func:`register_fetch_progress`
    and store the returned token. Pickle / deepcopy rebuild a fresh lock and drop
    the callback / semaphore so graph clones cannot fail on ``_thread.lock``.
    """

    total: int
    on_progress: ProgressCallback | None = None
    call_max_concurrent: int | None = None
    deferred_max_concurrent: int | None = None
    deferred_park_fraction: float = DEFAULT_DEFERRED_PARK_FRACTION
    done: int = field(default=0, init=False)
    errors: list[str] = field(default_factory=list, init=False)
    soft_failed: int = field(default=0, init=False)
    soft_failed_urls: list[str] = field(default_factory=list, init=False)
    deferred_parked: int = field(default=0, init=False)
    deferred_recovered: int = field(default=0, init=False)
    deferred_failed: int = field(default=0, init=False)
    _aborted: bool = field(default=False, init=False)
    _abort_reason: str | None = field(default=None, init=False)
    _primary_resolved: int = field(default=0, init=False)
    _parked_waiting: int = field(default=0, init=False)
    _deferred_pass_open: bool = field(default=False, init=False)
    _call_semaphore: threading.Semaphore | None = field(
        default=None, init=False, repr=False
    )
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )
    _condition: threading.Condition = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if self.call_max_concurrent is not None:
            if self.call_max_concurrent < 1:
                raise ValidationError("call_max_concurrent must be >= 1")
            self._call_semaphore = threading.Semaphore(self.call_max_concurrent)
        if (
            self.deferred_max_concurrent is not None
            and self.deferred_max_concurrent < 1
        ):
            raise ValidationError("deferred_max_concurrent must be >= 1")
        if not 0.0 < self.deferred_park_fraction <= 1.0:
            raise ValidationError("deferred_park_fraction must be in (0, 1]")
        self._condition = threading.Condition(self._lock)

    def __getstate__(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "on_progress": None,
            "call_max_concurrent": self.call_max_concurrent,
            "deferred_max_concurrent": self.deferred_max_concurrent,
            "deferred_park_fraction": self.deferred_park_fraction,
            "done": self.done,
            "errors": list(self.errors),
            "soft_failed": self.soft_failed,
            "soft_failed_urls": list(self.soft_failed_urls),
            "deferred_parked": self.deferred_parked,
            "deferred_recovered": self.deferred_recovered,
            "deferred_failed": self.deferred_failed,
            "_aborted": self._aborted,
            "_abort_reason": self._abort_reason,
            "_primary_resolved": self._primary_resolved,
            "_parked_waiting": self._parked_waiting,
            "_deferred_pass_open": self._deferred_pass_open,
        }

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.total = int(state["total"])
        self.on_progress = state.get("on_progress")
        self.call_max_concurrent = state.get("call_max_concurrent")
        self.deferred_max_concurrent = state.get("deferred_max_concurrent")
        self.deferred_park_fraction = float(
            state.get("deferred_park_fraction", DEFAULT_DEFERRED_PARK_FRACTION)
        )
        self.done = int(state.get("done", 0))
        self.errors = list(state.get("errors") or [])
        self.soft_failed = int(state.get("soft_failed", 0))
        self.soft_failed_urls = list(state.get("soft_failed_urls") or [])
        self.deferred_parked = int(state.get("deferred_parked", 0))
        self.deferred_recovered = int(state.get("deferred_recovered", 0))
        self.deferred_failed = int(state.get("deferred_failed", 0))
        self._aborted = bool(state.get("_aborted", False))
        self._abort_reason = state.get("_abort_reason")
        self._primary_resolved = int(state.get("_primary_resolved", 0))
        self._parked_waiting = int(state.get("_parked_waiting", 0))
        self._deferred_pass_open = bool(state.get("_deferred_pass_open", False))
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._call_semaphore = None
        if self.call_max_concurrent is not None:
            if self.call_max_concurrent < 1:
                raise ValidationError("call_max_concurrent must be >= 1")
            self._call_semaphore = threading.Semaphore(self.call_max_concurrent)

    def abort(self, reason: str) -> None:
        """Stop sibling tile fetches after a hard failure."""

        with self._condition:
            if not self._aborted:
                self._aborted = True
                self._abort_reason = reason
            # Wake parked deferred waiters so they can raise.
            self._deferred_pass_open = True
            self._condition.notify_all()

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

    def acquire_call_slot(self) -> None:
        """Acquire this session's local concurrency slot (no-op if unlimited)."""

        if self._call_semaphore is not None:
            self._call_semaphore.acquire()

    def release_call_slot(self) -> None:
        """Release this session's local concurrency slot (no-op if unlimited)."""

        if self._call_semaphore is not None:
            self._call_semaphore.release()

    def record_soft_fail(self, request: TileRequest) -> None:
        """Count a visible soft-fill (does not abort siblings)."""

        with self._lock:
            self.soft_failed += 1
            self.soft_failed_urls.append(request.url)

    def soft_failed_count(self) -> int:
        with self._lock:
            return self.soft_failed

    def mark_primary_resolved(self, *, allow_deferred_open: bool = True) -> None:
        """Count a tile that finished its primary attempt (success or hard-fail)."""

        with self._condition:
            self._primary_resolved += 1
            if allow_deferred_open:
                self._maybe_open_deferred_pass_locked()

    def try_park_for_deferred_retry(self) -> bool:
        """Park this tile for the deferred pass if under the park budget.

        Returns ``False`` when the park cap is already full (caller should abort).
        """

        with self._condition:
            cap = deferred_park_cap(self.total, fraction=self.deferred_park_fraction)
            if self.deferred_parked >= cap:
                return False
            self.deferred_parked += 1
            self._parked_waiting += 1
            self._primary_resolved += 1
            self._maybe_open_deferred_pass_locked()
            return True

    def _maybe_open_deferred_pass_locked(self) -> None:
        if self._deferred_pass_open:
            return
        if self._primary_resolved < self.total:
            return
        if self._parked_waiting < 1 and not self._aborted:
            return
        self._install_deferred_concurrency_locked()
        self._deferred_pass_open = True
        self._condition.notify_all()

    def _install_deferred_concurrency_locked(self) -> None:
        """Cap call-local in-flight at the AIMD floor for the deferred pass."""

        floor = self.deferred_max_concurrent
        if floor is None:
            floor = 1
        if self.call_max_concurrent is not None:
            floor = min(floor, self.call_max_concurrent)
        floor = max(1, int(floor))
        self._call_semaphore = threading.Semaphore(floor)

    def wait_for_deferred_pass(self, *, retry_after: float = 0.0) -> None:
        """Block until the deferred pass opens (or the mosaic aborts)."""

        with self._condition:
            while not self._deferred_pass_open and not self._aborted:
                self._condition.wait(timeout=0.05)
            if self._aborted:
                raise NetworkError(
                    self._abort_reason or "Tile fetch aborted after earlier failure"
                )
        cooldown = max(float(retry_after), 0.0)
        if cooldown > 0.0:
            time.sleep(cooldown)

    def record_deferred_outcome(self, *, recovered: bool) -> None:
        """Record whether a parked tile recovered on the deferred pass."""

        with self._lock:
            if recovered:
                self.deferred_recovered += 1
            else:
                self.deferred_failed += 1

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
    content_type = response.headers.get("content-type", "")
    body = response.content
    # 200 is not enough: portals and OGC ExceptionReports often return 200 with
    # text/html or a misleading image/* Content-Type. Treat those as fetch fails
    # so callers get HTML/OGC hints instead of Pillow "cannot identify image".
    if response.status_code == 200:
        if looks_like_html(body, content_type=content_type):
            body_snip = response.text[:200]
            return TileResponse(
                data=b"",
                content_type=content_type,
                status_code=response.status_code,
                headers=dict(response.headers),
                url=redact_url(str(response.url)),
                success=False,
                error_message=(
                    f"HTTP 200 (HTML body, not tile bytes / login portal): {body_snip}"
                ),
            )
        if looks_like_ogc_exception_report(body):
            body_snip = response.text[:200]
            return TileResponse(
                data=b"",
                content_type=content_type,
                status_code=response.status_code,
                headers=dict(response.headers),
                url=redact_url(str(response.url)),
                success=False,
                error_message=(
                    f"HTTP 200 (OGC ExceptionReport, not tile bytes): {body_snip}"
                ),
            )
        return TileResponse(
            data=body,
            content_type=content_type,
            status_code=response.status_code,
            headers=dict(response.headers),
            url=str(response.url),
            success=True,
        )

    body_snip = response.text[:200]
    error_msg = f"HTTP {response.status_code}: {body_snip}"
    if looks_like_html(response.text, content_type=content_type):
        error_msg = (
            f"HTTP {response.status_code} (HTML body, not tile bytes): {body_snip}"
        )
    elif looks_like_ogc_exception_report(response.text):
        error_msg = f"HTTP {response.status_code} (OGC ExceptionReport): {body_snip}"
    return TileResponse(
        data=b"",
        content_type=content_type,
        status_code=response.status_code,
        headers=dict(response.headers),
        url=redact_url(str(response.url)),
        success=False,
        error_message=error_msg,
    )


class TileFetcher:
    """Sync tile fetcher backed by a shared :class:`httpx.Client`."""

    _instances: dict[tuple[Any, ...], TileFetcher] = {}
    _instances_lock = threading.Lock()
    _serial_warn_lock = threading.Lock()
    _serial_warned_keys: set[tuple[Any, ...]] = set()

    def __init__(self, policy: FetchPolicy | None = None) -> None:
        self._policy = policy or FetchPolicy()
        self._pressure_classifier = (
            self._policy.pressure_classifier or DEFAULT_PRESSURE_CLASSIFIER
        )
        self.stats = FetchStats()
        pool_size = self._policy.max_connections or self._policy.max_concurrent
        self._client = make_sync_client(
            timeout=shape_httpx_timeout(
                connect=self._policy.connect_timeout,
                read=self._policy.read_timeout,
            ),
            limits=httpx.Limits(
                max_connections=pool_size,
                max_keepalive_connections=self._policy.max_concurrent,
            ),
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
        """Return a shared fetcher instance for the given policy.

        Instances are keyed by :meth:`FetchPolicy.cache_key` (shared gate
        identity). ``call_max_concurrent`` is ignored for sharing — apply it on
        :class:`FetchProgress` so it stays local to one call/session.
        """

        effective = policy or FetchPolicy()
        key = effective.cache_key()
        with cls._instances_lock:
            fetcher = cls._instances.get(key)
            if fetcher is None:
                # Shared gate uses max_concurrent only; strip call-local limit.
                gate_policy = (
                    effective.model_copy(update={"call_max_concurrent": None})
                    if effective.call_max_concurrent is not None
                    else effective
                )
                fetcher = cls(gate_policy)
                cls._instances[key] = fetcher
            return fetcher

    @classmethod
    def get_if_cached(cls, policy: FetchPolicy | None = None) -> TileFetcher | None:
        """Return an existing shared fetcher for ``policy``, or ``None``.

        Unlike :meth:`for_policy`, this never constructs a fetcher — useful for
        read-only summaries that must not start HTTP clients as a side effect.
        """

        effective = policy or FetchPolicy()
        with cls._instances_lock:
            return cls._instances.get(effective.cache_key())

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
            cls._serial_warned_keys.clear()
        reset_adaptive_gates()
        _default_fetcher = TileFetcher.for_policy(FetchPolicy())

    def _warn_if_serial_multi_waiter(self) -> None:
        """Log once when a shared gate of 1 has another waiter contending."""

        if self._policy.max_concurrent != 1:
            return
        key = self._policy.cache_key()
        with self._serial_warn_lock:
            if key in self._serial_warned_keys:
                return
            self._serial_warned_keys.add(key)
        logger.warning(
            "Shared fetch concurrency is 1 while multiple callers are waiting. "
            "FetchPolicy.max_concurrent is the process-local gate (host/preset "
            "ceiling for this Python process); use "
            "FetchProgress(call_max_concurrent=...) or "
            "FetchPolicy.call_max_concurrent for per-call politeness so siblings "
            "are not serialised. Separate processes do not share this gate."
        )

    def _acquire_concurrency(self, host: str) -> None:
        if self._adaptive_gate is not None:
            gate = self._adaptive_gate
            state = gate._state_for(host)
            with state.condition:
                if state.inflight >= state.limit and state.limit <= 1:
                    self._warn_if_serial_multi_waiter()
            gate.acquire(host)
            return
        assert self._semaphore is not None
        acquired = self._semaphore.acquire(blocking=False)
        if not acquired:
            self._warn_if_serial_multi_waiter()
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

    def _classify_response(self, response: httpx.Response) -> ResponseClassification:
        return classify_response(response, self._pressure_classifier)

    def _record_circuit_breaker_pressure(self, host: str) -> None:
        if self._adaptive_gate is not None:
            self._adaptive_gate.record_circuit_breaker_pressure(host)
            self.stats._sync_adaptive_stats(self._adaptive_gate, host)

    def _attempt_timeout(self, deadline_at: float) -> httpx.Timeout:
        """Cap httpx connect/read by remaining wall-clock deadline for this attempt."""

        remaining = max(1e-3, deadline_at - time.monotonic())
        return shape_httpx_timeout(
            connect=min(self._policy.connect_timeout, remaining),
            read=min(self._policy.read_timeout, remaining),
        )

    def _read_body_with_deadline(
        self,
        response: httpx.Response,
        deadline_at: float,
    ) -> bytes:
        """Consume a streamed body; abort if wall-clock deadline elapses (drip stalls)."""

        chunks: list[bytes] = []
        for chunk in response.iter_bytes():
            if time.monotonic() >= deadline_at:
                raise httpx.ReadTimeout(
                    "Per-tile wall-clock deadline exceeded",
                    request=response.request,
                )
            if chunk:
                chunks.append(chunk)
        return b"".join(chunks)

    def _get_with_deadline(
        self,
        *,
        url: str,
        params: dict[str, Any] | None,
        headers: dict[str, str],
        deadline_at: float,
    ) -> httpx.Response:
        """GET one attempt under httpx connect/read timeouts + wall-clock deadline."""

        timeout = self._attempt_timeout(deadline_at)
        with self._client.stream(
            "GET",
            url,
            params=params,
            headers=headers,
            timeout=timeout,
        ) as streamed:
            content = self._read_body_with_deadline(streamed, deadline_at)
            return httpx.Response(
                status_code=streamed.status_code,
                headers=streamed.headers,
                content=content,
                request=streamed.request,
            )

    def fetch(
        self,
        request: TileRequest,
        *,
        progress: FetchProgress | None = None,
    ) -> TileResponse:
        if not request.url:
            raise ValidationError("URL is required")

        if progress is not None:
            progress.check_not_aborted()

        headers = dict(request.headers or {})
        if request.output_format:
            headers.setdefault("Accept", request.output_format.value)

        host = urlparse(request.url).netloc or request.url
        # FetchPolicy.retries (from fetch_retries= on configs) is the attempt
        # budget. Do not max() with TileRequest.retries — that default (3)
        # silently overrode fetch_retries=0 into four attempts (#170).
        # Explicit TileRequest.retries == 0 is a fail-fast ceiling (e.g. WCS
        # scaled GetCoverage that must not burn retries on hang/5xx — #177).
        attempts = self._policy.retries + 1
        if request.retries == 0:
            attempts = 1
        deadline_s = tile_deadline_seconds(self._policy, request)
        # Per-signature retry counts for PressureSignature.max_retries caps
        # (e.g. EA preset bounded 404) within this one tile fetch.
        signature_retry_counts: dict[str, int] = {}

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
                progress.acquire_call_slot()
            try:
                self._acquire_concurrency(host)
                try:
                    if progress is not None:
                        progress.check_not_aborted()
                    self._rate_limiter.before_request(host)
                    self.stats._enter_inflight()
                    try:
                        self.stats._record_request()
                        logger.debug("Fetching tile: %s", request.url)
                        bare_url, get_params = peel_url_query(
                            request.url, request.params
                        )
                        deadline_at = time.monotonic() + deadline_s
                        response = self._get_with_deadline(
                            url=bare_url,
                            params=get_params or None,
                            headers=headers,
                            deadline_at=deadline_at,
                        )
                    finally:
                        self.stats._leave_inflight()
                except httpx.TransportError:
                    self._finish_pressure(host)
                    raise

                classification = self._classify_response(response)
                if classification.retryable:
                    sig = classification.signature
                    if sig is not None and classification.max_retries is not None:
                        used = signature_retry_counts.get(sig.name, 0)
                        if used >= classification.max_retries:
                            # Bounded signature budget exhausted — treat as
                            # final failure without another RetryableHTTPError.
                            self._finish_success(host)
                            return response
                        signature_retry_counts[sig.name] = used + 1
                    self._finish_pressure(host)
                    if classification.circuit_breaker:
                        self._record_circuit_breaker_pressure(host)
                    raise RetryableHTTPError(response, _parse_retry_after(response))

                self._finish_success(host)
                return response
            finally:
                if progress is not None:
                    progress.release_call_slot()

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
