"""Deferred retry pass for exhausted pressure tiles (no silent holes)."""

from __future__ import annotations

import threading
from typing import Any

import httpx
import numpy as np
import pytest
import respx

from tilearray import NetworkError, compute_mosaic, create_array, fetch_summary
from tilearray.array import _TILE_ERRORS_ATTR, _load_tile_array
from tilearray.fetch import (
    FetchPolicy,
    FetchProgress,
    TileFetcher,
    deferred_park_cap,
    is_deferred_retry_failure,
)
from tilearray.mosaic_plan import MosaicPlan
from tilearray.service.base import BaseService, TileGeometry
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    return np.ones((request.height or 1, request.width or 1), dtype=np.float32)


def _grid_service(rows: int, cols: int) -> type[BaseService]:
    class GridService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.GEOTIFF

        def __init__(self) -> None:
            super().__init__("https://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            width, height = chunk_size
            grid_rows, grid_cols = options.get("grid_shape", (rows, cols))
            span_x = (bbox.max_x - bbox.min_x) / grid_cols
            span_y = (bbox.max_y - bbox.min_y) / grid_rows
            tiles: list[TileRequest] = []
            for row in range(grid_rows):
                for col in range(grid_cols):
                    min_x = bbox.min_x + col * span_x
                    max_x = min_x + span_x
                    min_y = bbox.min_y + (grid_rows - 1 - row) * span_y
                    max_y = min_y + span_y
                    tile_bbox = BoundingBox(
                        min_x=min_x,
                        min_y=min_y,
                        max_x=max_x,
                        max_y=max_y,
                        crs=bbox.crs,
                    )
                    idx = row * grid_cols + col
                    tiles.append(
                        TileRequest(
                            url=f"https://example.com/tile/{idx}",
                            params={},
                            output_format=Format.GEOTIFF,
                            crs=bbox.crs,
                            bbox=tile_bbox,
                            width=width,
                            height=height,
                            retries=0,
                        )
                    )
            return MosaicPlan(tiles=tuple(tiles))

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    return GridService


def _patch_flaky_grid(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: int,
    cols: int,
    flaky_idx: int,
    primary_failures: int,
    fail_status: int = 429,
) -> dict[str, Any]:
    """One flaky tile fails ``primary_failures`` times then succeeds; peers always 200."""

    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}
    lock = threading.Lock()
    peak_deferred_inflight = {"value": 0}
    deferred_inflight = {"value": 0}

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        progress = kwargs.get("progress")
        in_deferred = bool(
            progress is not None and getattr(progress, "_deferred_pass_open", False)
        )
        with lock:
            n = attempts.get(idx, 0) + 1
            attempts[idx] = n
            if in_deferred:
                deferred_inflight["value"] += 1
                peak_deferred_inflight["value"] = max(
                    peak_deferred_inflight["value"], deferred_inflight["value"]
                )
        try:
            if idx == flaky_idx and n <= primary_failures:
                return TileResponse(
                    data=b"slow",
                    content_type="text/plain",
                    status_code=fail_status,
                    headers={"Retry-After": "0"},
                    url=request.url,
                    success=False,
                    error_message=f"HTTP {fail_status}",
                )
            return TileResponse(
                data=b"ok",
                content_type="image/tiff",
                status_code=200,
                headers={},
                url=request.url,
                success=True,
            )
        finally:
            if in_deferred:
                with lock:
                    deferred_inflight["value"] -= 1

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    monkeypatch.setattr("tilearray.array._resolve_decoder", lambda *a, **k: _decoder)
    return {
        "attempts": attempts,
        "peak_deferred_inflight": peak_deferred_inflight,
        "lock": lock,
    }


def test_is_deferred_retry_failure_taxonomy() -> None:
    ok = TileResponse(
        data=b"",
        content_type="",
        status_code=200,
        headers={},
        url="https://example.com/t",
        success=True,
    )
    assert is_deferred_retry_failure(ok) is False
    for status in (0, 408, 429, 500, 502, 503, 504):
        bad = TileResponse(
            data=b"",
            content_type="",
            status_code=status,
            headers={},
            url="https://example.com/t",
            success=False,
        )
        assert is_deferred_retry_failure(bad) is True
    for status in (401, 403, 404):
        bad = TileResponse(
            data=b"",
            content_type="",
            status_code=status,
            headers={},
            url="https://example.com/t",
            success=False,
        )
        assert is_deferred_retry_failure(bad) is False


def test_deferred_park_cap_ceil_fraction() -> None:
    assert deferred_park_cap(16, fraction=0.1) == 2
    assert deferred_park_cap(10, fraction=0.1) == 1
    assert deferred_park_cap(1, fraction=0.1) == 1


def test_flaky_tile_mosaic_completes_with_one_deferred_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = cols = 4
    state = _patch_flaky_grid(
        monkeypatch,
        rows=rows,
        cols=cols,
        flaky_idx=7,
        primary_failures=1,
        fail_status=429,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=8, max_y=8, crs=CRS.EPSG_4326)
    result = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(2, 2),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        band_policy="first_band",
        compute=True,
        tile_decoder=_decoder,
        max_concurrent_requests=4,
        min_concurrent_requests=2,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        fetch_retries=0,
    )
    assert result.shape == (rows * 2, cols * 2)
    assert np.isfinite(result.values).all()
    tile_errors = result.attrs[_TILE_ERRORS_ATTR]
    assert tile_errors["deferred_parked"] == 1
    assert tile_errors["deferred_recovered"] == 1
    assert tile_errors["deferred_failed"] == 0
    assert tile_errors["aborted"] is False
    assert state["attempts"][7] == 2
    assert sum(1 for n in state["attempts"].values() if n >= 1) == rows * cols
    summary = fetch_summary(result)
    assert summary.deferred_parked == 1
    assert summary.deferred_recovered == 1
    assert "deferred retry" in summary.summary()


