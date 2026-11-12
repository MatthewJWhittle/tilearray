"""WCS GetCoverage: scaleSize, lat-first subset axes, native subsettingCRS omitted."""

from __future__ import annotations

import pytest

from tests.unit.hands_on_examples import tile_geometry
from tilearray.service.wcs import WCSService
from tilearray.types import CRS, CoverageDescription, Format

pytestmark = pytest.mark.unit


def test_wcs201_getcoverage_includes_scalesize() -> None:
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
        )
    )
    scale = req.params.get("scaleSize")
    assert scale is not None
    assert "256" in str(scale)
    assert "x(256)" in str(scale).replace(" ", "")


def test_lat_first_coverage_subsets_use_axis_labels() -> None:
    """Lat-first native axes: subset keys follow caps labels; values match AOI."""
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:4258"),
            axis_labels={"EPSG:4258": ("y", "x")},
            native_format=Format.GEOTIFF,
        )
    )
    req = svc.build_tile_request(
        tile_geometry(
            min_x=2.8,
            min_y=50.775,
            max_x=2.825,
            max_y=50.79,
            crs=CRS("EPSG:4258"),
            width=250,
            height=150,
        )
    )
    subsets = req.params["subset"]
    assert isinstance(subsets, list)
    assert any(s.startswith("y(50.775") for s in subsets)
    assert any(s.startswith("x(2.8") for s in subsets)
    # Native CRS: omit short-form subsettingCRS (GeoServer rejects EPSG:NNNN).
    assert "subsettingCRS" not in req.params
    # scaleSize must use the same axis meaning as subsets (height on y).
    scale = str(req.params.get("scaleSize", "")).replace(" ", "")
    assert "y(150)" in scale
    assert "x(250)" in scale


def test_opaque_lat_first_labels_align_scalesize_with_subset() -> None:
    """Opaque axis names under lat-first CRS: scaleSize matches subset meaning."""
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:4326"),
            axis_labels={"EPSG:4326": ("i", "j")},
            native_format=Format.GEOTIFF,
        )
    )
    req = svc.build_tile_request(
        tile_geometry(
            min_x=2.8,
            min_y=50.775,
            max_x=2.825,
            max_y=50.79,
            crs=CRS("EPSG:4326"),
            width=250,
            height=150,
        )
    )
    # EPSG:4326 is north-axis-first: label0=i → northing/height, label1=j → easting/width.
    scale = str(req.params.get("scaleSize", "")).replace(" ", "")
    assert "i(150)" in scale
    assert "j(250)" in scale
    subsets = req.params["subset"]
    assert any(s.startswith("i(50.775") for s in subsets)
    assert any(s.startswith("j(2.8") for s in subsets)
