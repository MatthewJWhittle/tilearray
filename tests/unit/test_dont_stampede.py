"""Don't-stampede / process-local gate honesty (#59).

Offline contracts: shared in-process gates cap aggregate host in-flight;
independent worker pools (separate gates) can exceed a single-process ceiling;
concurrent mosaics still honour Retry-After / AIMD pressure inside one process.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import pytest
import respx

from tilearray.fetch import (
    FetchPolicy,
    FetchProgress,
    TileFetcher,
    reset_adaptive_gates,
)
from tilearray.types import Format, TileRequest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _tile_request(
    url: str = "https://example.com/tile", **overrides: Any
) -> TileRequest:
    defaults: dict[str, Any] = {
        "url": url,
        "params": {"layer": "a"},
        "retries": 0,
        "output_format": Format.PNG,
    }
    defaults.update(overrides)
    return TileRequest(**defaults)


@respx.mock
def test_shared_gate_caps_aggregate_inflight_across_sibling_sessions() -> None:
    """Sibling mosaics in one process share the host ceiling (aggregate ≤ N)."""

    host_ceiling = 2
    latency_s = 0.05
    active = 0
    peak = 0
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(latency_s)
        with lock:
            active -= 1
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(
        max_concurrent=host_ceiling,
        rate_limit_per_second=None,
        retries=0,
    )
    # Same cache_key → one shared fetcher / gate for both "worker pools".
    fetcher_a = TileFetcher.for_policy(policy)
    fetcher_b = TileFetcher.for_policy(policy)
    assert fetcher_a is fetcher_b

    request = _tile_request()

    def run_session(n_tiles: int) -> None:
        progress = FetchProgress(total=n_tiles)
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [
                pool.submit(fetcher_a.fetch, request, progress=progress)
                for _ in range(n_tiles)
            ]
            for future in futures:
                assert future.result().success is True

    with ThreadPoolExecutor(max_workers=2) as pools:
        futures = [pools.submit(run_session, 6) for _ in range(2)]
        for future in futures:
            future.result()

    assert peak <= host_ceiling
    assert fetcher_a.stats.max_inflight <= host_ceiling


@respx.mock
def test_independent_gates_can_exceed_single_process_host_ceiling() -> None:
    """Separate gates (multi-process shape) do not enforce a global ceiling."""

    per_gate_ceiling = 2
    latency_s = 0.08
    active = 0
    peak = 0
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(latency_s)
        with lock:
            active -= 1
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(
        max_concurrent=per_gate_ceiling,
        rate_limit_per_second=None,
        retries=0,
        adaptive_concurrency=False,
    )
    # Construct independently — models one gate per process (not for_policy share).
    fetchers = [TileFetcher(policy) for _ in range(2)]
    request = _tile_request()

    def run_pool(fetcher: TileFetcher) -> None:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(fetcher.fetch, request) for _ in range(4)]
            for future in futures:
                assert future.result().success is True

    with ThreadPoolExecutor(max_workers=2) as pools:
        futures = [pools.submit(run_pool, fetcher) for fetcher in fetchers]
        for future in futures:
            future.result()

    for fetcher in fetchers:
        fetcher.close()

    # Aggregate across independent gates can exceed any single process ceiling.
    assert peak > per_gate_ceiling
    assert peak <= per_gate_ceiling * 2


def _multiprocess_worker(base_url: str, n_requests: int, ceiling: int) -> None:
    """Child process: polite local ceiling only — no cross-process coordination."""

    TileFetcher.reset_instances()
    reset_adaptive_gates()
    policy = FetchPolicy(
        max_concurrent=ceiling,
        rate_limit_per_second=None,
        retries=0,
        adaptive_concurrency=False,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = TileRequest(
        url=f"{base_url}/tile",
        params={},
        retries=0,
        output_format=Format.PNG,
    )
    try:
        with ThreadPoolExecutor(max_workers=ceiling * 2) as pool:
            futures = [pool.submit(fetcher.fetch, request) for _ in range(n_requests)]
            for future in futures:
                assert future.result().success is True
    finally:
        TileFetcher.reset_instances()


def test_multiprocess_worker_pools_aggregate_inflight_exceeds_local_ceiling() -> None:
    """Fake host: N processes each at ceiling C → aggregate can exceed C."""

    per_process_ceiling = 2
    n_processes = 2
    latency_s = 0.1
    peak = {"value": 0}
    active = {"value": 0}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            with lock:
                active["value"] += 1
                peak["value"] = max(peak["value"], active["value"])
            time.sleep(latency_s)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.end_headers()
            self.wfile.write(b"x")
            with lock:
                active["value"] -= 1

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    host, port = server.server_address
    base_url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with ProcessPoolExecutor(max_workers=n_processes) as pool:
            futures = [
                pool.submit(
                    _multiprocess_worker,
                    base_url,
                    per_process_ceiling * 3,
                    per_process_ceiling,
                )
                for _ in range(n_processes)
            ]
            for future in futures:
                future.result()
    finally:
        server.shutdown()
        server.server_close()

    assert peak["value"] > per_process_ceiling
    assert peak["value"] <= per_process_ceiling * n_processes


@respx.mock
def test_concurrent_sessions_aimd_honours_retry_after_pressure() -> None:
    """Sibling sessions share AIMD; 429 + Retry-After backs off the process gate."""

    calls = {"n": 0}
    lock = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            calls["n"] += 1
            n = calls["n"]
        # Two pressure events are enough for AIMD decrease; later attempts succeed
        # so concurrent sessions do not exhaust retries.
        if n <= 2:
            return httpx.Response(429, headers={"Retry-After": "0"}, text="slow down")
        return httpx.Response(200, content=b"ok")

    respx.get("https://example.com/tile").mock(side_effect=handler)
    policy = FetchPolicy(
        max_concurrent=8,
        initial_concurrent=4,
        min_concurrent=1,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        retries=2,
    )
    fetcher = TileFetcher.for_policy(policy)
    request = _tile_request(retries=2)

    def one_session() -> None:
        progress = FetchProgress(total=2)
        results = [
            fetcher.fetch(request, progress=progress),
            fetcher.fetch(request, progress=progress),
        ]
        assert all(result.success for result in results)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(one_session) for _ in range(2)]
        for future in futures:
            future.result()

    gate = fetcher._adaptive_gate
    assert gate is not None
    # AIMD recorded pressure decreases (Retry-After 429 path) on the shared gate.
    assert fetcher.stats.concurrency_decreases >= 1
    assert gate.decrease_count >= 1
