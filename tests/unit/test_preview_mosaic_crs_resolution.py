"""Preview applies ground resolution in mosaic CRS when output_crs is set."""

from __future__ import annotations

import pytest
import respx

from tilearray import WMSConfig, preview_array
from tilearray.types import CRS

pytestmark = pytest.mark.unit


def test_preview_applies_ground_resolution_in_mosaic_crs_edinburgh_20m() -> None:
    """Lon/lat AOI + output_crs=27700 + resolution 20 m → mosaic-CRS pixels, few tiles."""
    cfg = WMSConfig.from_url("https://example.com/wms", layers="a")
    with respx.mock(assert_all_called=False):
        pv = preview_array(
            cfg,
            (-3.25, 55.92, -3.10, 55.98),
            bbox_crs="EPSG:4326",
            output_crs="EPSG:27700",
            resolution=(20, 20),
        )
    # Edinburgh box at 20 m is hundreds of pixels / a handful of 256 tiles —
    # never ~130k one-pixel requests.
    assert pv.request_count < 50
    assert pv.request_count >= 1
    assert pv.mosaic_crs in ("EPSG:27700", CRS("EPSG:27700"))
    assert pv.planned_pixel_size is not None
    assert abs(pv.planned_pixel_size[0] - 20.0) < 1e-6
    assert abs(pv.planned_pixel_size[1] - 20.0) < 1e-6
