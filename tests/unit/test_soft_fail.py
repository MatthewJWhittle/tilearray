"""Opt-in soft-fail / on_tile_error mosaic behaviour (#65)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import httpx
import numpy as np
import pytest
import respx

from tilearray import (
    NetworkError,
    ValidationError,
    compute_mosaic,
    soft_fail_exhausted_tiles,
)
from tilearray.array import (
    _PROGRESS_REF_ATTR,
    _TILE_ERRORS_ATTR,
    _load_tile_array,
    create_array,
)
from tilearray.fetch import (
    FetchPolicy,
    FetchProgress,
    TileFetcher,
    lookup_fetch_progress,
)
from tilearray.mosaic_plan import MosaicPlan
from tilearray.service.base import BaseService, TileGeometry
from tilearray.tile_errors import TileErrorAction, TileErrorContext
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


def _failing_decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    raise ValueError("corrupt tile body")


def _grid_service(rows: int, cols: int, *, tile_retries: int = 0) -> type[BaseService]:
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
                            retries=tile_retries,
                        )
                    )
            return MosaicPlan(tiles=tuple(tiles))

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    return GridService


def _patch_grid(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: int,
    cols: int,
    fail_every: int | None = 10,
    fail_status: int = 404,
    fail_body: bytes = b"missing",
    tile_retries: int = 0,
    fetch_side_effect: Any | None = None,
) -> list[dict[str, Any]]:
    service_cls = _grid_service(rows, cols, tile_retries=tile_retries)
    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: service_cls()
    )

    calls: list[dict[str, Any]] = []

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        calls.append({"url": request.url, "kwargs": kwargs})
        if fetch_side_effect is not None:
            return fetch_side_effect(request, calls)
        # Index from URL .../tile/{idx}
        idx = int(request.url.rsplit("/", 1)[-1])
        if fail_every is not None and idx % fail_every == 0:
            return TileResponse(
                data=fail_body,
                content_type="text/plain",
                status_code=fail_status,
                headers={},
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
            error_message=None,
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch)
    return calls


@respx.mock
def test_default_hard_fail_still_aborts() -> None:
    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=2, rate_limit_per_second=None, retries=0)
    route = respx.get("https://example.com/fail").mock(
        return_value=httpx.Response(500, text="fail", headers={"Retry-After": "0"})
    )
    request = TileRequest(
        url="https://example.com/fail",
        params={},
        output_format=Format.GEOTIFF,
        width=2,
        height=2,
        retries=0,
    )
    with pytest.raises(NetworkError, match="deferred retry pass failed"):
        _load_tile_array(request, None, _decoder, np.dtype("float32"), policy, progress)
    assert progress.is_aborted() is True
    assert progress.soft_failed == 0
    assert progress.deferred_parked == 1
    assert progress.deferred_failed == 1
    # Primary attempt + one deferred pass attempt (retries=0 → 1 each).
    assert route.call_count == 2


@respx.mock
def test_soft_fail_returns_nan_block_without_abort() -> None:
    progress = FetchProgress(total=2)
    policy = FetchPolicy(max_concurrent=2, rate_limit_per_second=None, retries=0)
    respx.get("https://example.com/fail").mock(
        return_value=httpx.Response(404, text="missing")
    )
    request = TileRequest(
        url="https://example.com/fail",
        params={},
        output_format=Format.GEOTIFF,
        width=3,
        height=2,
        retries=0,
    )
    array = _load_tile_array(
        request,
        None,
        _decoder,
        np.dtype("float32"),
        policy,
        progress,
        soft_fail_exhausted_tiles,
        float("nan"),
        1,
    )
    assert array.shape == (2, 3)
    assert np.isnan(array).all()
    assert progress.is_aborted() is False
    assert progress.soft_failed == 1
    assert len(progress.errors) == 1


def test_mosaic_soft_fail_every_tenth_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, cols = 4, 5  # 20 tiles; indices 0,10 soft-fail → 2/20 = 0.1
    _patch_grid(monkeypatch, rows=rows, cols=cols, fail_every=10, fail_status=404)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=8, crs=CRS.EPSG_4326)
    progress_events: list[tuple[int, int, bool]] = []

    def on_progress(
        done: int, total: int, request: TileRequest, response: TileResponse
    ) -> None:
        progress_events.append((done, total, response.success))

    result = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(4, 4),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        band_policy="first_band",
        compute=True,
        tile_decoder=_decoder,
        on_progress=on_progress,
        soft_fail=True,
        soft_fail_max_fraction=0.1,
        max_concurrent_requests=4,
        rate_limit_per_second=None,
        fetch_retries=0,
    )

    assert result.shape == (rows * 4, cols * 4)
    tile_errors = result.attrs["tilearray_tile_errors"]
    assert tile_errors["armed"] is True
    assert tile_errors["soft_failed"] == 2
    assert tile_errors["aborted"] is False
    assert len(tile_errors["examples"]) >= 1

    # Two soft-failed 4x4 blocks → 32 NaN pixels; rest finite ones.
    nan_count = int(np.isnan(result.values).sum())
    assert nan_count == 2 * 4 * 4
    assert int(np.isfinite(result.values).sum()) == result.size - nan_count
    assert any(not ok for _, _, ok in progress_events)
    assert _PROGRESS_REF_ATTR not in result.attrs


def test_mosaic_soft_fail_attrs_refresh_via_compute_mosaic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lazy create_array → compute_mosaic must refresh tilearray_tile_errors (#73)."""

    rows, cols = 4, 5  # 20 tiles; indices 0,10 soft-fail → 2/20 = 0.1
    _patch_grid(monkeypatch, rows=rows, cols=cols, fail_every=10, fail_status=404)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=8, crs=CRS.EPSG_4326)

    lazy = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(4, 4),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        band_policy="first_band",
        compute=False,
        tile_decoder=_decoder,
        soft_fail=True,
        soft_fail_max_fraction=0.1,
        max_concurrent_requests=4,
        rate_limit_per_second=None,
        fetch_retries=0,
    )

    # Plan-time attrs are armed but counters still zero until compute.
    lazy_errors = lazy.attrs[_TILE_ERRORS_ATTR]
    assert lazy_errors["armed"] is True
    assert lazy_errors["soft_failed"] == 0
    # Live session is registry-backed; attrs hold only a JSON-safe token.
    assert isinstance(lazy.attrs.get(_PROGRESS_REF_ATTR), str)
    assert isinstance(
        lookup_fetch_progress(lazy.attrs.get(_PROGRESS_REF_ATTR)), FetchProgress
    )

    result = compute_mosaic(lazy)

    tile_errors = result.attrs[_TILE_ERRORS_ATTR]
    assert tile_errors["armed"] is True
    assert tile_errors["soft_failed"] == 2
    assert tile_errors["aborted"] is False
    assert len(tile_errors["examples"]) >= 1
    assert _PROGRESS_REF_ATTR not in result.attrs

    # Source lazy attrs also refreshed for post-compute inspection.
    assert lazy.attrs[_TILE_ERRORS_ATTR]["soft_failed"] == 2

    nan_count = int(np.isnan(result.values).sum())
    assert nan_count == 2 * 4 * 4
    assert int(np.isfinite(result.values).sum()) == result.size - nan_count


