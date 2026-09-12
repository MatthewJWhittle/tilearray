# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingImports=false

"""Integration tests for tilearray against real OGC services."""

from typing import Any

import pytest
import xarray as xr

from tilearray import array as array_module
from tilearray.service.config import WCSConfig
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
            crs=CRS.EPSG_27700,
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
            crs=CRS.EPSG_27700,
        )

        assert result.data.chunks == (chunk_size, chunk_size)
        data = result.compute()  # sequential requests for stability
        assert isinstance(data, xr.DataArray)
        assert data.shape == (width, width)
        mean_value = float(data.mean())
        assert -1000 < mean_value < 1000
        # assert no missing values
        assert data.isnull().sum() == 0
