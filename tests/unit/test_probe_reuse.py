"""Offline tests: band-probe reuse when fetch identity + shape/dtype match (#183)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from pytest import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
import tilearray.decode as decode_module
from tilearray._planning import (
    BandProbeResult,
    probe_array_fits_tile,
    tile_fetch_identity,
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

GEOTIFF_FIXTURE = (
    Path(__file__).resolve().parents[1] / "data" / "wcs_tiles" / "ea_lidar_64x64.tif"
)


@pytest.fixture(autouse=True)
def preserve_decoder_registry():
    original = dict(decode_module._DECODER_REGISTRY)
    try:
        yield
    finally:
        decode_module._DECODER_REGISTRY = original


def _geotiff_bytes() -> bytes:
    return GEOTIFF_FIXTURE.read_bytes()


class _GridGeoTIFFService(BaseService):
    """N in-extent GeoTIFF tiles with distinct fetch identities."""

    service_type = ServiceTypeEnum.XYZ
    output_format = Format.GEOTIFF

    def __init__(self, *, rows: int = 2, cols: int = 2) -> None:
        super().__init__("https://example.com")
        self.rows = rows
        self.cols = cols

    def generate_tile_requests(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: Any,
    ) -> MosaicPlan:
        width, height = chunk_size
        span_x = (bbox.max_x - bbox.min_x) / self.cols
        span_y = (bbox.max_y - bbox.min_y) / self.rows
        tiles: list[TileRequest] = []
        for row in range(self.rows):
            for col in range(self.cols):
                min_x = bbox.min_x + col * span_x
                max_x = min_x + span_x
                min_y = bbox.min_y + (self.rows - 1 - row) * span_y
                max_y = min_y + span_y
                tile_bbox = BoundingBox(
                    min_x=min_x,
                    min_y=min_y,
                    max_x=max_x,
                    max_y=max_y,
                    crs=bbox.crs,
                )
                tiles.append(
                    TileRequest(
                        url=f"https://example.com/tile/{row}/{col}.tif",
                        params={"r": str(row), "c": str(col)},
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


def _install_grid_service(
    monkeypatch: MonkeyPatch, *, rows: int = 2, cols: int = 2
) -> _GridGeoTIFFService:
    service = _GridGeoTIFFService(rows=rows, cols=cols)
    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: service)
    return service


def _counting_fetch(monkeypatch: MonkeyPatch, payload: bytes) -> list[str]:
    urls: list[str] = []

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        urls.append(request.url)
        return TileResponse(
            data=payload,
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch)
    return urls


@pytest.mark.unit
def test_tile_fetch_identity_uses_url_params_bbox_size() -> None:
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)
    other_bbox = BoundingBox(min_x=1, min_y=0, max_x=2, max_y=1, crs=CRS.EPSG_4326)
    a = TileRequest(
        url="https://example.com/a",
        params={"z": "1", "x": "2"},
        bbox=bbox,
        width=8,
        height=8,
        output_format=Format.GEOTIFF,
    )
    b = TileRequest(
        url="https://example.com/a",
        params={"x": "2", "z": "1"},
        bbox=bbox,
        width=8,
        height=8,
        output_format=Format.GEOTIFF,
    )
    c = TileRequest(
        url="https://example.com/a",
        params={"x": "2", "z": "1"},
        bbox=other_bbox,
        width=8,
        height=8,
        output_format=Format.GEOTIFF,
    )
    assert tile_fetch_identity(a) == tile_fetch_identity(b)
    assert tile_fetch_identity(a) != tile_fetch_identity(c)


@pytest.mark.unit
def test_probe_array_fits_tile_requires_identity_shape_dtype() -> None:
    req = TileRequest(
        url="https://example.com/t0",
        params={},
        width=8,
        height=8,
        output_format=Format.GEOTIFF,
    )
    other = TileRequest(
        url="https://example.com/t1",
        params={},
        width=8,
        height=8,
        output_format=Format.GEOTIFF,
    )
    array = np.zeros((8, 8), dtype=np.float32)
    assert (
        probe_array_fits_tile(
            array,
            req,
            req,
            dtype=np.dtype(np.float32),
            n_bands=1,
            chunk_height=8,
            chunk_width=8,
        )
        is True
    )
    assert (
        probe_array_fits_tile(
            array,
            req,
            other,
            dtype=np.dtype(np.float32),
            n_bands=1,
            chunk_height=8,
            chunk_width=8,
        )
        is False
    )
    assert (
        probe_array_fits_tile(
            np.zeros((4, 4), dtype=np.float32),
            req,
            req,
            dtype=np.dtype(np.float32),
            n_bands=1,
            chunk_height=8,
            chunk_width=8,
        )
        is False
    )
    assert (
        probe_array_fits_tile(
            array,
            req,
            req,
            dtype=np.dtype(np.float64),
            n_bands=1,
            chunk_height=8,
            chunk_width=8,
        )
        is False
    )


@pytest.mark.unit
def test_create_array_reuses_probe_n_tiles_n_fetches(
    monkeypatch: MonkeyPatch,
) -> None:
    """Preserve GeoTIFF: probe is tile 0's download — N tiles → N HTTP fetches."""

    rows, cols = 2, 2
    n_tiles = rows * cols
    tile_size = 64
    _install_grid_service(monkeypatch, rows=rows, cols=cols)
    fetched = _counting_fetch(monkeypatch, _geotiff_bytes())
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=2, crs=CRS.EPSG_4326)

    result = array_module.create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(tile_size, tile_size),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        compute=True,
    )

    assert len(fetched) == n_tiles
    assert result.shape == (tile_size * rows, tile_size * cols)
    assert np.isfinite(result.values).any()


