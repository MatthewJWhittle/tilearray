"""WMS/WCS default chunk_size is 1024 (still coupled); XYZ/WMTS stay ~256 (#181)."""

from __future__ import annotations

import pytest

from tilearray import WCSConfig, WMSConfig, XYZConfig, preview_array
from tilearray.service.base import BaseService
from tilearray.service.config import WMTSConfig
from tilearray.types import CRS, Format, ServiceTypeEnum, TileRequest

pytestmark = pytest.mark.unit

# 2048×2048 m OSGB window at 1 m → 2048×2048 px. At chunk 256 that is 64 tiles;
# at the new WMS/WCS default 1024 it is 4.
_AOI_2048 = (431900.0, 382700.0, 433948.0, 384748.0)
_RESOLUTION = (1.0, 1.0)


class _PlanningService(BaseService):
    """Minimal service that uses BaseService resolution tiling."""

    service_type = ServiceTypeEnum.WCS
    output_format = Format.GEOTIFF

    def build_tile_request(self, tile, **options):  # type: ignore[no-untyped-def]
        return TileRequest(
            url=self.base_url,
            params={},
            bbox=tile.bbox,
            width=tile.width,
            height=tile.height,
            output_format=self.output_format,
            crs=tile.crs,
        )


def test_wcs_default_chunk_size_is_1024() -> None:
    config = WCSConfig.from_url("https://example.com/wcs", coverage_id="cov")
    assert config.chunk_size == (1024, 1024)
    assert config.array_defaults()["chunk_size"] == (1024, 1024)


def test_wms_default_chunk_size_is_1024() -> None:
    config = WMSConfig.from_url("https://example.com/wms", layers="layer")
    assert config.chunk_size == (1024, 1024)
    assert config.array_defaults()["chunk_size"] == (1024, 1024)


def test_xyz_and_wmts_defaults_stay_near_256() -> None:
    xyz = XYZConfig.from_url(
        "https://tiles.example.com/{z}/{x}/{y}.png",
        zoom=10,
        output_format=Format.PNG,
    )
    assert xyz.tile_size == 256
    assert xyz.chunk_size is None
    assert "chunk_size" not in xyz.array_defaults()

    wmts = WMTSConfig.from_url(
        "https://example.com/wmts",
        layer="layer",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=10,
    )
    assert wmts.chunk_size is None
    assert "chunk_size" not in wmts.array_defaults()


def test_wcs_2048_aoi_default_plans_at_most_four_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default WCS settings on a 2048-class AOI must plan ≤4 tiles (was 64 at 256)."""

    service = _PlanningService("https://example.com/wcs")
    config = WCSConfig.from_url(
        "https://example.com/wcs",
        coverage_id="cov",
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        resolution=_RESOLUTION,
    )
    # Caller omits chunk_size — service default must apply.
    assert config.chunk_size == (1024, 1024)

    monkeypatch.setattr(WCSConfig, "build_service", lambda self: service)

    preview = preview_array(config, _AOI_2048, CRS.EPSG_27700)
    assert preview.request_count <= 4
    assert preview.request_count == 4
    assert preview.grid_shape == (2, 2)


def test_wms_2048_aoi_default_plans_at_most_four_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _PlanningService("https://example.com/wms")
    service.service_type = ServiceTypeEnum.WMS
    service.output_format = Format.PNG

    config = WMSConfig.from_url(
        "https://example.com/wms",
        layers="layer",
        crs=CRS.EPSG_27700,
        output_format=Format.PNG,
        resolution=_RESOLUTION,
    )
    monkeypatch.setattr(WMSConfig, "build_service", lambda self: service)

    preview = preview_array(config, _AOI_2048, CRS.EPSG_27700)
    assert preview.request_count <= 4
    assert preview.request_count == 4
