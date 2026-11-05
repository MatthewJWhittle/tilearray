# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingImports=false

"""Integration tests for tilearray against real OGC services."""

from typing import Any

import pytest
import xarray as xr

from tilearray import array as array_module
from tilearray import compute_with_policy, create_array
from tilearray.service.config import WCSConfig, WMTSConfig
from tilearray.service.wcs import WCSService
from tilearray.types import CRS, Format


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.net
class TestRealServiceIntegration:
    """Smoke tests against live WCS endpoints."""

    SERVICE_URL = "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs"

    def test_get_capabilities(self):
        service = WCSService(self.SERVICE_URL, crs=CRS.EPSG_4326)
        capabilities: Any = service.get_capabilities()  # type: ignore[attr-defined]

        assert capabilities.service_title
        assert capabilities.coverages

    def test_describe_first_coverage(self):
        service = WCSService(self.SERVICE_URL, crs=CRS.EPSG_4326)
        capabilities: Any = service.get_capabilities()  # type: ignore[attr-defined]
        first: Any = capabilities.coverages[0]

        description: Any = service.describe_coverage(first.identifier)  # type: ignore[attr-defined]
        assert description.identifier == first.identifier
        assert description.spatial_extent is not None

    def test_fetch_array_from_wcs(self):
        service = WCSService(self.SERVICE_URL, crs=CRS.EPSG_4326)
        capabilities: Any = service.get_capabilities()  # type: ignore[attr-defined]
        coverage_id: Any = capabilities.coverages[0].identifier

        bbox = (
            431900.0,
            382700.0,
            432700.0,
            383500.0,
        )  # 800 x 800 m tile in EPSG:27700 over England
        config = WCSConfig.from_url(
            self.SERVICE_URL,
            coverage_id=coverage_id,
            crs=CRS.EPSG_27700,
            output_format=Format.GEOTIFF,
            chunk_size=(800, 800),
            grid_shape=(1, 1),
        )

        result = array_module.create_array(
            service_url=config,
            bbox=bbox,
            bbox_crs=CRS.EPSG_27700,
        )
        data: Any = result.compute()  # type: ignore[call-arg]

        assert isinstance(data, xr.DataArray)
        assert data.shape == (800, 800)
        mean_value = float(data.mean())
        assert -1000 < mean_value < 1000

    def test_fetch_array_from_wcs_multiple_tiles(self):
        service = WCSService(self.SERVICE_URL, crs=CRS.EPSG_27700)
        capabilities: Any = service.get_capabilities()  # type: ignore[attr-defined]
        coverage_id: Any = capabilities.coverages[0].identifier

        width = 128
        xmin, ymin = 431900.0, 382700.0
        xmax = xmin + width
        ymax = ymin + width
        bbox = (xmin, ymin, xmax, ymax)
        grid_shape = (2, 2)
        chunk_size = (width // grid_shape[0], width // grid_shape[1])

        config = WCSConfig.from_url(
            self.SERVICE_URL,
            coverage_id=coverage_id,
            crs=CRS.EPSG_27700,
            output_format=Format.GEOTIFF,
            chunk_size=chunk_size,
            resolution=(1.0, 1.0),
        )

        result = array_module.create_array(
            service_url=config,
            bbox=bbox,
            bbox_crs=CRS.EPSG_27700,
        )

        assert result.data.chunks == (chunk_size, chunk_size)
        data = result.compute()  # sequential requests for stability
        assert isinstance(data, xr.DataArray)
        assert data.shape == (width, width)
        mean_value = float(data.mean())
        assert -1000 < mean_value < 1000
        # assert no missing values
        assert data.isnull().sum() == 0


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.net
def test_live_kartverket_wmts_oslo_smoke() -> None:
    """Tiny live smoke: Kartverket topo / utm33n → EPSG:25833 mosaic."""

    config = WMTSConfig.from_url(
        "https://cache.kartverket.no/v1/service",
        layer="topo",
        tile_matrix_set="utm33n",
        tile_matrix=8,
        style="default",
        output_format=Format.PNG,
        chunk_size=(256, 256),
        max_concurrent_requests=2,
        rate_limit_per_second=2.0,
        headers={
            "User-Agent": (
                "tilearray-integration/0.2 "
                "(+https://github.com/MatthewJWhittle/tilearray)"
            ),
        },
    )
    da = create_array(
        config,
        bbox=(10.6, 59.85, 10.8, 59.95),
        bbox_crs=CRS.EPSG_4326,
        show_progress=False,
    )
    arr = compute_with_policy(da)

    assert isinstance(arr, xr.DataArray)
    assert da.attrs["crs"] == "EPSG:25833"
    assert arr.shape[0] >= 256
    assert arr.shape[1] >= 256
    assert float(arr.x.min()) < float(arr.x.max())
    assert float(arr.y.min()) < float(arr.y.max())