@pytest.mark.unit
def test_create_array_mismatch_identity_refetches_probed_tile(
    monkeypatch: MonkeyPatch,
) -> None:
    """When probe identity does not match any mosaic tile, every tile is fetched."""

    rows, cols = 2, 2
    n_tiles = rows * cols
    tile_size = 64
    _install_grid_service(monkeypatch, rows=rows, cols=cols)
    fetched = _counting_fetch(monkeypatch, _geotiff_bytes())

    real_probe = planning_module.probe_tile_bands

    def probe_wrong_identity(*args: Any, **kwargs: Any) -> BandProbeResult:
        result = real_probe(*args, **kwargs)
        assert result.array is not None and result.request is not None
        alien = result.request.model_copy(
            update={"url": "https://example.com/alien-probe.tif"}
        )
        return BandProbeResult(
            n_bands=result.n_bands, array=result.array, request=alien
        )

    monkeypatch.setattr(array_module, "_probe_tile_bands", probe_wrong_identity)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=2, crs=CRS.EPSG_4326)
    array_module.create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(tile_size, tile_size),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        compute=True,
    )

    # Probe fetch + N mosaic fetches (no reuse).
    assert len(fetched) == n_tiles + 1


@pytest.mark.unit
def test_create_array_mismatch_shape_refetches_probed_tile(
    monkeypatch: MonkeyPatch,
) -> None:
    """Shape-incompatible probe payload must not be placed — refetch the tile."""

    rows, cols = 2, 1
    n_tiles = rows * cols
    tile_size = 64
    _install_grid_service(monkeypatch, rows=rows, cols=cols)
    fetched = _counting_fetch(monkeypatch, _geotiff_bytes())

    real_probe = planning_module.probe_tile_bands

    def probe_wrong_shape(*args: Any, **kwargs: Any) -> BandProbeResult:
        result = real_probe(*args, **kwargs)
        assert result.array is not None and result.request is not None
        wrong = np.zeros((4, 4), dtype=result.array.dtype)
        return BandProbeResult(
            n_bands=result.n_bands, array=wrong, request=result.request
        )

    monkeypatch.setattr(array_module, "_probe_tile_bands", probe_wrong_shape)

    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=2, crs=CRS.EPSG_4326)
    array_module.create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(tile_size, tile_size),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        compute=True,
    )

    assert len(fetched) == n_tiles + 1


@pytest.mark.unit
def test_create_array_first_band_skips_probe_http(
    monkeypatch: MonkeyPatch,
) -> None:
    """Format-fixed band_policy skips the probe — N tiles → N fetches only."""

    rows, cols = 2, 2
    n_tiles = rows * cols
    tile_size = 64
    _install_grid_service(monkeypatch, rows=rows, cols=cols)
    fetched = _counting_fetch(monkeypatch, _geotiff_bytes())
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=2, crs=CRS.EPSG_4326)

    array_module.create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(tile_size, tile_size),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        band_policy="first_band",
        compute=True,
    )

    assert len(fetched) == n_tiles


@pytest.mark.unit
def test_probe_reuse_pixels_match_refetch(
    monkeypatch: MonkeyPatch,
) -> None:
    """Reused probe pixels match a path that refetches the same payload."""

    rows, cols = 1, 2
    tile_size = 64
    payload = _geotiff_bytes()
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=1, crs=CRS.EPSG_4326)

    _install_grid_service(monkeypatch, rows=rows, cols=cols)
    _counting_fetch(monkeypatch, payload)
    reused = array_module.create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(tile_size, tile_size),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        compute=True,
    )

    _install_grid_service(monkeypatch, rows=rows, cols=cols)
    _counting_fetch(monkeypatch, payload)

    def never_fit(*args: Any, **kwargs: Any) -> bool:
        return False

    monkeypatch.setattr(array_module, "_probe_array_fits_tile", never_fit)
    refetched = array_module.create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(tile_size, tile_size),
        grid_shape=(rows, cols),
        output_format=Format.GEOTIFF,
        compute=True,
    )

    np.testing.assert_array_equal(reused.values, refetched.values)
