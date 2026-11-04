"""Raster fidelity: bands, dtype, palette, nodata (#94)."""

from __future__ import annotations

from io import BytesIO
from typing import Any

import numpy as np
import pytest
import tifffile
from _pytest.monkeypatch import MonkeyPatch
from PIL import Image

import tilearray._planning as planning_module
import tilearray.array as array_module
import tilearray.decode as decode_module
from tilearray.decode import (
    band_count_from_array,
    decode_tile_bytes,
    default_band_count_for_format,
    identity_unwrapper,
    make_image_reader,
    read_geotiff_bytes,
    read_image_bytes,
)
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)


def _write_tiff(array: np.ndarray, **kwargs: object) -> bytes:
    buffer = BytesIO()
    tifffile.imwrite(buffer, array, **kwargs)
    return buffer.getvalue()


@pytest.mark.unit
def test_default_geotiff_band_policy_is_preserve() -> None:
    assert decode_module.DEFAULT_BAND_POLICIES[Format.GEOTIFF] == "preserve"
    assert default_band_count_for_format(Format.GEOTIFF) is None
    assert default_band_count_for_format(Format.GEOTIFF, band_policy="first_band") == 1


@pytest.mark.unit
def test_multiband_geotiff_preserve_keeps_all_bands() -> None:
    stack = np.stack(
        [
            np.full((8, 8), 1, dtype=np.uint16),
            np.full((8, 8), 2, dtype=np.uint16),
            np.full((8, 8), 3, dtype=np.uint16),
            np.full((8, 8), 4, dtype=np.uint16),
        ],
        axis=0,
    )
    raw = _write_tiff(stack)
    decoded = decode_tile_bytes(
        raw,
        reader=read_geotiff_bytes,
        band_policy="preserve",
        unwrapper=identity_unwrapper,
    )
    assert decoded.shape == (8, 8, 4)
    assert band_count_from_array(decoded) == 4
    assert float(decoded[..., 3].mean()) == pytest.approx(4.0)


@pytest.mark.unit
def test_multiband_geotiff_first_band_is_explicit() -> None:
    stack = np.stack(
        [np.full((8, 8), 10.0), np.full((8, 8), 20.0)],
        axis=-1,
    ).astype(np.float32)
    raw = _write_tiff(stack)
    decoded = decode_tile_bytes(
        raw,
        reader=read_geotiff_bytes,
        band_policy="first_band",
        unwrapper=identity_unwrapper,
    )
    assert decoded.shape == (8, 8)
    assert float(decoded.mean()) == pytest.approx(10.0)


@pytest.mark.unit
def test_integer_geotiff_nodata_maps_to_nan_float32() -> None:
    elev = np.full((6, 6), 42, dtype=np.int16)
    elev[0, 0] = -9999
    raw = _write_tiff(
        elev,
        extratags=[(42113, "s", 1, "-9999", True)],
    )
    decoded = read_geotiff_bytes(raw)
    assert decoded.dtype == np.float32
    assert np.isnan(decoded[0, 0])
    assert float(np.nanmean(decoded)) == pytest.approx(42.0)


@pytest.mark.unit
def test_palette_png_stays_indices_by_default() -> None:
    image = Image.new("P", (16, 16))
    image.putpalette([i % 256 for i in range(768)])
    pixels = image.load()
    assert pixels is not None
    for y in range(16):
        for x in range(16):
            pixels[x, y] = (x + y) % 256
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()

    decoded = read_image_bytes(raw, expand_palette=False)
    assert decoded.ndim == 2
    assert decoded.shape == (16, 16)
    assert decoded.dtype == np.uint8
    assert int(decoded[1, 2]) == 3


