# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingImports=false

"""
Preset politeness soak / bench evidence (#56).

Nightly / maintainer-opt-in only: marked ``net`` + ``slow`` (+ ``soak``) so PR CI
keeps them deselected under default ``not slow and not net``.

- Live paths: multi-tile *cold* mosaics under EA DSP + OSM polite ceilings.
- Offline path: fail-loud + sibling abort under concurrent multi-tile pressure
  (do not intentionally break live hosts for this contract).

Larger county-/valley-scale soaks (hundreds–thousands of tiles) belong in
``scripts/soak_large_aoi_presets.py``, not this module / nightly job.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tilearray import NetworkError, compute_with_policy, create_array
from tilearray.fetch import TileFetcher
from tilearray.fetch_presets import (
    EA_LIDAR_COVERAGE_ID,
    EA_LIDAR_WCS_URL,
    OSM_TILE_TEMPLATE,
    ea_dsp_fetch_defaults,
    osm_fetch_defaults,
)
from tilearray.service.base import BaseService, TileGeometry
from tilearray.service.config import WCSConfig, XYZConfig
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

# ~256 m OSGB window → 4×4 = 16 tiles at 64 px / 1 m (guide-scale, polite ceiling).
_EA_SOAK_BBOX = (431900.0, 382700.0, 432156.0, 382956.0)
_EA_CHUNK = (64, 64)
_EA_RESOLUTION = (1.0, 1.0)
_EA_EXPECTED_TILES = 16
_EA_CEILING = 10

# Central London window at z16 → multiple slippy tiles under OSM polite caps.
_OSM_SOAK_BBOX = (-0.005, 51.495, 0.005, 51.505)
_OSM_ZOOM = 16
_OSM_CEILING = 2
_OSM_MIN_TILES = 4

pytestmark = [
    pytest.mark.integration,
    pytest.mark.soak,
]


@pytest.fixture(autouse=True)
def _reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _nchunks(da: Any) -> int:
    """Number of spatial Dask chunks (proxy for planned tile count)."""
    y_chunks, x_chunks = da.data.chunks[0], da.data.chunks[1]
    return len(y_chunks) * len(x_chunks)


def _assert_cold_cache(cache_dir: Path) -> None:
    assert cache_dir.exists()
    assert not any(cache_dir.iterdir()), "soak requires an empty cold cache"


@pytest.mark.net
@pytest.mark.slow
def test_ea_dsp_multi_tile_cold_mosaic_under_preset_ceiling(
    tmp_cache_dir: Path,
) -> None:
    """EA Lidar multi-tile cold mosaic completes under ea_dsp preset ceiling (10)."""
    _assert_cold_cache(tmp_cache_dir)

    config = WCSConfig.from_url(
        EA_LIDAR_WCS_URL,
        coverage_id=EA_LIDAR_COVERAGE_ID,
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        chunk_size=_EA_CHUNK,
        resolution=_EA_RESOLUTION,
        **ea_dsp_fetch_defaults(),
    )
    assert config.max_concurrent_requests == _EA_CEILING

    da = create_array(
        config,
        _EA_SOAK_BBOX,
        CRS.EPSG_27700,
        cache_dir=tmp_cache_dir,
    )
    policy = da.attrs["tilearray_fetch_policy"]
    assert policy["max_concurrent"] == _EA_CEILING
    assert policy["adaptive_concurrency"] is True
    assert da.attrs["tilearray_tile_errors"]["armed"] is False
    assert _nchunks(da) == _EA_EXPECTED_TILES

    elevation = compute_with_policy(da)
    assert elevation.shape == (256, 256)
    mean_value = float(elevation.mean())
    assert -1000.0 < mean_value < 1000.0
    assert int(np.isnan(elevation.values).sum()) == 0


@pytest.mark.net
@pytest.mark.slow
def test_osm_multi_tile_cold_mosaic_under_preset_ceiling(
    tmp_cache_dir: Path,
) -> None:
    """OSM XYZ multi-tile cold mosaic completes under osm_fetch_defaults caps."""
    _assert_cold_cache(tmp_cache_dir)

    config = XYZConfig.from_url(
        OSM_TILE_TEMPLATE,
        zoom=_OSM_ZOOM,
        output_format=Format.PNG,
        chunk_size=(256, 256),
        **osm_fetch_defaults(),
    )
    assert config.max_concurrent_requests == _OSM_CEILING
    assert config.rate_limit_per_second == 2.0

    da = create_array(
        config,
        _OSM_SOAK_BBOX,
        CRS.EPSG_4326,
        cache_dir=tmp_cache_dir,
    )
    policy = da.attrs["tilearray_fetch_policy"]
    assert policy["max_concurrent"] == _OSM_CEILING
    assert policy["adaptive_concurrency"] is False
    assert da.attrs["tilearray_tile_errors"]["armed"] is False
    assert _nchunks(da) >= _OSM_MIN_TILES

    mosaic = compute_with_policy(da)
    assert mosaic.size > 0
    # Palette PNG may be single-band; either shape is fine for soak evidence.
    assert mosaic.ndim in (2, 3)


@pytest.mark.slow
def test_multi_tile_fail_loud_aborts_under_pressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default hard-fail aborts the mosaic under concurrent multi-tile pressure.

    Live hosts are not intentionally broken; this offline soak contract keeps
    fail-loud + sibling abort observable when one tile exhausts retries.
    """
    rows, cols = 4, 4
    fail_index = 5

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
        ) -> list[TileRequest]:
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
                    idx = row * grid_cols + col
                    tiles.append(
                        TileRequest(
                            url=f"https://example.com/tile/{idx}",
                            params={},
                            output_format=Format.GEOTIFF,
                            crs=bbox.crs,
                            bbox=BoundingBox(
                                min_x=min_x,
                                min_y=min_y,
                                max_x=max_x,
                                max_y=max_y,
                                crs=bbox.crs,
                            ),
                            width=width,
                            height=height,
                            retries=0,
                        )
                    )
            return tiles

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    def fake_get_service(*_args: Any, **_kwargs: Any) -> GridService:
        return GridService()

    def fake_fetch_tile(request: TileRequest, **_kwargs: Any) -> TileResponse:
        idx = int(str(request.url).rsplit("/", maxsplit=1)[-1])
        if idx == fail_index:
            return TileResponse(
                data=b"",
                content_type="text/plain",
                status_code=500,
                headers={},
                url=request.url,
                success=False,
                error_message="injected soak failure",
            )
        width = request.width or 1
        height = request.height or 1
        return TileResponse(
            data=b"\x00" * (width * height),
            content_type="application/octet-stream",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    def decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
        return np.ones(
            (request.height or 1, request.width or 1),
            dtype=np.float32,
        )

    monkeypatch.setattr("tilearray.array.get_service", fake_get_service)
    monkeypatch.setattr("tilearray.array.fetch_tile", fake_fetch_tile)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=16, max_y=16, crs=CRS.EPSG_4326)
    with pytest.raises(NetworkError):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(4, 4),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            compute=True,
            tile_decoder=decoder,
            max_concurrent_requests=8,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
