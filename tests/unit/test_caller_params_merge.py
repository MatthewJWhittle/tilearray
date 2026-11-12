"""Caller params= must merge into protocol tile HTTP params (no TypeError)."""

from __future__ import annotations

import pytest

from tests.unit.hands_on_examples import tile_geometry
from tilearray.service.wcs import WCSService
from tilearray.service.wms import WMSService
from tilearray.service.wmts import WMTSService
from tilearray.service.xyz import XYZService
from tilearray.types import CRS, CoverageDescription, Format

pytestmark = pytest.mark.unit


def test_wms_merges_caller_params_into_getmap() -> None:
    req = WMSService(
        "https://example.com/wms", layers="a", output_format=Format.PNG
    ).build_tile_request(tile_geometry(), params={"key": "x"})
    assert req.params.get("key") == "x"
    assert (
        req.params.get("service") == "WMS"
        or req.params.get("REQUEST") == "GetMap"
        or "GetMap" in str(req.params.get("request"))
    )


def test_wcs_merges_caller_params_into_getcoverage() -> None:
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:28992"),
            axis_labels={"EPSG:28992": ("x", "y")},
            native_format=Format.GEOTIFF,
        )
    )
    req = svc.build_tile_request(
        tile_geometry(
            max_x=2560,
            max_y=2560,
            crs=CRS("EPSG:28992"),
            width=256,
            height=256,
        ),
        params={"key": "x"},
    )
    assert req.params.get("key") == "x"


def test_wmts_kvp_merges_caller_params_into_gettile() -> None:
    geom = tile_geometry(tile_x=0, tile_y=0, zoom=1)
    req = WMTSService(
        "https://example.com/wmts",
        layer="a",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix="1",
        output_format=Format.PNG,
    ).build_tile_request(geom, params={"key": "x"})
    assert req.params.get("key") == "x"


def test_xyz_still_carries_caller_params() -> None:
    """Regression lock: XYZ already merged params — keep the key once."""
    geom = tile_geometry(tile_x=0, tile_y=0, zoom=3)
    req = XYZService(
        "https://tiles.example.com/{z}/{x}/{y}.png", zoom=3
    ).build_tile_request(geom, params={"key": "x"})
    assert req.params.get("key") == "x" or "key=x" in req.url
