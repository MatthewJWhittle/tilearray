"""Unit tests for the shared tile decode pipeline."""

from __future__ import annotations

import importlib.util
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
import tifffile
from PIL import Image

from tilearray.decode import (
    apply_band_policy,
    band_count_from_array,
    decode_tile_bytes,
    default_band_count_for_format,
    identity_unwrapper,
    read_geotiff_bytes,
    read_image_bytes,
    sniff_effective_content_type,
    unwrap_multipart,
)
from tilearray.types import Format, TileResponse

GEOTIFF_FIXTURE = (
    Path(__file__).resolve().parents[1] / "data" / "wcs_tiles" / "ea_lidar_64x64.tif"
)


def _write_tiff_bytes(array: np.ndarray, **kwargs: object) -> bytes:
    buffer = BytesIO()
    tifffile.imwrite(buffer, array, **kwargs)
    return buffer.getvalue()


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


@pytest.mark.unit
def test_sniff_effective_content_type_prefers_multipart_bytes_over_tiff_header() -> (
    None
):
    tiff_bytes = GEOTIFF_FIXTURE.read_bytes()
    multipart_body = (
        b"--wcs\r\nContent-Type: image/tiff\r\n\r\n" + tiff_bytes + b"\r\n--wcs--\r\n"
    )

    sniffed = sniff_effective_content_type("image/tiff", multipart_body)

    assert sniffed.startswith("multipart/related")
    assert "wcs" in sniffed


@pytest.mark.unit
def test_sniff_effective_content_type_keeps_raw_tiff() -> None:
    tiff_bytes = GEOTIFF_FIXTURE.read_bytes()

    assert sniff_effective_content_type("image/tiff", tiff_bytes) == "image/tiff"
    assert sniff_effective_content_type("", tiff_bytes) == "image/tiff"


@pytest.mark.unit
def test_read_geotiff_bytes_single_band_elevation_fixture() -> None:
    """Documented single-band WCS GeoTIFF path stays numerically honest."""

    decoded = read_geotiff_bytes(GEOTIFF_FIXTURE.read_bytes())

    assert decoded.shape == (64, 64)
    assert decoded.dtype == np.float32
    assert np.isfinite(decoded).all()
    assert 100 < float(np.nanmean(decoded)) < 300


@pytest.mark.unit
def test_read_geotiff_bytes_maps_gdal_nodata_and_sentinels_to_nan() -> None:
    elev = np.full((8, 8), 42.0, dtype=np.float32)
    elev[0, 0] = -3.4028235e38
    elev[1, 1] = np.inf
    raw = _write_tiff_bytes(
        elev,
        extratags=[(42113, "s", 1, "-3.4028234663852886E38", True)],
    )

    decoded = read_geotiff_bytes(raw)

    assert decoded.shape == (8, 8)
    assert np.isnan(decoded[0, 0])
    assert np.isnan(decoded[1, 1])
    assert float(np.nanmean(decoded)) == pytest.approx(42.0)


@pytest.mark.unit
def test_decode_rgb_geotiff_preserve_keeps_bands_uncorrupted() -> None:
    """RGB GeoTIFF under preserve policy must not scramble channel values."""

    rgb = np.zeros((16, 16, 3), dtype=np.uint8)
    rgb[..., 0] = 10
    rgb[..., 1] = 20
    rgb[..., 2] = 30
    raw = _write_tiff_bytes(rgb, photometric="rgb")

    decoded = decode_tile_bytes(
        raw,
        reader=read_geotiff_bytes,
        band_policy="preserve",
        unwrapper=identity_unwrapper,
    )

    assert decoded.shape == (16, 16, 3)
    assert band_count_from_array(decoded) == 3
    assert float(decoded[..., 0].mean()) == pytest.approx(10.0)
    assert float(decoded[..., 1].mean()) == pytest.approx(20.0)
    assert float(decoded[..., 2].mean()) == pytest.approx(30.0)


@pytest.mark.unit
def test_decode_rgb_geotiff_first_band_policy_keeps_band0() -> None:
    rgb = np.zeros((16, 16, 3), dtype=np.uint8)
    rgb[..., 0] = 11
    rgb[..., 1] = 22
    rgb[..., 2] = 33
    raw = _write_tiff_bytes(rgb, photometric="rgb")

    decoded = decode_tile_bytes(
        raw,
        reader=read_geotiff_bytes,
        band_policy="first_band",
        unwrapper=identity_unwrapper,
    )

    assert decoded.shape == (16, 16)
    assert float(decoded.mean()) == pytest.approx(11.0)


@pytest.mark.unit
def test_runtime_does_not_require_geotiff_zarr_pin_cage() -> None:
    """Clean installs must not need the geotiff → zarr/numcodecs pin cage."""

    assert importlib.util.find_spec("tifffile") is not None
    assert importlib.util.find_spec("geotiff") is None
    assert importlib.util.find_spec("zarr") is None
    assert importlib.util.find_spec("numcodecs") is None
