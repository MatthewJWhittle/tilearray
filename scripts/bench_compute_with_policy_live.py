#!/usr/bin/env python3
"""
Live bench: plain ``.compute()`` vs ``compute_with_policy`` on county-scale EA DTM.

Compares wall time for a lazy Skipton/Yorkshire mosaic (~64 tiles) where Dask's
default thread pool (~cpu_count) under-saturates AIMD fetch headroom.

Requires network access to EA public WCS. Not run in CI.

Usage:
    uv run python scripts/bench_compute_with_policy_live.py
    uv run python scripts/bench_compute_with_policy_live.py --cache-dir /tmp/ea-dtm-bench
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tilearray import compute_with_policy, create_array  # noqa: E402
from tilearray.fetch_presets import (  # noqa: E402
    EA_LIDAR_COVERAGE_ID,
    EA_LIDAR_WCS_URL,
)
from tilearray.service import WCSConfig  # noqa: E402
from tilearray.types import CRS, Format  # noqa: E402

# ~1 km square near Skipton, Yorkshire (EPSG:27700) → 8×8 = 64 tiles at 128 px / 1 m.
SKIPTON_COUNTY_BBOX = (418_000.0, 451_400.0, 419_024.0, 452_424.0)
CHUNK_SIZE = (128, 128)
RESOLUTION = (1.0, 1.0)
EA_MAX_CONCURRENT = 32


def _build_config() -> WCSConfig:
    return WCSConfig.for_ea_dsp(
        EA_LIDAR_WCS_URL,
        coverage_id=EA_LIDAR_COVERAGE_ID,
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        chunk_size=CHUNK_SIZE,
        resolution=RESOLUTION,
    )


def _lazy_array(cache_dir: Path | None) -> object:
    config = _build_config()
    return create_array(
        config,
        SKIPTON_COUNTY_BBOX,
        CRS.EPSG_27700,
        cache_dir=cache_dir,
    )


def _reset_cache(cache_dir: Path | None) -> None:
    if cache_dir is not None and cache_dir.exists():
        shutil.rmtree(cache_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)


def _bench(label: str, cache_dir: Path | None, compute_fn: object) -> float:
    _reset_cache(cache_dir)
    da = _lazy_array(cache_dir)
    lazy = da.mean()
    workers_hint = da.attrs.get("tilearray_recommended_num_workers", "?")
    print(
        f"\n{label} (shape={tuple(da.sizes.values())}, "
        f"workers_hint={workers_hint}, delayed mean)..."
    )
    start = time.perf_counter()
    result = compute_fn(lazy, da)
    elapsed = time.perf_counter() - start
    print(f"  wall={elapsed:.2f}s  mean={float(result):.2f}")
    return elapsed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional per-run tile cache directory (cleared before each timed run)",
    )
    args = parser.parse_args()

    plain_s = _bench(
        "plain .compute()",
        args.cache_dir,
        lambda lazy, _da: lazy.compute(),
    )
    policy_s = _bench(
        "compute_with_policy()",
        args.cache_dir,
        lambda lazy, da: compute_with_policy(
            lazy,
            max_concurrent=da.attrs["tilearray_fetch_policy"]["max_concurrent"],
        ),
    )

    print("\n--- summary ---")
    print(f"plain .compute():        {plain_s:.2f}s")
    print(f"compute_with_policy():   {policy_s:.2f}s")
    if policy_s > 0:
        ratio = plain_s / policy_s
        print(f"speedup (plain/policy):  {ratio:.2f}x")
    print(
        "\nWhy plain .compute() is slower on cold fetch: Dask defaults to "
        "~cpu_count() thread workers, capping in-flight tile HTTP below the AIMD "
        f"ceiling ({EA_MAX_CONCURRENT} for EA preset). compute_with_policy sets "
        "num_workers=max(cpu_count, max_concurrent) on the threaded scheduler."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
