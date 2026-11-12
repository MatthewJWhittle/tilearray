"""WCS coverage envelope clips GetCoverage subsets; outside tiles pad nodata."""

from __future__ import annotations

from io import BytesIO
from typing import Any
from urllib.parse import parse_qs

import httpx
import numpy as np
import pytest
import respx
import tifffile

from tilearray import compute_mosaic, create_array
from tilearray.errors import ValidationError
from tilearray.service.base import TileGeometry
from tilearray.service.config import WCSConfig
from tilearray.service.wcs import WCSParser, WCSService
from tilearray.types import CRS, BoundingBox, CoverageDescription, Format, SpatialExtent

pytestmark = pytest.mark.unit

# Flanders-shaped envelope (EPSG:31370): coverage starts at SW (18000, 152000).
_ENVELOPE_MIN_X = 18000.0
_ENVELOPE_MIN_Y = 152000.0
_ENVELOPE_MAX_X = 260000.0
_ENVELOPE_MAX_Y = 250000.0
_CRS = CRS.parse("EPSG:31370")
# AOI straddles the SW corner (1280 m at 5 m → 256 px; 128-px chunks → 2×2).
_AOI = (17360.0, 151360.0, 18640.0, 152640.0)
_BASE_URL = "https://example.com/dhmv/wcs"
_COVERAGE_ID = "DHMVI_DTM_5m"


def _describe_xml(*, axis_labels: str = "x y", y_x_corners: bool = False) -> str:
    if y_x_corners:
        lower = f"{_ENVELOPE_MIN_Y} {_ENVELOPE_MIN_X}"
        upper = f"{_ENVELOPE_MAX_Y} {_ENVELOPE_MAX_X}"
    else:
        lower = f"{_ENVELOPE_MIN_X} {_ENVELOPE_MIN_Y}"
        upper = f"{_ENVELOPE_MAX_X} {_ENVELOPE_MAX_Y}"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<wcs:CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0"
 xmlns:gml="http://www.opengis.net/gml/3.2"
 xmlns:ows="http://www.opengis.net/ows/2.0">
  <wcs:CoverageDescription>
    <gml:boundedBy>
      <gml:Envelope srsName="http://www.opengis.net/def/crs/EPSG/0/31370"
                    axisLabels="{axis_labels}" uomLabels="m m" srsDimension="2">
        <gml:lowerCorner>{lower}</gml:lowerCorner>
        <gml:upperCorner>{upper}</gml:upperCorner>
      </gml:Envelope>
    </gml:boundedBy>
    <wcs:CoverageId>{_COVERAGE_ID}</wcs:CoverageId>
    <wcs:SupportedCRS>EPSG:31370</wcs:SupportedCRS>
    <wcs:ServiceParameters>
      <wcs:CoverageSubtype>RectifiedGridCoverage</wcs:CoverageSubtype>
      <wcs:nativeFormat>image/tiff</wcs:nativeFormat>
    </wcs:ServiceParameters>
  </wcs:CoverageDescription>