def test_mosaic_without_soft_fail_aborts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rows, cols = 2, 5
    _patch_grid(monkeypatch, rows=rows, cols=cols, fail_every=10, fail_status=404)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=10, max_y=4, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(4, 4),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            compute=True,
            tile_decoder=_decoder,
            max_concurrent_requests=4,
            rate_limit_per_second=None,
            fetch_retries=0,
        )


@respx.mock
def test_retries_still_run_before_soft_fill() -> None:
    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=2)
    route = respx.get("https://example.com/flaky").mock(
        return_value=httpx.Response(500, text="err", headers={"Retry-After": "0"})
    )
    request = TileRequest(
        url="https://example.com/flaky",
        params={},
        output_format=Format.GEOTIFF,
        width=2,
        height=2,
        retries=2,
    )
    array = _load_tile_array(
        request,
        None,
        _decoder,
        np.dtype("float32"),
        policy,
        progress,
        soft_fail_exhausted_tiles,
        float("nan"),
        1,
    )
    assert np.isnan(array).all()
    assert progress.is_aborted() is False
    # Primary retries+1 plus one deferred pass of retries+1 before soft-fill.
    assert route.call_count == 6
    assert progress.deferred_parked == 1
    assert progress.deferred_failed == 1


@respx.mock
def test_retryable_then_success_no_soft_fill() -> None:
    progress = FetchProgress(total=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=2)
    route = respx.get("https://example.com/recover").mock(
        side_effect=[
            httpx.Response(429, text="slow", headers={"Retry-After": "0"}),
            httpx.Response(200, content=b"x"),
        ]
    )
    request = TileRequest(
        url="https://example.com/recover",
        params={},
        output_format=Format.GEOTIFF,
        width=2,
        height=2,
        retries=2,
    )
    array = _load_tile_array(
        request,
        None,
        _decoder,
        np.dtype("float32"),
        policy,
        progress,
        soft_fail_exhausted_tiles,
        float("nan"),
        1,
    )
    assert np.isfinite(array).all()
    assert progress.soft_failed == 0
    assert route.call_count == 2


