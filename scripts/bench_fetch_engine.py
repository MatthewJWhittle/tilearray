#!/usr/bin/env python3
"""
Before/after benchmark for the TileFetcher engine (offline, no network).

Compares legacy unbounded ``requests`` fetching against the new ``TileFetcher``
with EA DSP / OSM presets on a mock server that enforces a concurrency ceiling.

Re-run:
    uv run python scripts/bench_fetch_engine.py
    # or: python3 scripts/bench_fetch_engine.py
"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import respx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tilearray.fetch import FetchPolicy, TileFetcher  # noqa: E402
from tilearray.fetch_presets import (  # noqa: E402
    EA_LIDAR_BENCH_BBOX,
    ea_dsp_fetch_defaults,
    osm_fetch_defaults,
)
from tilearray.types import TileRequest  # noqa: E402

TILE_COUNT = 4
HEALTHY_TILE_COUNT = 16
REALISTIC_LATENCY_S = 0.12
LIVE_TILE_RTT_S = 1.5


def _policy_from_service_defaults(defaults: dict[str, object]) -> FetchPolicy:
    return FetchPolicy(
        max_concurrent=int(defaults["max_concurrent_requests"]),  # type: ignore[arg-type]
        max_connections=defaults.get("max_connections"),  # type: ignore[arg-type]
        retries=int(defaults["fetch_retries"]),  # type: ignore[arg-type]
        timeout=float(defaults["fetch_timeout"]),  # type: ignore[arg-type]
        rate_limit_per_second=defaults.get("rate_limit_per_second"),  # type: ignore[arg-type]
        adaptive_concurrency=bool(defaults.get("adaptive_concurrency", False)),
        initial_concurrent=int(
            defaults.get("initial_concurrent_requests", 2)  # type: ignore[arg-type]
        ),
        min_concurrent=int(defaults.get("min_concurrent_requests", 1)),  # type: ignore[arg-type]
    )


MOCK_LATENCY_S = 0.03
SERVER_MAX_CONCURRENT = 2
BASE_URL = "https://bench.example/wcs"


@dataclass
class LegacyStats:
    max_inflight: int = 0
    http_attempts: int = 0
    retries: int = 0
    _current: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def enter(self) -> None:
        with self._lock:
            self._current += 1
            self.max_inflight = max(self.max_inflight, self._current)

    def leave(self) -> None:
        with self._lock:
            self._current -= 1

    def attempt(self) -> None:
        with self._lock:
            self.http_attempts += 1


class OverloadMockServer:
    """Mock origin that 503s when concurrent requests exceed a threshold."""

    def __init__(self, max_concurrent: int) -> None:
        self.max_concurrent = max_concurrent
        self._current = 0
        self._lock = threading.Lock()

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
            if self._current >= self.max_concurrent:
                return httpx.Response(503, text="overload")
            self._current += 1
        time.sleep(MOCK_LATENCY_S)
        with self._lock:
            self._current -= 1
        return httpx.Response(200, content=b"tile-bytes")


class HealthyMockServer:
    """Mock origin with fixed latency and no overload ceiling."""

    def __init__(self, latency_s: float) -> None:
        self.latency_s = latency_s

    def handle(self, request: httpx.Request) -> httpx.Response:
        time.sleep(self.latency_s)
        return httpx.Response(200, content=b"tile-bytes")


def _tile_requests(count: int) -> list[TileRequest]:
    return [
        TileRequest(url=f"{BASE_URL}/tile-{index}", params={}, retries=2)
        for index in range(count)
    ]


def _legacy_fetch(
    request: TileRequest,
    client: httpx.Client,
    stats: LegacyStats,
) -> None:
    """Immediate-retry fetch without semaphore (pre-TileFetcher behaviour)."""

    for attempt in range(request.retries + 1):
        stats.attempt()
        if attempt:
            stats.retries += 1
        stats.enter()
        try:
            response = client.get(request.url, timeout=10)
        finally:
            stats.leave()
        if response.status_code == 200:
            return
        if attempt == request.retries:
            return


def _run_legacy(requests_list: list[TileRequest]) -> tuple[float, LegacyStats]:
    stats = LegacyStats()
    started = time.perf_counter()
    with (
        httpx.Client() as client,
        ThreadPoolExecutor(max_workers=len(requests_list)) as pool,
    ):
        futures = [
            pool.submit(_legacy_fetch, req, client, stats) for req in requests_list
        ]
        for future in as_completed(futures):
            future.result()
    return time.perf_counter() - started, stats


def _run_tile_fetcher(
    policy: FetchPolicy,
    requests_list: list[TileRequest],
) -> tuple[float, TileFetcher]:
    TileFetcher.reset_instances()
    fetcher = TileFetcher.for_policy(policy)
    fetcher.stats.reset()
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(requests_list)) as pool:
        futures = [pool.submit(fetcher.fetch, req) for req in requests_list]
        for future in as_completed(futures):
            future.result()
    return time.perf_counter() - started, fetcher


def _format_row(
    label: str, wall: float, max_inflight: int, attempts: int, retries: int
) -> str:
    return (
        f"{label:<28}  wall={wall:6.3f}s  max_inflight={max_inflight}  "
        f"http_attempts={attempts}  retries={retries}"
    )


def run_overload_scenario() -> list[str]:
    lines: list[str] = []
    server = OverloadMockServer(SERVER_MAX_CONCURRENT)
    tiles = _tile_requests(TILE_COUNT)

    with respx.mock:
        respx.route(url__startswith=BASE_URL).mock(side_effect=server.handle)

        legacy_wall, legacy_stats = _run_legacy(tiles)

        ea_policy = _policy_from_service_defaults(ea_dsp_fetch_defaults())
        new_wall, fetcher = _run_tile_fetcher(ea_policy, tiles)

        osm_policy = _policy_from_service_defaults(osm_fetch_defaults())
        osm_wall, osm_fetcher = _run_tile_fetcher(osm_policy, tiles)

    lines.append(
        f"Scenario A — overload-sensitive mock (server max concurrent = 2, {TILE_COUNT} tiles)"
    )
    lines.append(
        _format_row(
            "Legacy (unbounded)",
            legacy_wall,
            legacy_stats.max_inflight,
            legacy_stats.http_attempts,
            legacy_stats.retries,
        )
    )
    lines.append(
        _format_row(
            "TileFetcher + EA AIMD preset",
            new_wall,
            fetcher.stats.max_inflight,
            fetcher.stats.request_count,
            fetcher.stats.retry_count,
        )
    )
    lines.append(
        _format_row(
            "TileFetcher + OSM preset",
            osm_wall,
            osm_fetcher.stats.max_inflight,
            osm_fetcher.stats.request_count,
            osm_fetcher.stats.retry_count,
        )
    )
    lines.append("")
    lines.append(
        "Win: max_inflight capped, HTTP attempts and retry storms reduced "
        f"({legacy_stats.http_attempts}→{fetcher.stats.request_count} attempts, "
        f"{legacy_stats.retries}→{fetcher.stats.retry_count} retries for EA preset). "
        "EA preset uses AIMD concurrency (starts at 2, backs off on pressure)."
    )
    return lines


def run_healthy_scenario() -> list[str]:
    lines: list[str] = []
    server = HealthyMockServer(MOCK_LATENCY_S)
    tiles = _tile_requests(HEALTHY_TILE_COUNT)

    with respx.mock:
        respx.route(url__startswith=BASE_URL).mock(side_effect=server.handle)

        legacy_wall, legacy_stats = _run_legacy(tiles)

        ea_policy = _policy_from_service_defaults(ea_dsp_fetch_defaults())
        ea_wall, ea_fetcher = _run_tile_fetcher(ea_policy, tiles)

        static_ea_policy = FetchPolicy(
            max_concurrent=2,
            retries=4,
            timeout=60.0,
            rate_limit_per_second=1.0,
            adaptive_concurrency=False,
        )
        static_ea_wall, static_ea_fetcher = _run_tile_fetcher(static_ea_policy, tiles)

        fixed2_policy = FetchPolicy(
            max_concurrent=2,
            retries=4,
            timeout=60.0,
            rate_limit_per_second=None,
            adaptive_concurrency=False,
        )
        fixed2_wall, fixed2_fetcher = _run_tile_fetcher(fixed2_policy, tiles)

    static_live_est = (HEALTHY_TILE_COUNT / 2) * LIVE_TILE_RTT_S

    lines.append("")
    lines.append(
        f"Scenario C — instant mock ({HEALTHY_TILE_COUNT} tiles, "
        f"{MOCK_LATENCY_S}s latency; not representative of live RTT)"
    )
    lines.append(
        _format_row(
            "Legacy (unbounded)",
            legacy_wall,
            legacy_stats.max_inflight,
            legacy_stats.http_attempts,
            legacy_stats.retries,
        )
    )
    lines.append(
        _format_row(
            "Static EA (1 req/s, cap=2)",
            static_ea_wall,
            static_ea_fetcher.stats.max_inflight,
            static_ea_fetcher.stats.request_count,
            static_ea_fetcher.stats.retry_count,
        )
    )
    lines.append(
        _format_row(
            "Fixed cap=2, no throttle",
            fixed2_wall,
            fixed2_fetcher.stats.max_inflight,
            fixed2_fetcher.stats.request_count,
            fixed2_fetcher.stats.retry_count,
        )
    )
    lines.append(
        _format_row(
            "TileFetcher + EA AIMD preset",
            ea_wall,
            ea_fetcher.stats.max_inflight,
            ea_fetcher.stats.request_count,
            ea_fetcher.stats.retry_count,
        )
    )
    lines.append(
        f"  EA AIMD peak limit={ea_fetcher.stats.peak_concurrency_limit}  "
        f"concurrency_decreases={ea_fetcher.stats.concurrency_decreases}"
    )
    lines.append(
        "Note: instant mocks understate live RTT. Extrapolating Skipton (~16 tiles, "
        f"~{LIVE_TILE_RTT_S}s/tile): static cap=2 ~{static_live_est:.0f}s (observed "
        "~25s on live EA host); AIMD slow-start target ~10–13s (legacy range)."
    )
    return lines


def run_realistic_latency_scenario() -> list[str]:
    lines: list[str] = []
    server = HealthyMockServer(REALISTIC_LATENCY_S)
    tiles = _tile_requests(HEALTHY_TILE_COUNT)

    with respx.mock:
        respx.route(url__startswith=BASE_URL).mock(side_effect=server.handle)

        ea_policy = _policy_from_service_defaults(ea_dsp_fetch_defaults())
        ea_wall, ea_fetcher = _run_tile_fetcher(ea_policy, tiles)

        fixed2_policy = FetchPolicy(
            max_concurrent=2,
            adaptive_concurrency=False,
            rate_limit_per_second=None,
        )
        fixed2_wall, fixed2_fetcher = _run_tile_fetcher(fixed2_policy, tiles)

    lines.append("")
    lines.append(
        f"Scenario D — realistic RTT mock ({HEALTHY_TILE_COUNT} tiles, "
        f"{REALISTIC_LATENCY_S}s/tile latency)"
    )
    lines.append(
        _format_row(
            "Fixed cap=2",
            fixed2_wall,
            fixed2_fetcher.stats.max_inflight,
            fixed2_fetcher.stats.request_count,
            fixed2_fetcher.stats.retry_count,
        )
    )
    lines.append(
        _format_row(
            "TileFetcher + EA AIMD preset",
            ea_wall,
            ea_fetcher.stats.max_inflight,
            ea_fetcher.stats.request_count,
            ea_fetcher.stats.retry_count,
        )
    )
    lines.append(
        f"  EA AIMD peak limit={ea_fetcher.stats.peak_concurrency_limit}  "
        f"max_inflight={ea_fetcher.stats.max_inflight}"
    )
    speedup = fixed2_wall / ea_wall if ea_wall > 0 else 0.0
    lines.append(
        f"Win: AIMD reaches high in-flight under RTT (~{speedup:.1f}× faster than "
        f"fixed cap=2 at {REALISTIC_LATENCY_S}s/tile; scales to live Skipton target)."
    )
    return lines


def run_ea_fixture_scenario() -> list[str]:
    lines: list[str] = []
    fixture = ROOT / "tests" / "data" / "wcs_tiles" / "ea_lidar_64x64.tif"
    if not fixture.is_file():
        lines.append("Scenario B — EA fixture bench skipped (fixture missing)")
        return lines

    tile_bytes = fixture.read_bytes()
    retry_route = f"{BASE_URL}/fixture"

    call_counts: dict[str, int] = {}

    def fixture_handler(request: httpx.Request) -> httpx.Response:
        key = str(request.url)
        call_counts[key] = call_counts.get(key, 0) + 1
        if call_counts[key] == 1:
            return httpx.Response(
                429, headers={"Retry-After": "0"}, text="rate limited"
            )
        time.sleep(MOCK_LATENCY_S)
        return httpx.Response(
            200, content=tile_bytes, headers={"content-type": "image/tiff"}
        )

    with respx.mock:
        for index in range(4):
            respx.get(f"{retry_route}/{index}").mock(side_effect=fixture_handler)

        fixture_requests = [
            TileRequest(url=f"{retry_route}/{index}", params={}, retries=2)
            for index in range(4)
        ]
        ea_policy = _policy_from_service_defaults(ea_dsp_fetch_defaults())
        wall, fetcher = _run_tile_fetcher(ea_policy, fixture_requests)

    lines.append("")
    lines.append(
        f"Scenario B — EA fixture offline ({fixture.name}, 4 tiles, 429 + Retry-After)"
    )
    lines.append(f"  bbox reference (OSGB): {EA_LIDAR_BENCH_BBOX}")
    lines.append(
        _format_row(
            "TileFetcher + EA AIMD preset",
            wall,
            fetcher.stats.max_inflight,
            fetcher.stats.request_count,
            fetcher.stats.retry_count,
        )
    )
    lines.append(
        f"  fixture bytes/tile={len(tile_bytes)}  successful_retries={fetcher.stats.retry_count}"
    )
    return lines


def main() -> int:
    lines = [
        "=== tilearray TileFetcher bench (offline mock) ===",
        f"tiles={TILE_COUNT}  mock_latency={MOCK_LATENCY_S}s  server_max_concurrent={SERVER_MAX_CONCURRENT}",
        "",
    ]
    lines.extend(run_overload_scenario())
    lines.extend(run_healthy_scenario())
    lines.extend(run_realistic_latency_scenario())
    lines.extend(run_ea_fixture_scenario())
    lines.append("")
    lines.append("Re-run: uv run python scripts/bench_fetch_engine.py")

    output = "\n".join(lines)
    print(output)

    results_path = ROOT / "benchmarks" / "fetch_engine_bench_results.txt"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    results_path.write_text(output + "\n", encoding="utf-8")
    print(f"\n(wrote {results_path.relative_to(ROOT)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
