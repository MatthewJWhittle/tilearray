"""Clipped / partial WCS GeoTIFF placement (#95)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import numpy as np
import pytest
import tifffile
from _pytest.monkeypatch import MonkeyPatch

import tilearray._planning as planning_module
import tilearray.array as array_module
from tilearray.decode import (
    GeoTransform,
    decoder_for_format,
    pixel_offsets_for_placement,
    place_array_on_canvas,
    read_geotiff_georef,
)
from tilearray.errors import ValidationError
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)


def _geotiff_bytes(
    array: np.ndarray,
    *,
    ul_x: float,
    ul_y: float,
    scale: float = 10.0,
    epsg: int | None = 27700,
    nodata: str | None = None,
) -> bytes:
    """Write a north-up GeoTIFF with ModelPixelScale/ModelTiepoint (+ optional EPSG)."""

    extratags: list[tuple[Any, ...]] = [
        (33550, 12, 3, [float(scale), float(scale), 0.0], False),
        (33922, 12, 6, [0.0, 0.0, 0.0, float(ul_x), float(ul_y), 0.0], False),
    ]
    if epsg is not None:
        extratags.append((34735, 3, 8, [1, 1, 0, 1, 3072, 0, 1, int(epsg)], False))
    if nodata is not None:
        extratags.append((42113, "s", 1, nodata, True))
    buffer = BytesIO()
    tifffile.imwrite(buffer, array, extratags=extratags)
    return buffer.getvalue()


@pytest.mark.unit
def test_read_geotiff_georef_scale_tiepoint_and_epsg() -> None:
    raw = _geotiff_bytes(
        np.ones((4, 4), dtype=np.float32), ul_x=100.0, ul_y=200.0, scale=5.0
    )
    georef = read_geotiff_georef(raw)
    assert georef is not None
    assert georef.transform == GeoTransform(5.0, 0.0, 100.0, 0.0, -5.0, 200.0)
    assert georef.epsg == 27700


@pytest.mark.unit
def test_pixel_offsets_reject_resolution_mismatch() -> None:
    request = GeoTransform(10.0, 0.0, 0.0, 0.0, -10.0, 80.0)
    response = GeoTransform(5.0, 0.0, 0.0, 0.0, -5.0, 80.0)
    with pytest.raises(ValidationError, match="resolution"):
        pixel_offsets_for_placement(
            request_transform=request, response_transform=response
        )


@pytest.mark.unit
def test_place_array_on_canvas_pads_and_copies() -> None:
    source = np.arange(4, dtype=np.float32).reshape(2, 2) + 10
    canvas = place_array_on_canvas(
        source,
        target_height=4,
        target_width=4,
        row_offset=1,
        col_offset=2,
        fill_value=float("nan"),
    )
    assert canvas.shape == (4, 4)
    assert np.isnan(canvas[0, 0])
    assert canvas[1, 2] == pytest.approx(10.0)
    assert canvas[2, 3] == pytest.approx(13.0)


@pytest.mark.unit
def test_fit_tile_places_clipped_corner_without_stretch() -> None:
    """NE-corner clip: values land at correct offsets; SW stays nodata."""

    pixels = np.arange(16, dtype=np.float32).reshape(4, 4) + 100
    raw = _geotiff_bytes(pixels, ul_x=40.0, ul_y=80.0, scale=10.0)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=80, max_y=80, crs=CRS.EPSG_27700)
    request = TileRequest(
        url="http://example.com/wcs",
        params={},
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
        bbox=bbox,
        width=8,
        height=8,
    )
    response = TileResponse(
        data=raw,
        content_type="image/tiff",
        status_code=200,
        headers={},
        url=request.url,
        success=True,
    )
    decoder = decoder_for_format(Format.GEOTIFF)
    assert decoder is not None
    array = decoder(response, request)
    placed = array_module._fit_tile_to_request(array, request, response)  # noqa: SLF001

    assert placed.shape == (8, 8)
    assert np.isnan(placed[0, 0])
    assert placed[0, 4] == pytest.approx(100.0)
    assert placed[3, 7] == pytest.approx(115.0)
    assert np.isnan(placed[4:, :]).all()
    assert np.isnan(placed[:, :4]).all()


@pytest.mark.unit
def test_fit_tile_rejects_missing_georef_on_undersize() -> None:
    buffer = BytesIO()
    tifffile.imwrite(buffer, np.ones((2, 2), dtype=np.float32))
    raw = buffer.getvalue()
    bbox = BoundingBox(min_x=0, min_y=0, max_x=40, max_y=40, crs=CRS.EPSG_27700)
    request = TileRequest(
        url="http://example.com/wcs",
        params={},
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
        bbox=bbox,
        width=4,
        height=4,
    )
    response = TileResponse(
        data=raw,
        content_type="image/tiff",
        status_code=200,
        headers={},
        url=request.url,
        success=True,
    )
    decoder = decoder_for_format(Format.GEOTIFF)
    assert decoder is not None
    array = decoder(response, request)
    with pytest.raises(ValidationError, match="ModelPixelScale|georeferencing"):
        array_module._fit_tile_to_request(array, request, response)  # noqa: SLF001


@pytest.mark.unit
def test_fit_tile_rejects_crs_mismatch() -> None:
    raw = _geotiff_bytes(
        np.ones((2, 2), dtype=np.float32),
        ul_x=0.0,
        ul_y=20.0,
        scale=10.0,
        epsg=4326,
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=40, max_y=40, crs=CRS.EPSG_27700)
    request = TileRequest(
        url="http://example.com/wcs",
        params={},
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
        bbox=bbox,
        width=4,
        height=4,
    )
    response = TileResponse(
        data=raw,
        content_type="image/tiff",
        status_code=200,
        headers={},
        url=request.url,
        success=True,
    )
    decoder = decoder_for_format(Format.GEOTIFF)
    assert decoder is not None
    array = decoder(response, request)
    with pytest.raises(ValidationError, match="CRS"):
        array_module._fit_tile_to_request(array, request, response)  # noqa: SLF001


@pytest.mark.unit
def test_create_array_clipped_wcs_keeps_shape_and_values(
    monkeypatch: MonkeyPatch,
) -> None:
    """Full mosaic path: clipped GeoTIFF → fixed request shape, honest pads."""

    pixels = np.full((4, 4), 7.0, dtype=np.float32)
    # Request 8×8 over [0,80]²; response covers SW quarter UL=(0,40).
    raw = _geotiff_bytes(pixels, ul_x=0.0, ul_y=40.0, scale=10.0)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=80, max_y=80, crs=CRS.EPSG_27700)

    class ClippedWCS:
        service_type = ServiceTypeEnum.WCS
        coverage_id = "dummy"
        output_format = Format.GEOTIFF

        def generate_tile_requests(
            self, *args: Any, **kwargs: Any
        ) -> list[TileRequest]:
            del args, kwargs
            return [
                TileRequest(
                    url="http://example.com/wcs",
                    params={},
                    output_format=Format.GEOTIFF,
                    crs=CRS.EPSG_27700,
                    bbox=bbox,
                    width=8,
                    height=8,
                )
            ]

    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: ClippedWCS())
    monkeypatch.setattr(
        array_module,
        "fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=raw,
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        ),
    )

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=bbox,
        bbox_crs=CRS.EPSG_27700,
        chunk_size=(8, 8),
        output_format=Format.GEOTIFF,
        compute=True,
        show_progress=False,
    )

    assert result.shape == (8, 8)
    assert result.attrs["transform_source"] == "mosaic_bbox"
    assert result.attrs["band_policy"] == "preserve"
    values = np.asarray(result.values)
    # SW quarter (rows 4:8, cols 0:4) filled with 7; rest NaN
    assert np.all(values[4:8, 0:4] == 7.0)
    assert np.isnan(values[0:4, :]).all()
    assert np.isnan(values[:, 4:8]).all()
    # Soft-fail counters stay unused — coverage pad ≠ fetch soft-fail
    errors = result.attrs.get("tilearray_tile_errors") or {}
    assert errors.get("soft_failed", 0) == 0


@pytest.mark.unit
def test_adjacent_clipped_tiles_seam(
    monkeypatch: MonkeyPatch,
) -> None:
    """Two adjacent clipped tiles share an edge without off-by-one shift."""

    left = np.full((4, 4), 1.0, dtype=np.float32)
    right = np.full((4, 4), 2.0, dtype=np.float32)
    left_bytes = _geotiff_bytes(left, ul_x=0.0, ul_y=40.0, scale=10.0)
    right_bytes = _geotiff_bytes(right, ul_x=40.0, ul_y=40.0, scale=10.0)
    mosaic_bbox = BoundingBox(min_x=0, min_y=0, max_x=80, max_y=40, crs=CRS.EPSG_27700)
    left_bbox = BoundingBox(min_x=0, min_y=0, max_x=40, max_y=40, crs=CRS.EPSG_27700)
    right_bbox = BoundingBox(min_x=40, min_y=0, max_x=80, max_y=40, crs=CRS.EPSG_27700)

    class TwoTileWCS:
        service_type = ServiceTypeEnum.WCS
        coverage_id = "dummy"
        output_format = Format.GEOTIFF

        def generate_tile_requests(
            self, *args: Any, **kwargs: Any
        ) -> list[TileRequest]:
            del args, kwargs
            return [
                TileRequest(
                    url="http://example.com/wcs?tile=left",
                    params={},
                    output_format=Format.GEOTIFF,
                    crs=CRS.EPSG_27700,
                    bbox=left_bbox,
                    width=4,
                    height=4,
                ),
                TileRequest(
                    url="http://example.com/wcs?tile=right",
                    params={},
                    output_format=Format.GEOTIFF,
                    crs=CRS.EPSG_27700,
                    bbox=right_bbox,
                    width=4,
                    height=4,
                ),
            ]

    payloads = {
        "http://example.com/wcs?tile=left": left_bytes,
        "http://example.com/wcs?tile=right": right_bytes,
    }

    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: TwoTileWCS())
    monkeypatch.setattr(
        array_module,
        "fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=payloads[request.url],
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        ),
    )

    result = array_module.create_array(
        service_url="http://example.com/wcs",
        bbox=mosaic_bbox,
        bbox_crs=CRS.EPSG_27700,
        chunk_size=(4, 4),
        grid_shape=(1, 2),
        output_format=Format.GEOTIFF,
        compute=True,
        show_progress=False,
    )

    values = np.asarray(result.values)
    assert values.shape == (4, 8)
    assert np.all(values[:, 0:4] == 1.0)
    assert np.all(values[:, 4:8] == 2.0)
