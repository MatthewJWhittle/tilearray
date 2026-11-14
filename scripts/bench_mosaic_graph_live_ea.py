#!/usr/bin/env python3
"""
Live EA LiDAR WCS proof for mosaic graph changes (#185 / #178).

Same AOI/resolution as Wave 1 (#181): 2048×2048 m @ 1 m near Skipton OSGB.
Records preview request count, wall time, HTTP requests, retries, and pressure
events (429 path). Not a merge/nightly gate — architecture proof only.

Usage:
    uv run python scripts/bench_mosaic_graph_live_ea.py --label after
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tilearray import (  # noqa: E402
    compute_mosaic,
    create_array,
    fetch_summary,
    preview_array,
)
from tilearray.fetch_presets import (  # noqa: E402
    EA_LIDAR_COVERAGE_ID,
    EA_LIDAR_WCS_URL,
    ea_dsp_fetch_defaults,
)
from tilearray.service import WCSConfig  # noqa: E402
from tilearray.service.capabilities_memo import clear_capabilities_memo  # noqa: E402
from tilearray.types import CRS, Format  # noqa: E402

# Wave 1 live proof AOI (#181).
AOI_2048 = (431900.0, 382700.0, 433948.0, 384748.0)
RESOLUTION = (1.0, 1.0)


def _config(*, max_concurrent: int = 2) -> WCSConfig:
    defaults = ea_dsp_fetch_defaults()
    defaults["max_concurrent_requests"] = max_concurrent
    defaults["initial_concurrent_requests"] = max_concurrent
    defaults["min_concurrent_requests"] = max(1, max_concurrent)
    return WCSConfig.from_url(
        EA_LIDAR_WCS_URL,
        coverage_id=EA_LIDAR_COVERAGE_ID,
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        resolution=RESOLUTION,
        **defaults,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="run", help="Label for this measurement")
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=2,
        help="Host ceiling for the run (Wave 1 used 2)",
    )
    args = parser.parse_args()

    clear_capabilities_memo()
    cfg = _config(max_concurrent=args.max_concurrent)
    preview = preview_array(cfg, AOI_2048, CRS.EPSG_27700)
    print(
        f"[{args.label}] preview.request_count={preview.request_count} "
        f"grid={preview.grid_shape}"
    )

    t0 = time.perf_counter()
    da = create_array(cfg, AOI_2048, CRS.EPSG_27700, show_progress=False)
    build_s = time.perf_counter() - t0
    layers = len(getattr(da.data.dask, "layers", {}) or {})
    print(
        f"[{args.label}] create_array build={build_s:.2f}s "
        f"shape={tuple(da.shape)} hlg_layers={layers}"
    )

    t1 = time.perf_counter()
    result = compute_mosaic(da)
    wall_s = time.perf_counter() - t1
    mean = float(result.mean())
    summary = fetch_summary(result)
    print(
        f"[{args.label}] wall={wall_s:.2f}s mean={mean:.2f} "
        f"http_requests={summary.request_count} "
        f"retries={summary.retry_count} "
        f"pressure_events={summary.pressure_event_count} "
        f"breaker_trips={summary.circuit_breaker_trips}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