@respx.mock
def test_auth_401_still_aborts_with_soft_preset() -> None:
    progress = FetchProgress(total=1)
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
    with pytest.raises(NetworkError):
        _load_tile_array(
            request,
            None,
            _decoder,
            np.dtype("float32"),
            policy,
            progress,
            soft_fail_exhausted_tiles,
            float("nan"),
            1,
        )
    assert progress.is_aborted() is True
    assert progress.soft_failed == 0


@respx.mock
def test_exhausted_429_still_aborts_with_soft_preset() -> None:
    progress = FetchProgress(total=1, deferred_max_concurrent=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0)
    route = respx.get("https://example.com/rate").mock(
        return_value=httpx.Response(429, text="slow", headers={"Retry-After": "0"})
    )
    request = TileRequest(
        url="https://example.com/rate",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
        retries=0,
    )
    with pytest.raises(NetworkError):
        _load_tile_array(
            request,
            None,
            _decoder,
            np.dtype("float32"),
            policy,
            progress,
            soft_fail_exhausted_tiles,
            float("nan"),
            1,
        )
    assert progress.is_aborted() is True
    assert progress.deferred_parked == 1
    assert progress.deferred_failed == 1
    assert route.call_count == 2


def test_soft_fail_threshold_aborts_mosaic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 10 tiles, every tile fails → first soft-fills (1/10=0.1), second trips threshold.
    rows, cols = 2, 5
    _patch_grid(monkeypatch, rows=rows, cols=cols, fail_every=1, fail_status=404)
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
            soft_fail=True,
            soft_fail_max_fraction=0.1,
            max_concurrent_requests=2,
            rate_limit_per_second=None,
            fetch_retries=0,
        )


@respx.mock
def test_decode_error_does_not_silent_nan_with_preset() -> None:
    progress = FetchProgress(total=1)
    policy = FetchPolicy(max_concurrent=1, rate_limit_per_second=None, retries=0)
    respx.get("https://example.com/bad").mock(
        return_value=httpx.Response(200, content=b"not-a-tiff")
    )
    request = TileRequest(
        url="https://example.com/bad",
        params={},
        output_format=Format.GEOTIFF,
        width=2,
        height=2,
        retries=0,
    )
    with pytest.raises(ValueError, match="corrupt tile body"):
        _load_tile_array(
            request,
            None,
            _failing_decoder,
            np.dtype("float32"),
            policy,
            progress,
            soft_fail_exhausted_tiles,
            float("nan"),
            1,
        )
    assert progress.is_aborted() is True
    assert progress.soft_failed == 0