</wcs:CoverageDescriptions>"""


def _geotiff_bytes(
    array: np.ndarray,
    *,
    ul_x: float,
    ul_y: float,
    scale: float = 5.0,
    epsg: int = 31370,
) -> bytes:
    extratags: list[tuple[Any, ...]] = [
        (33550, 12, 3, [float(scale), float(scale), 0.0], False),
        (33922, 12, 6, [0.0, 0.0, 0.0, float(ul_x), float(ul_y), 0.0], False),
        (34735, 3, 8, [1, 1, 0, 1, 3072, 0, 1, int(epsg)], False),
    ]
    buffer = BytesIO()
    tifffile.imwrite(buffer, array, extratags=extratags)
    return buffer.getvalue()


def _subset_outside_envelope(subset_values: list[str]) -> bool:
    """True when any subset interval lies wholly outside the envelope."""

    x_lo = x_hi = y_lo = y_hi = None
    for part in subset_values:
        if part.startswith("x(") or part.startswith("E("):
            inner = part[part.index("(") + 1 : part.rindex(")")]
            a, b = (float(v) for v in inner.split(","))
            x_lo, x_hi = a, b
        elif part.startswith("y(") or part.startswith("N("):
            inner = part[part.index("(") + 1 : part.rindex(")")]
            a, b = (float(v) for v in inner.split(","))
            y_lo, y_hi = a, b
    if None in (x_lo, x_hi, y_lo, y_hi):
        return True
    assert x_lo is not None and x_hi is not None
    assert y_lo is not None and y_hi is not None
    return (
        x_hi <= _ENVELOPE_MIN_X
        or y_hi <= _ENVELOPE_MIN_Y
        or (x_lo >= _ENVELOPE_MAX_X or y_lo >= _ENVELOPE_MAX_Y)
    )


def _service_with_extent(
    *,
    axis_labels: tuple[str, str] = ("x", "y"),
) -> WCSService:
    service = WCSService(
        _BASE_URL,
        coverage_id=_COVERAGE_ID,
        output_format=Format.GEOTIFF,
        crs=_CRS,
    )
    service.set_coverage_metadata(
        CoverageDescription(
            identifier=_COVERAGE_ID,
            native_crs=_CRS,
            native_format=Format.GEOTIFF,
            supported_crs=[_CRS],
            supported_formats=[Format.GEOTIFF],
            axis_labels={_CRS.value: axis_labels},
            spatial_extent=SpatialExtent(
                bbox=BoundingBox(
                    min_x=_ENVELOPE_MIN_X,
                    min_y=_ENVELOPE_MIN_Y,
                    max_x=_ENVELOPE_MAX_X,
                    max_y=_ENVELOPE_MAX_Y,
                    crs=_CRS,
                )
            ),
        )
    )
    return service


def test_parser_honours_y_x_axis_labels_for_spatial_extent() -> None:
    parser = WCSParser(_BASE_URL)
    description = parser.parse_describe_coverage(
        _describe_xml(axis_labels="y x", y_x_corners=True)
    )
    assert description.spatial_extent is not None
    bbox = description.spatial_extent.bbox
    assert bbox.min_x == pytest.approx(_ENVELOPE_MIN_X)
    assert bbox.min_y == pytest.approx(_ENVELOPE_MIN_Y)
    assert bbox.max_x == pytest.approx(_ENVELOPE_MAX_X)
    assert bbox.max_y == pytest.approx(_ENVELOPE_MAX_Y)


def test_build_tile_request_marks_wholly_outside_empty() -> None:
    service = _service_with_extent()
    outside = TileGeometry(
        bbox=BoundingBox(
            min_x=17360.0,
            min_y=151360.0,
            max_x=18000.0,
            max_y=152000.0,
            crs=_CRS,
        ),
        width=128,
        height=128,
        crs=_CRS,
    )
    request = service.build_tile_request(outside)
    assert request.coverage_empty is True
    assert "subset" not in request.params


def test_build_tile_request_clips_straddling_subset() -> None:
    service = _service_with_extent()
    straddling = TileGeometry(
        bbox=BoundingBox(
            min_x=17360.0,
            min_y=151360.0,
            max_x=18640.0,
            max_y=152640.0,
            crs=_CRS,
        ),
        width=256,
        height=256,
        crs=_CRS,
    )
    request = service.build_tile_request(straddling)
    assert request.coverage_empty is False
    assert request.width == 256
    assert request.height == 256
    subsets = request.params["subset"]
    assert isinstance(subsets, list)
    assert not _subset_outside_envelope(subsets)
    assert any("x(18000" in part.replace(" ", "") for part in subsets)
    assert any("y(152000" in part.replace(" ", "") for part in subsets)
    assert request.params["width"] == "128"
    assert request.params["height"] == "128"


def test_planned_getcoverage_never_leaves_envelope() -> None:
    service = _service_with_extent()
    aoi = BoundingBox.from_tuple(_AOI, crs=_CRS)
    plan = service.generate_tile_requests(
        aoi,
        chunk_size=(128, 128),
        resolution=(5.0, 5.0),
        origin=(_AOI[0], _AOI[1]),
    )
    fetchable = [tile for tile in plan.tiles if not tile.coverage_empty]
    empty = [tile for tile in plan.tiles if tile.coverage_empty]
    assert empty, "expected outside tiles to be coverage_empty"
    assert fetchable, "expected at least one in-extent tile"
    for tile in fetchable:
        subsets = tile.params["subset"]
        assert isinstance(subsets, list)
        assert not _subset_outside_envelope(subsets)
    for tile in empty:
        assert "subset" not in tile.params


def test_fully_inside_aoi_unchanged() -> None:
    service = _service_with_extent()
    inside = BoundingBox(
        min_x=19000.0,
        min_y=160000.0,
        max_x=19640.0,
        max_y=160640.0,
        crs=_CRS,
    )
    plan = service.generate_tile_requests(
        inside,
        chunk_size=(128, 128),
        resolution=(5.0, 5.0),
        origin=(19000.0, 160000.0),
    )
    assert plan.tiles
    assert all(not tile.coverage_empty for tile in plan.tiles)
    for tile in plan.tiles:
        assert tile.params["width"] == str(tile.width)
        assert tile.params["height"] == str(tile.height)
        assert tile.bbox is not None
        subsets = tile.params["subset"]
        assert isinstance(subsets, list)
        # Subset matches the planned tile bbox (no extra clip).
        assert any(
            f"x({tile.bbox.min_x},{tile.bbox.max_x})" in part.replace(" ", "")
            or f"x({tile.bbox.min_x:.1f},{tile.bbox.max_x:.1f})"
            in part.replace(" ", "")
            for part in subsets
        )


@respx.mock
def test_soft_fail_straddle_returns_nodata_outside_without_outside_fetch() -> None:
    """create_array + soft_fail on a SW-corner straddle: shape held, NaN outside."""

    getcoverage_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        req_name = (params.get("request") or params.get("REQUEST") or [""])[0]
        if req_name == "DescribeCoverage":
            return httpx.Response(200, text=_describe_xml())
        if req_name == "GetCoverage":
            getcoverage_urls.append(str(request.url))
            subsets = params.get("subset") or params.get("SUBSET") or []
            assert not _subset_outside_envelope(list(subsets))
            # In-extent NE tile: [18000,18640] × [152000,152640], UL=(18000,152640).
            pixels = np.full((128, 128), 42.0, dtype=np.float32)
            raw = _geotiff_bytes(pixels, ul_x=18000.0, ul_y=152640.0, scale=5.0)
            return httpx.Response(
                200, content=raw, headers={"content-type": "image/tiff"}
            )
        return httpx.Response(404, text="unexpected")

    respx.get(_BASE_URL).mock(side_effect=handler)

    cfg = WCSConfig.from_url(
        _BASE_URL,
        _COVERAGE_ID,
        crs=_CRS,
        output_format=Format.GEOTIFF,
        chunk_size=(128, 128),
        resolution=(5.0, 5.0),
        max_concurrent_requests=2,
    )
    da = create_array(
        cfg,
        _AOI,
        bbox_crs=_CRS,
        show_progress=False,
        soft_fail=True,
        soft_fail_max_fraction=1.0,
        origin=(_AOI[0], _AOI[1]),
    )
    result = compute_mosaic(da)

    assert result.shape == (256, 256)
    values = np.asarray(result.values)
    # NE quarter inside envelope has finite DTM values; SW of window is nodata.
    assert np.all(values[0:128, 128:256] == pytest.approx(42.0))
    assert np.isnan(values[128:256, 0:128]).all()
    # Outside tiles never hit GetCoverage.
    assert getcoverage_urls, "expected at least one in-extent GetCoverage"
    for url in getcoverage_urls:
        params = parse_qs(url.split("?", 1)[1]) if "?" in url else {}
        subsets = params.get("subset") or params.get("SUBSET") or []
        assert not _subset_outside_envelope(list(subsets))
    # Coverage pads are not soft-fail counters.
    errors = result.attrs.get("tilearray_tile_errors") or {}
    assert errors.get("soft_failed", 0) == 0


@respx.mock
def test_probe_uses_in_extent_tile_when_southwest_is_outside() -> None:
    """Band probe must not sample the out-of-extent SW tile (Flanders geometry)."""

    probed: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        req_name = (params.get("request") or params.get("REQUEST") or [""])[0]
        if req_name == "DescribeCoverage":
            return httpx.Response(200, text=_describe_xml())
        if req_name == "GetCoverage":
            probed.append(str(request.url))
            subsets = params.get("subset") or params.get("SUBSET") or []
            assert not _subset_outside_envelope(list(subsets))
            pixels = np.full((128, 128), 7.0, dtype=np.float32)
            raw = _geotiff_bytes(pixels, ul_x=18000.0, ul_y=152640.0, scale=5.0)
            return httpx.Response(
                200, content=raw, headers={"content-type": "image/tiff"}
            )
        return httpx.Response(404, text="unexpected")

    respx.get(_BASE_URL).mock(side_effect=handler)

    cfg = WCSConfig.from_url(
        _BASE_URL,
        _COVERAGE_ID,
        crs=_CRS,
        output_format=Format.GEOTIFF,
        chunk_size=(128, 128),
        resolution=(5.0, 5.0),
        max_concurrent_requests=2,
    )
    # create_array probes before building the graph; must succeed.
    da = create_array(
        cfg,
        _AOI,
        bbox_crs=_CRS,
        show_progress=False,
        soft_fail=True,
        soft_fail_max_fraction=1.0,
        origin=(_AOI[0], _AOI[1]),
    )
    assert da.shape[0] == 256
    assert probed, "probe should fetch an in-extent tile"


@respx.mock
def test_entire_aoi_outside_raises_validation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = parse_qs(request.url.query.decode())
        req_name = (params.get("request") or params.get("REQUEST") or [""])[0]
        if req_name == "DescribeCoverage":
            return httpx.Response(200, text=_describe_xml())
        return httpx.Response(500, text="should not GetCoverage")

    respx.get(_BASE_URL).mock(side_effect=handler)
    cfg = WCSConfig.from_url(
        _BASE_URL,
        _COVERAGE_ID,
        crs=_CRS,
        output_format=Format.GEOTIFF,
        chunk_size=(64, 64),
        resolution=(5.0, 5.0),
    )
    outside = (1000.0, 1000.0, 1640.0, 1640.0)
    with pytest.raises(ValidationError, match="does not intersect coverage extent"):
        create_array(cfg, outside, bbox_crs=_CRS, show_progress=False)
