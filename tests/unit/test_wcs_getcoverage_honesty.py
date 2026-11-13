"""WCS GetCoverage: scaleSize only when non-native; lat-first axes; native CRS omit."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.hands_on_examples import tile_geometry
from tilearray.fetch_presets import EA_LIDAR_BENCH_BBOX, EA_LIDAR_COVERAGE_ID
from tilearray.service.wcs import WCSParser, WCSService
from tilearray.types import CRS, CoverageDescription, Format

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "wcs_fixtures"


def test_wcs201_getcoverage_includes_scalesize_when_non_native() -> None:
    """MapServer / #154: non-native output still gets scaleSize (+ width/height)."""
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:28992"),
            axis_labels={"EPSG:28992": ("x", "y")},
            native_format=Format.GEOTIFF,
            native_resolution=(0.5, 0.5),
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
    assert req.params.get("width") == "256"
    assert req.params.get("height") == "256"
    assert req.retries == 0


def test_wcs201_getcoverage_omits_scalesize_at_native_resolution() -> None:
    """Native GSD: omit scaleSize and vendor width/height (#177)."""
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:27700"),
            axis_labels={"EPSG:27700": ("E", "N")},
            native_format=Format.GEOTIFF,
            native_resolution=(1.0, 1.0),
        )
    )
    req = svc.build_tile_request(
        tile_geometry(
            min_x=431900.0,
            min_y=382700.0,
            max_x=431964.0,
            max_y=382764.0,
            crs=CRS("EPSG:27700"),
            width=64,
            height=64,
        )
    )
    assert "scaleSize" not in req.params
    assert "width" not in req.params
    assert "height" not in req.params


def test_lat_first_coverage_subsets_use_axis_labels() -> None:
    """Lat-first native axes: subset keys follow caps labels; values match AOI."""
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:4258"),
            axis_labels={"EPSG:4258": ("y", "x")},
            native_format=Format.GEOTIFF,
            # Requested GSD is coarser than native → scaleSize kept (#154).
            native_resolution=(0.00001, 0.00001),
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
            native_resolution=(0.00001, 0.00001),
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


@pytest.mark.parametrize(
    ("fixture_name", "bbox", "width", "height", "expect_scalesize"),
    [
        (
            "ea_lidar_describe_coverage.xml",
            EA_LIDAR_BENCH_BBOX,
            64,
            64,
            False,
        ),
        (
            "usgs_3dep_describe_coverage.xml",
            (
                -11_689_046.533293726,
                4_829_179.18977684,
                -11_688_046.533293726,
                4_830_179.18977684,
            ),
            16,
            16,
            True,
        ),
        (
            "pdok_ahn_describe_coverage.xml",
            (100_000.0, 485_000.0, 102_560.0, 487_560.0),
            256,
            256,
            True,
        ),
    ],
    ids=["ea_native_1m", "usgs_coarser_than_native", "pdok_mapserver_coarser"],
)
def test_host_preset_getcoverage_scalesize_shape(
    fixture_name: str,
    bbox: tuple[float, float, float, float],
    width: int,
    height: int,
    expect_scalesize: bool,
) -> None:
    """Request shape for each recorded / stub host preset (#177)."""
    parser = WCSParser("https://example.com/wcs")
    metadata = parser.parse_describe_coverage((FIXTURES / fixture_name).read_text())
    assert metadata.native_resolution is not None
    svc = WCSService(
        "https://example.com/wcs",
        coverage_id=metadata.identifier,
        crs=metadata.native_crs,
    )
    svc.set_coverage_metadata(metadata)
    min_x, min_y, max_x, max_y = bbox
    req = svc.build_tile_request(
        tile_geometry(
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
            crs=metadata.native_crs or CRS.EPSG_4326,
            width=width,
            height=height,
        )
    )
    if expect_scalesize:
        assert "scaleSize" in req.params
        assert req.params.get("width") == str(width)
        assert req.params.get("height") == str(height)
    else:
        assert "scaleSize" not in req.params
        assert "width" not in req.params
        assert "height" not in req.params
        assert metadata.identifier == EA_LIDAR_COVERAGE_ID
