#!/usr/bin/env python3
"""
Maintainer large-AOI politeness soak under EA + OSM presets (#56 → 0.3).

Nightly CI runs the modest multi-tile soak in
``tests/integration/test_preset_soak.py`` (tens of tiles). This script is the
opt-in path for county-/valley-scale evidence (hundreds–thousands of tiles)
that feeds the 0.3 large-AOI exit criterion — not folklore, not always-on PR CI.

Respect host ceilings: EA DSP AIMD ceiling 10; OSM max 2 in-flight / ~2 req/s.
Do not raise concurrency past the presets. Prefer ``compute_with_policy``.

Usage:
    uv run python scripts/soak_large_aoi_presets.py
    uv run python scripts/soak_large_aoi_presets.py --host ea --profile medium
    uv run python scripts/soak_large_aoi_presets.py --host osm --profile small
    uv run python scripts/soak_large_aoi_presets.py --host both --profile large \\
        --cache-dir /tmp/tilearray-soak
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
from tilearray.service import WCSConfig, XYZConfig  # noqa: E402
from tilearray.types import CRS, Format  # noqa: E402

# Profiles: modest → county-scale. ``large`` EA is the Aire-valley-shaped window
# historically used for ceiling evidence (~1k tiles at 128 px / 1 m).
_EA_PROFILES: dict[str, tuple[tuple[float, float, float, float], tuple[int, int]]] = {
    # 16 tiles (same footprint as nightly soak)
    "small": ((431900.0, 382700.0, 432156.0, 382956.0), (64, 64)),
    # ~64 tiles near Skipton (see scripts/bench_compute_with_policy_live.py)
    "medium": ((418_000.0, 451_400.0, 419_024.0, 452_424.0), (128, 128)),
    # ~1015-tile Aire valley cold mosaic (0.3-scale evidence target)
    "large": ((410_000.0, 430_000.0, 414_048.0, 434_048.0), (128, 128)),
}

_OSM_PROFILES: dict[str, tuple[tuple[float, float, float, float], int]] = {
    # Guide / nightly-scale London window
    "small": ((-0.005, 51.495, 0.005, 51.505), 16),
    # Wider central London — still polite; OSMF policy applies
    "medium": ((-0.02, 51.48, 0.02, 51.52), 15),
    # Larger AOI at lower zoom — hundreds of tiles; maintainer-only
    "large": ((-0.05, 51.45, 0.05, 51.55), 14),
}


def _nchunks(da: object) -> int:
    data = getattr(da, "data", None)
    chunks = getattr(data, "chunks", None)
    if chunks is None:
        return 0
    return len(chunks[0]) * len(chunks[1])


def _reset_cache(cache_dir: Path | None) -> None:
    if cache_dir is None:
        return
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)


def _soak_ea(profile: str, cache_dir: Path | None) -> None:
    bbox, chunk = _EA_PROFILES[profile]
    config = WCSConfig.for_ea_dsp(
        EA_LIDAR_WCS_URL,
        coverage_id=EA_LIDAR_COVERAGE_ID,
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        chunk_size=chunk,
        resolution=(1.0, 1.0),
    )
    print(
        f"\n[ea/{profile}] ceiling={config.max_concurrent_requests} "
        f"chunk={chunk} bbox={bbox}"
    )
    _reset_cache(cache_dir)
    da = create_array(config, bbox, CRS.EPSG_27700, cache_dir=cache_dir)
    tiles = _nchunks(da)
    policy = da.attrs["tilearray_fetch_policy"]
    print(
        f"  planned_tiles≈{tiles}  max_concurrent={policy['max_concurrent']} "
        f"adaptive={policy['adaptive_concurrency']}"
    )
    start = time.perf_counter()
    result = compute_with_policy(da.mean())
    elapsed = time.perf_counter() - start
    print(f"  wall={elapsed:.1f}s  mean={float(result):.2f}  ok")


def _soak_osm(profile: str, cache_dir: Path | None) -> None:
    bbox, zoom = _OSM_PROFILES[profile]
    config = XYZConfig.for_openstreetmap(
        zoom=zoom,
        output_format=Format.PNG,
        chunk_size=(256, 256),
    )
    print(
        f"\n[osm/{profile}] ceiling={config.max_concurrent_requests} "
        f"rate={config.rate_limit_per_second}/s zoom={zoom} bbox={bbox}"
    )
    print("  note: stay within OSMF tile usage policy; do not raise concurrency")
    _reset_cache(cache_dir)
    da = create_array(config, bbox, CRS.EPSG_4326, cache_dir=cache_dir)
    tiles = _nchunks(da)
    policy = da.attrs["tilearray_fetch_policy"]
    print(
        f"  planned_tiles≈{tiles}  max_concurrent={policy['max_concurrent']} "
        f"adaptive={policy['adaptive_concurrency']}"
    )
    start = time.perf_counter()
    result = compute_with_policy(da)
    elapsed = time.perf_counter() - start
    print(f"  wall={elapsed:.1f}s  shape={tuple(result.shape)}  ok")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        choices=("ea", "osm", "both"),
        default="both",
        help="Which preset host(s) to soak (default: both)",
    )
    parser.add_argument(
        "--profile",
        choices=("small", "medium", "large"),
        default="medium",
        help="AOI scale: small≈nightly, medium≈tens–hundreds, large≈0.3 evidence",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        help="Optional cold-cache directory (cleared before each host run)",
    )
    args = parser.parse_args()

    print(
        "Large-AOI preset soak (maintainer). "
        "Nightly evidence: pytest -m soak (deselected on PRs)."
    )
    if args.host in ("ea", "both"):
        _soak_ea(args.profile, args.cache_dir)
    if args.host in ("osm", "both"):
        _soak_osm(args.profile, args.cache_dir)
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
