"""Abort propagation from mosaic tile loads."""

from __future__ import annotations

import threading
import time
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
    progress = FetchProgress(total=3)
    policy = FetchPolicy(max_concurrent=3, rate_limit_per_second=None, retries=0)
    start_gate = threading.Barrier(3)
    ok_calls = {"count": 0}

    respx.get("https://example.com/fail").mock(
        return_value=httpx.Response(500, text="fail")
    )

    def ok_handler(request: httpx.Request) -> httpx.Response:
        start_gate.wait(timeout=1)
        ok_calls["count"] += 1
        time.sleep(0.05)
        return httpx.Response(200, content=b"x")

    respx.get("https://example.com/ok").mock(side_effect=ok_handler)

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

    with ThreadPoolExecutor(max_workers=3) as pool:
        fail_future = pool.submit(
            _load_tile_array,
            fail_request,
            None,
            _decoder,
            np.dtype("float32"),
            policy,
            progress,
        )
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

        with pytest.raises(NetworkError):
            fail_future.result()

        aborted = 0
        for future in ok_futures:
            try:
                future.result()
            except NetworkError:
                aborted += 1

    assert progress.is_aborted() is True
    assert aborted >= 1
    assert ok_calls["count"] <= 1
