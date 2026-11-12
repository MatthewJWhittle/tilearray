"""Thin PNG decoded tiles keep (height, width[, band]) layout."""

from __future__ import annotations

import pytest

from tests.unit.hands_on_examples import png_bytes
from tilearray.decode import decoder_for_format
from tilearray.types import CRS, BoundingBox, Format, TileRequest, TileResponse

pytestmark = pytest.mark.unit


def _decode_png(raw: bytes, *, width: int, height: int):
    resp = TileResponse(
        data=raw,
        content_type="image/png",
        status_code=200,
        headers={},
        url="",
        success=True,
    )
    req = TileRequest(
        url="https://example.com/wms",
        params={},
        output_format=Format.PNG,
        crs=CRS.EPSG_4326,
        width=width,
        height=height,
        bbox=BoundingBox(
            min_x=0,
            min_y=0,
            max_x=max(width / 256, 0.01),
            max_y=max(height / 256, 0.01),
            crs=CRS.EPSG_4326,
        ),
    )
    return decoder_for_format(Format.PNG)(resp, req)


@pytest.mark.parametrize("height", [1, 2, 3, 4])
@pytest.mark.parametrize("bands", [3, 4])
def test_thin_rgb_png_keeps_height_width_bands(height: int, bands: int) -> None:
    raw = png_bytes(height, 256, bands)
    arr = _decode_png(raw, width=256, height=height)
    assert arr.shape == (height, 256, bands)


@pytest.mark.parametrize("height", [1, 2, 3, 4])
def test_thin_greyscale_png_keeps_height_width(height: int) -> None:
    raw = png_bytes(height, 256, 1)
    arr = _decode_png(raw, width=256, height=height)
    assert arr.shape == (height, 256)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_narrow_rgb_png_keeps_height_width_bands(width: int) -> None:
    raw = png_bytes(256, width, 3)
    arr = _decode_png(raw, width=width, height=256)
    assert arr.shape == (256, width, 3)
