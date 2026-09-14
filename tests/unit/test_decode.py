"""Unit tests for the shared tile decode pipeline."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tilearray.decode import (
    apply_band_policy,
    band_count_from_array,
    decode_tile_bytes,
    default_band_count_for_format,
    identity_unwrapper,
    read_geotiff_bytes,
    read_image_bytes,
    unwrap_multipart,
)
from tilearray.types import Format, TileResponse

GEOTIFF_FIXTURE = (
    Path(__file__).resolve().parents[1] / "data" / "wcs_tiles" / "ea_lidar_64x64.tif"
)


@pytest.mark.unit
def test_apply_band_policy_preserves_rgb() -> None:
    rgb = np.zeros((8, 8, 3), dtype=np.float32)
    rgb[..., 0] = 200
    rgb[..., 1] = 100
    rgb[..., 2] = 50

    preserved = apply_band_policy(rgb, "preserve")

    assert preserved.shape == (8, 8, 3)
    assert float(preserved[..., 1].mean()) == pytest.approx(100.0)


@pytest.mark.unit
def test_apply_band_policy_first_band_for_geotiff_stack() -> None:
    stack = np.stack(
        [np.full((8, 8), 10.0), np.full((8, 8), 20.0)],
        axis=0,
    )

    reduced = apply_band_policy(stack, "first_band")

    assert reduced.shape == (8, 8)
    assert float(reduced.mean()) == pytest.approx(10.0)


@pytest.mark.unit
def test_decode_tile_bytes_preserves_jpeg_rgb_bands() -> None:
    source = np.zeros((32, 32, 3), dtype=np.uint8)
    source[..., 0] = 180
    source[..., 1] = 90
    source[..., 2] = 30
    with BytesIO() as buffer:
        Image.fromarray(source, mode="RGB").save(buffer, format="JPEG")
        raw = buffer.getvalue()

    decoded = decode_tile_bytes(
        raw,
        reader=read_image_bytes,
        band_policy="preserve",
        unwrapper=identity_unwrapper,
    )

    assert decoded.shape == (32, 32, 3)
    assert band_count_from_array(decoded) == 3


@pytest.mark.unit
def test_default_band_count_for_format() -> None:
    assert default_band_count_for_format(Format.GEOTIFF) == 1
    assert default_band_count_for_format(Format.JPEG) is None
    assert default_band_count_for_format(Format.PNG) is None


@pytest.mark.unit
def test_unwrap_multipart_passes_through_non_multipart() -> None:
    payload = b"raw-bytes"
    response = TileResponse(
        data=payload,
        content_type="application/octet-stream",
        status_code=200,
        headers={},
        url="https://example.com/wcs",
        success=True,
    )

    assert unwrap_multipart(payload, response) == payload


@pytest.mark.unit
def test_unwrap_multipart_extracts_geotiff_part() -> None:
    tiff_bytes = GEOTIFF_FIXTURE.read_bytes()
    multipart_body = (
        b"--wcs\r\n"
        b"Content-Type: text/xml\r\n"
        b"Content-ID: GML-Part\r\n\r\n"
        b"<gmlcov:RectifiedGridCoverage/>\r\n"
        b"--wcs\r\n"
        b"Content-Type: image/tiff\r\n"
        b"Content-ID: coverage.tif\r\n"
        b"Content-Transfer-Encoding: binary\r\n\r\n" + tiff_bytes + b"\r\n--wcs--\r\n"
    )
    response = TileResponse(
        data=multipart_body,
        content_type='multipart/related; boundary="wcs";type="text/xml"',
        status_code=200,
        headers={},
        url="https://example.com/wcs",
        success=True,
    )

    unwrapped = unwrap_multipart(multipart_body, response)
    assert unwrapped[:2] in (b"II", b"MM")
    decoded = decode_tile_bytes(
        multipart_body,
        reader=read_geotiff_bytes,
        band_policy="first_band",
        unwrapper=unwrap_multipart,
        response=response,
    )
    assert decoded.shape == (64, 64)


@pytest.mark.unit
def test_unwrap_multipart_sniffs_body_when_content_type_is_image_tiff() -> None:
    """Disk cache may restore image/tiff while bytes remain multipart/related."""

    tiff_bytes = GEOTIFF_FIXTURE.read_bytes()
    multipart_body = (
        b"--wcs\r\n"
        b"Content-Type: text/xml\r\n"
        b"Content-ID: GML-Part\r\n\r\n"
        b"<gmlcov:RectifiedGridCoverage/>\r\n"
        b"--wcs\r\n"
        b"Content-Type: image/tiff\r\n"
        b"Content-ID: coverage.tif\r\n"
        b"Content-Transfer-Encoding: binary\r\n\r\n" + tiff_bytes + b"\r\n--wcs--\r\n"
    )
    misleading_response = TileResponse(
        data=multipart_body,
        content_type="image/tiff",
        status_code=200,
        headers={},
        url="https://example.com/wcs",
        success=True,
    )

    unwrapped = unwrap_multipart(multipart_body, misleading_response)
    assert unwrapped[:2] in (b"II", b"MM")
    decoded = decode_tile_bytes(
        multipart_body,
        reader=read_geotiff_bytes,
        band_policy="first_band",
        unwrapper=unwrap_multipart,
        response=misleading_response,
    )
    assert decoded.shape == (64, 64)
