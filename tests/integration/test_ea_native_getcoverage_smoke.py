"""Live EA native GetCoverage smoke (#177).

Marked ``net`` + ``slow`` so it runs in the existing nightly ``net-slow`` job
(``pytest -m "net or slow"``), not a one-off workflow. A timeout must report
that EA did not answer the request tilearray builds — not ordinary 429 soak
noise.
"""

from __future__ import annotations

import time

import httpx
import numpy as np
import pytest
import xarray as xr

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

_ATTEMPT_LIMIT_S = 10.0
_MAX_ATTEMPTS = 2  # one retry, per #177 acceptance


def _run_native_bench() -> tuple[xr.DataArray, float]:
    fetch = {
        **ea_dsp_fetch_defaults(),
        "fetch_timeout": _ATTEMPT_LIMIT_S,
        "fetch_retries": 1,
    }
    config = WCSConfig.from_url(
        EA_LIDAR_WCS_URL,
        EA_LIDAR_COVERAGE_ID,
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        chunk_size=(64, 64),
        resolution=(1.0, 1.0),
        **fetch,
    )
    started = time.perf_counter()
    result = create_array(
        config,
        EA_LIDAR_BENCH_BBOX,
        bbox_crs=CRS.EPSG_27700,
    )
    computed = result.compute()
    elapsed = time.perf_counter() - started
    return computed, elapsed


def test_ea_native_bench_bbox_getcoverage_answers_within_10s() -> None:
    """One native-resolution create_array on EA_LIDAR_BENCH_BBOX must return in <10 s."""

    last_error: BaseException | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            computed, elapsed = _run_native_bench()
        except (NetworkError, httpx.TimeoutException, httpx.TransportError) as exc:
            last_error = exc
            message = str(exc).lower()
            if "timeout" in message or "timed out" in message:
                if attempt < _MAX_ATTEMPTS:
                    continue
                raise AssertionError(
                    "EA did not answer the request tilearray builds "
                    f"(attempt {attempt}/{_MAX_ATTEMPTS}). This is not ordinary "
                    "429 soak noise; check that GetCoverage omits scaleSize at "
                    f"native resolution (#177). Underlying error: {exc}"
                ) from exc
            raise

        if elapsed < _ATTEMPT_LIMIT_S:
            assert computed.shape == (64, 64)
            assert np.isfinite(computed).any()
            return

        last_error = AssertionError(
            f"EA native GetCoverage took {elapsed:.1f}s "
            f"(limit {_ATTEMPT_LIMIT_S:.0f}s on attempt {attempt}/{_MAX_ATTEMPTS})"
        )
        if attempt < _MAX_ATTEMPTS:
            continue

    raise AssertionError(
        "EA did not answer the request tilearray builds promptly (#177). "
        f"Last detail: {last_error}"
    )
