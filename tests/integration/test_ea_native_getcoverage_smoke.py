"""Nightly single-request EA smoke: the GetCoverage we build must be answered (#177).

Runs in its own nightly job, separate from multi-tile soak / #150 429 noise.
A timeout here means EA did not answer the request tilearray builds.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tilearray import create_array
from tilearray.errors import NetworkError
from tilearray.fetch_presets import (
    EA_LIDAR_BENCH_BBOX,
    EA_LIDAR_COVERAGE_ID,
    EA_LIDAR_WCS_URL,
    ea_dsp_fetch_defaults,
)
from tilearray.service.config import WCSConfig
from tilearray.types import CRS, Format

pytestmark = [pytest.mark.integration, pytest.mark.slow, pytest.mark.net]


def test_ea_native_bench_bbox_getcoverage_answers_within_10s() -> None:
    """One native-resolution create_array on EA_LIDAR_BENCH_BBOX must return in <10 s."""

    config = WCSConfig.from_url(
        EA_LIDAR_WCS_URL,
        EA_LIDAR_COVERAGE_ID,
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        chunk_size=(64, 64),
        resolution=(1.0, 1.0),
        **ea_dsp_fetch_defaults(),
        fetch_timeout=10.0,
        fetch_retries=1,
    )

    started = time.perf_counter()
    try:
        result = create_array(
            config,
            EA_LIDAR_BENCH_BBOX,
            bbox_crs=CRS.EPSG_27700,
        )
        computed = result.compute()
    except (NetworkError, httpx.TimeoutException, httpx.TransportError) as exc:
        elapsed = time.perf_counter() - started
        message = str(exc).lower()
        if "timeout" in message or "timed out" in message or elapsed >= 9.0:
            raise AssertionError(
                "EA did not answer the request tilearray builds "
                f"(elapsed={elapsed:.1f}s). This is not ordinary 429 soak noise; "
                "check that GetCoverage omits scaleSize at native resolution (#177). "
                f"Underlying error: {exc}"
            ) from exc
        raise

    elapsed = time.perf_counter() - started
    assert elapsed < 10.0, (
        f"EA native GetCoverage took {elapsed:.1f}s (limit 10s); "
        "EA did not answer the request tilearray builds promptly (#177)"
    )
    assert computed.shape == (64, 64)
