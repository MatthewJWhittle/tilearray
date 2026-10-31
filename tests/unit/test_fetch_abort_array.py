"""Abort propagation from mosaic tile loads."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx
import numpy as np
import pytest
import respx

from tilearray.array import _load_tile_array
from tilearray.errors import NetworkError
from tilearray.fetch import FetchPolicy, FetchProgress, TileFetcher
from tilearray.types import Format, TileRequest, TileResponse

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    return np.ones((request.height or 1, request.width or 1), dtype=np.float32)


@respx.mock
def test_load_tile_array_abort_stops_sibling_workers() -> None:
    """After one tile aborts progress, sibling loads sharing it must fail fast.

    Earlier versions raced a Barrier(3) with only two waiters and wall-clock
    sleeps; under xdist that raised BrokenBarrierError. Abort is checked before
    HTTP, so the reliable contract is: once aborted, further siblings raise.
    """
    progress = FetchProgress(total=3)
    policy = FetchPolicy(max_concurrent=3, rate_limit_per_second=None, retries=0)

    respx.get("https://example.com/fail").mock(
        return_value=httpx.Response(500, text="fail")
    )
    respx.get("https://example.com/ok").mock(
        return_value=httpx.Response(200, content=b"x")
    )

    fail_request = TileRequest(
        url="https://example.com/fail",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
        retries=0,
    )
    ok_request = TileRequest(
        url="https://example.com/ok",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
        retries=0,
    )

    with pytest.raises(NetworkError):
        _load_tile_array(
            fail_request,
            None,
            _decoder,
            np.dtype("float32"),
            policy,
            progress,
        )

    assert progress.is_aborted() is True

    with ThreadPoolExecutor(max_workers=2) as pool:
        ok_futures = [
            pool.submit(
                _load_tile_array,
                ok_request,
                None,
                _decoder,
                np.dtype("float32"),
                policy,
                progress,
            )
            for _ in range(2)
        ]
        for future in ok_futures:
            with pytest.raises(NetworkError):
                future.result(timeout=2)