def test_permanent_404_still_aborts_without_deferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = cols = 2
    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        attempts[idx] = attempts.get(idx, 0) + 1
        if idx == 0:
            return TileResponse(
                data=b"missing",
                content_type="text/plain",
                status_code=404,
                headers={},
                url=request.url,
                success=False,
                error_message="HTTP 404",
            )
        return TileResponse(
            data=b"ok",
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    monkeypatch.setattr("tilearray.array._resolve_decoder", lambda *a, **k: _decoder)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=4, max_y=4, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError, match="404"):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(2, 2),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            compute=True,
            tile_decoder=_decoder,
            max_concurrent_requests=4,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
    assert attempts.get(0) == 1


@respx.mock
def test_permanent_401_still_aborts_on_first_answer() -> None:
    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0)
    respx.get("https://example.com/auth").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    request = TileRequest(
        url="https://example.com/auth",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
        retries=0,
    )
    with pytest.raises(NetworkError, match="401"):
        _load_tile_array(request, None, _decoder, np.dtype("float32"), policy, progress)
    assert progress.is_aborted() is True
    assert progress.deferred_parked == 0


@respx.mock
def test_deferred_pass_failure_raises_with_attempt_counts() -> None:
    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=1)
    route = respx.get("https://example.com/rate").mock(
        return_value=httpx.Response(429, text="slow", headers={"Retry-After": "0"})
    )
    request = TileRequest(
        url="https://example.com/rate",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
        retries=1,
    )
    with pytest.raises(NetworkError, match="deferred retry pass failed"):
        _load_tile_array(request, None, _decoder, np.dtype("float32"), policy, progress)
    assert progress.is_aborted() is True
    assert progress.deferred_parked == 1
    assert progress.deferred_failed == 1
    # primary attempts (retries+1) + deferred attempts (retries+1)
    assert route.call_count == 4


def test_park_cap_exceeded_aborts_without_looping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, cols = 2, 5  # 10 tiles; cap = ceil(0.1*10) = 1
    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        attempts[idx] = attempts.get(idx, 0) + 1
        # First two tiles always pressure-fail (second exceeds park cap).
        if idx in {0, 1}:
            return TileResponse(
                data=b"slow",
                content_type="text/plain",
                status_code=500,
                headers={"Retry-After": "0"},
                url=request.url,
                success=False,
                error_message="HTTP 500",
            )
        return TileResponse(
            data=b"ok",
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    monkeypatch.setattr("tilearray.array._resolve_decoder", lambda *a, **k: _decoder)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=4, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(2, 2),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            compute=True,
            tile_decoder=_decoder,
            max_concurrent_requests=4,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
    # Cap tile aborts on first exhausted answer (no deferred loop).
    assert attempts.get(0, 0) <= 2
    assert attempts.get(1, 0) <= 2


def test_deferred_pass_concurrency_at_most_aimd_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows = cols = 3
    floor = 2
    real_progress = FetchProgress

    def progress_factory(*args: Any, **kwargs: Any) -> FetchProgress:
        kwargs.setdefault("deferred_park_fraction", 0.5)
        kwargs.setdefault("deferred_max_concurrent", floor)
        return real_progress(*args, **kwargs)

    monkeypatch.setattr("tilearray.array.FetchProgress", progress_factory)

    service_cls = _grid_service(rows, cols)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )
    attempts: dict[int, int] = {}
    lock = threading.Lock()
    peak = {"value": 0}
    inflight = {"value": 0}

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        progress = kwargs.get("progress")
        in_deferred = bool(
            progress is not None and getattr(progress, "_deferred_pass_open", False)
        )
        with lock:
            n = attempts.get(idx, 0) + 1
            attempts[idx] = n
            if in_deferred:
                inflight["value"] += 1
                peak["value"] = max(peak["value"], inflight["value"])
        try:
            if idx in {1, 2, 3} and n <= 1:
                return TileResponse(
                    data=b"slow",
                    content_type="text/plain",
                    status_code=429,
                    headers={"Retry-After": "0"},
                    url=request.url,
                    success=False,
                    error_message="HTTP 429",
                )
            return TileResponse(
                data=b"ok",
                content_type="image/tiff",
                status_code=200,
                headers={},
                url=request.url,
                success=True,
            )
        finally:
            if in_deferred:
                with lock:
                    inflight["value"] -= 1

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    monkeypatch.setattr("tilearray.array._resolve_decoder", lambda *a, **k: _decoder)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=6, max_y=6, crs=CRS.EPSG_4326)
    lazy = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(2, 2),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        band_policy="first_band",
        compute=False,
        tile_decoder=_decoder,
        max_concurrent_requests=8,
        min_concurrent_requests=floor,
        adaptive_concurrency=True,
        rate_limit_per_second=None,
        fetch_retries=0,
    )
    result = compute_mosaic(lazy)
    assert np.isfinite(result.values).all()
    assert result.attrs[_TILE_ERRORS_ATTR]["deferred_parked"] == 3
    assert peak["value"] <= floor
