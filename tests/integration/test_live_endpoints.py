# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false, reportMissingImports=false

"""Integration tests for tilearray against real OGC services."""

from typing import Any

import numpy as np
import pytest
import xarray as xr

from tilearray import array as array_module
from tilearray import compute_mosaic, create_array, inspect_endpoint, preview_array
from tilearray.fetch_presets import OSM_TILE_TEMPLATE, osm_fetch_defaults
from tilearray.service.config import WCSConfig, WMSConfig, WMTSConfig, XYZConfig
from tilearray.service.wcs import WCSService
from tilearray.types import CRS, Format, ServiceTypeEnum


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


_KARTVERKET_WMTS_URL = "https://cache.kartverket.no/v1/service"
_KARTVERKET_UA = {
    "User-Agent": (
        "tilearray-integration/0.2 (+https://github.com/MatthewJWhittle/tilearray)"
    ),
}


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.net
def test_live_kartverket_inspect_opaque_url_smoke() -> None:
    """Discovery path: opaque ``/v1/service`` → inspect → build_config → tiny preview.

    Typed ``WMTSConfig.from_url`` smoke below does **not** cover this front door.
    """

    inspection = inspect_endpoint(
        _KARTVERKET_WMTS_URL,
        headers=_KARTVERKET_UA,
    )
    assert inspection.service_type is ServiceTypeEnum.WMTS
    assert any(r.identifier == "topo" for r in inspection.resources)

    config = inspection.build_config(
        layer="topo",
        tile_matrix_set="utm33n",
        target_resolution=50.0,
        style="default",
        output_format=Format.PNG,
        chunk_size=(256, 256),
        max_concurrent_requests=2,
        rate_limit_per_second=2.0,
        headers=_KARTVERKET_UA,
    )
    assert isinstance(config, WMTSConfig)
    assert config.tile_matrix is not None

    preview = preview_array(
        config,
        bbox=(10.72, 59.90, 10.73, 59.91),
        bbox_crs=CRS.EPSG_4326,
    )
    assert preview.request_count >= 1
    assert preview.crs in {"EPSG:25833", "25833"}


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.net
def test_live_osm_plot_ready_smoke() -> None:
    """Tiny OSM window with ``expand_palette=True`` stays plot-ready (uint8 RGB)."""

    config = XYZConfig.from_url(
        OSM_TILE_TEMPLATE,
        zoom=15,
        output_format=Format.PNG,
        chunk_size=(256, 256),
        **osm_fetch_defaults(),
    )
    # ~one tile over central London
    da = create_array(
        config,
        bbox=(-0.128, 51.507, -0.126, 51.508),
        bbox_crs=CRS.EPSG_4326,
        expand_palette=True,
        show_progress=False,
    )
    arr = compute_mosaic(da)
    assert isinstance(arr, xr.DataArray)
    assert "band" in arr.dims
    assert arr.sizes["band"] >= 3
    assert arr.dtype == np.uint8
    assert arr.attrs.get("palette_mode") == "rgb"
    assert arr.attrs.get("dtype_policy") == "plot_ready_uint8"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.net
def test_live_gibs_xyz_bluemarble_smoke() -> None:
    """Docs-host smoke: NASA GIBS Blue Marble XYZ → uint8 RGB, EPSG:3857 mosaic."""

    template = (
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
        "BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/"
        "{z}/{y}/{x}.jpeg"
    )
    config = XYZConfig.from_url(
        template,
        zoom=3,
        output_format=Format.JPEG,
        chunk_size=(256, 256),
        max_concurrent_requests=2,
        rate_limit_per_second=2.0,
        headers={
            "User-Agent": (
                "tilearray-integration/0.3 "
                "(+https://github.com/MatthewJWhittle/tilearray)"
            ),
        },
    )
    da = create_array(
        config,
        bbox=(-40.0, 20.0, -30.0, 30.0),
        bbox_crs=CRS.EPSG_4326,
        show_progress=False,
    )
    arr = compute_mosaic(da)
    assert isinstance(arr, xr.DataArray)
    assert "band" in arr.dims
    assert arr.sizes["band"] == 3
    assert arr.dtype == np.uint8
    assert arr.attrs.get("palette_mode") != "indices"
    assert da.attrs["crs"] == "EPSG:3857"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.net
def test_live_ea_vom_wms_smoke() -> None:
    """Docs-host smoke: EA Vegetation Object Model WMS → small OSGB mosaic."""

    config = WMSConfig.from_url(
        "https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms",
        layers="Vegetation_Object_Model_2022",
        output_format=Format.PNG,
        chunk_size=(256, 256),
        grid_shape=(1, 1),
        max_concurrent_requests=2,
        rate_limit_per_second=2.0,
    )
    da = create_array(
        config,
        bbox=(430000.0, 380000.0, 430256.0, 380256.0),
        bbox_crs=CRS.EPSG_27700,
        show_progress=False,
    )
    arr = compute_mosaic(da)
    assert isinstance(arr, xr.DataArray)
    assert arr.size > 0
    assert da.attrs["crs"] in {"EPSG:27700", "27700"}
    assert da.attrs["service_type"] == "WMS"


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.net
def test_live_kartverket_wmts_oslo_smoke() -> None:
    """Tiny live smoke: Kartverket topo / utm33n → EPSG:25833 mosaic."""

    config = WMTSConfig.from_url(
        _KARTVERKET_WMTS_URL,
        layer="topo",
        tile_matrix_set="utm33n",
        tile_matrix=8,
        style="default",
        output_format=Format.PNG,
        chunk_size=(256, 256),
        max_concurrent_requests=2,
        rate_limit_per_second=2.0,
        headers=_KARTVERKET_UA,
    )
    da = create_array(
        config,
        bbox=(10.6, 59.85, 10.8, 59.95),
        bbox_crs=CRS.EPSG_4326,
        show_progress=False,
    )
    arr = compute_mosaic(da)

    assert isinstance(arr, xr.DataArray)
    assert da.attrs["crs"] == "EPSG:25833"
    assert arr.shape[0] >= 256
    assert arr.shape[1] >= 256
    assert float(arr.x.min()) < float(arr.x.max())
    assert float(arr.y.min()) < float(arr.y.max())