def test_probe_phase_hard_fails_despite_soft_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failing first probe tile must still raise before the graph is built.

    soft_fail is not bound yet during the pre-graph band probe, so preserve-policy
    formats that must discover band count stay hard-fail even when soft_fail=True.
    """

    rows, cols = 1, 2

    class ProbeFailService(BaseService):
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
            tiles: list[TileRequest] = []
            for idx in range(rows * cols):
                tiles.append(
                    TileRequest(
                        url=f"https://example.com/tile/{idx}",
                        params={},
                        output_format=Format.GEOTIFF,
                        crs=bbox.crs,
                        bbox=bbox,
                        width=width,
                        height=height,
                        retries=0,
                    )
                )
            return MosaicPlan(tiles=tuple(tiles))

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: ProbeFailService()
    )
    monkeypatch.setattr(
        "tilearray._planning.default_band_count_for_format",
        lambda fmt, band_policy=None: None,
    )

    def fail_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=b"",
            content_type="text/plain",
            status_code=404,
            headers={},
            url=request.url,
            success=False,
            error_message="HTTP 404",
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", fail_fetch)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=1, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(4, 4),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            compute=False,
            tile_decoder=_decoder,
            soft_fail=True,
            max_concurrent_requests=2,
            rate_limit_per_second=None,
            fetch_retries=0,
        )


def test_soft_fail_exhausted_tiles_unit_decisions() -> None:
    progress = MagicMock(spec=FetchProgress)
    request = TileRequest(
        url="https://example.com/x",
        params={},
        output_format=Format.GEOTIFF,
        width=1,
        height=1,
    )

    def ctx(
        *,
        status: int | None = 404,
        exc: BaseException | None = None,
        soft_failed_so_far: int = 0,
        total_tiles: int = 10,
    ) -> TileErrorContext:
        response = None
        if status is not None and exc is None:
            response = TileResponse(
                data=b"",
                content_type="",
                status_code=status,
                headers={},
                url=request.url,
                success=False,
                error_message=f"HTTP {status}",
            )
        return TileErrorContext(
            request=request,
            response=response,
            exc=exc,
            progress=progress,
            attempt_policy_retries=3,
            soft_failed_so_far=soft_failed_so_far,
            total_tiles=total_tiles,
        )

    assert soft_fail_exhausted_tiles(ctx(status=404)) is TileErrorAction.SOFT_FAIL
    assert soft_fail_exhausted_tiles(ctx(status=500)) is TileErrorAction.SOFT_FAIL
    assert soft_fail_exhausted_tiles(ctx(status=0)) is TileErrorAction.SOFT_FAIL
    assert soft_fail_exhausted_tiles(ctx(status=401)) is TileErrorAction.ABORT_MOSAIC
    assert soft_fail_exhausted_tiles(ctx(status=429)) is TileErrorAction.ABORT_MOSAIC
    assert soft_fail_exhausted_tiles(ctx(status=403)) is TileErrorAction.ABORT_MOSAIC
    assert (
        soft_fail_exhausted_tiles(ctx(exc=ValueError("boom"), status=None))
        is TileErrorAction.ABORT_MOSAIC
    )
    assert (
        soft_fail_exhausted_tiles(ctx(status=404, soft_failed_so_far=0, total_tiles=10))
        is TileErrorAction.SOFT_FAIL
    )
    assert (
        soft_fail_exhausted_tiles(ctx(status=404, soft_failed_so_far=1, total_tiles=10))
        is TileErrorAction.ABORT_MOSAIC
    )
    assert (
        soft_fail_exhausted_tiles(ctx(status=404, soft_failed_so_far=0, total_tiles=1))
        is TileErrorAction.SOFT_FAIL
    )
    assert (
        soft_fail_exhausted_tiles(
            ctx(status=404, soft_failed_so_far=0, total_tiles=10),
            max_fraction=None,
        )
        is TileErrorAction.SOFT_FAIL
    )


def test_on_tile_error_and_soft_fail_mutually_exclusive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_grid(monkeypatch, rows=1, cols=1, fail_every=None)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)
    with pytest.raises(ValidationError, match="either on_tile_error"):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(2, 2),
            grid_shape=(1, 1),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            tile_decoder=_decoder,
            soft_fail=True,
            on_tile_error=soft_fail_exhausted_tiles,
            max_concurrent_requests=1,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