@pytest.mark.unit
def test_palette_png_expands_to_rgb_when_requested() -> None:
    image = Image.new("P", (8, 8))
    palette = [0] * 768
    palette[0:3] = [10, 20, 30]
    palette[3:6] = [40, 50, 60]
    image.putpalette(palette)
    pixels = image.load()
    assert pixels is not None
    for y in range(8):
        for x in range(8):
            pixels[x, y] = 0 if x < 4 else 1
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()

    expanded = read_image_bytes(raw, expand_palette=True)
    assert expanded.shape == (8, 8, 3)
    assert list(expanded[0, 0]) == [10, 20, 30]
    assert list(expanded[0, 7]) == [40, 50, 60]


@pytest.mark.unit
def test_image_decode_preserves_uint8_without_float_cast() -> None:
    source = np.zeros((4, 4, 3), dtype=np.uint8)
    source[..., 0] = 255
    buffer = BytesIO()
    Image.fromarray(source, mode="RGB").save(buffer, format="PNG")
    raw = buffer.getvalue()
    decoded = decode_tile_bytes(
        raw,
        reader=make_image_reader(expand_palette=False),
        band_policy="preserve",
        unwrapper=identity_unwrapper,
        cast_float32=False,
    )
    assert decoded.dtype == np.uint8
    assert decoded.shape == (4, 4, 3)


@pytest.mark.unit
def test_create_array_multiband_geotiff_mosaic(
    monkeypatch: MonkeyPatch,
) -> None:
    bands = np.stack(
        [
            np.full((4, 4), 11, dtype=np.uint8),
            np.full((4, 4), 22, dtype=np.uint8),
            np.full((4, 4), 33, dtype=np.uint8),
        ],
        axis=-1,
    )
    raw = _write_tiff(bands, photometric="rgb")
    bbox = BoundingBox(min_x=0, min_y=0, max_x=4, max_y=4, crs=CRS.EPSG_3857)

    class MultiBandWCS:
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
                    crs=CRS.EPSG_3857,
                    bbox=bbox,
                    width=4,
                    height=4,
                )
            ]

    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: MultiBandWCS())
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
        crs=CRS.EPSG_3857,
        chunk_size=(4, 4),
        output_format=Format.GEOTIFF,
        compute=True,
        show_progress=False,
    )

    assert result.dims == ("y", "x", "band")
    assert result.shape == (4, 4, 3)
    assert result.attrs["band_policy"] == "preserve"
    assert result.attrs["dtype_policy"] == "float32_nodata_nan"
    assert float(result.values[..., 0].mean()) == pytest.approx(11.0)
    assert float(result.values[..., 2].mean()) == pytest.approx(33.0)


@pytest.mark.unit
def test_create_array_expand_palette_png(
    monkeypatch: MonkeyPatch,
) -> None:
    image = Image.new("P", (4, 4))
    palette = [0] * 768
    palette[0:3] = [1, 2, 3]
    image.putpalette(palette)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    raw = buffer.getvalue()
    bbox = BoundingBox(min_x=0, min_y=0, max_x=4, max_y=4, crs=CRS.EPSG_3857)

    class PaletteXYZ:
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.PNG

        def generate_tile_requests(
            self, *args: Any, **kwargs: Any
        ) -> list[TileRequest]:
            del args, kwargs
            return [
                TileRequest(
                    url="http://example.com/0/0/0.png",
                    params={},
                    output_format=Format.PNG,
                    crs=CRS.EPSG_3857,
                    bbox=bbox,
                    width=4,
                    height=4,
                )
            ]

    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: PaletteXYZ())
    monkeypatch.setattr(
        array_module,
        "fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=raw,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        ),
    )

    result = array_module.create_array(
        service_url="http://example.com/{z}/{x}/{y}.png",
        bbox=bbox,
        crs=CRS.EPSG_3857,
        chunk_size=(4, 4),
        output_format=Format.PNG,
        expand_palette=True,
        compute=True,
        show_progress=False,
    )

    assert result.shape == (4, 4, 3)
    assert result.attrs["palette_mode"] == "rgb"
    assert result.attrs["band_policy"] == "preserve"
    assert float(result.values[..., 0].mean()) == pytest.approx(1.0)
