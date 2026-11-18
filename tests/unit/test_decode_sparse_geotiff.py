"""Sparse GeoTIFFs: absent strips/tiles (offset 0, byte count 0) decode as nodata (#215).

GDAL writes blocks that are entirely nodata this way (``SPARSE_OK``), and WCS
servers return such tiles at coverage edges.
"""

from __future__ import annotations

import struct
from io import BytesIO
from typing import Any

import numpy as np
import pytest
import tifffile

from tilearray.decode import read_geotiff_bytes

pytestmark = pytest.mark.unit

_FMT = {3: "<H", 4: "<I", 16: "<Q"}


def _sparse_tiff(
    *, empty: set[int] | None, truncate: bool = False, **kwargs: Any
) -> bytes:
    """Write a 32x32 float32 TIFF, then blank the given segments (None = all)."""

    values = np.arange(32 * 32, dtype=np.float32).reshape(32, 32) + 1.0
    buf = BytesIO()
    tifffile.imwrite(buf, values, metadata=None, **kwargs)
    raw = bytearray(buf.getvalue())
    with tifffile.TiffFile(BytesIO(bytes(raw))) as tif:
        page = tif.pages.first
        assert page is not None
        n = len(page.dataoffsets)
        first_data = min(page.dataoffsets)
        names = (
            ("TileOffsets", "TileByteCounts")
            if page.is_tiled
            else (
                "StripOffsets",
                "StripByteCounts",
            )
        )
        for name in names:
            tag = page.tags[name]
            fmt = _FMT[tag.dtype]
            for i in range(n):
                if empty is None or i in empty:
                    struct.pack_into(
                        fmt, raw, tag.valueoffset + i * struct.calcsize(fmt), 0
                    )
    if truncate:
        raw = raw[:first_data]  # GDAL omits the absent blocks' bytes entirely
    return bytes(raw)


def test_all_sparse_single_strip_tiff_decodes_to_all_nan() -> None:
    out = read_geotiff_bytes(_sparse_tiff(empty=None, truncate=True))
    assert out.shape == (32, 32)
    assert out.dtype == np.float32
    assert np.isnan(out).all()


def test_all_sparse_tiled_tiff_decodes_to_all_nan() -> None:
    out = read_geotiff_bytes(_sparse_tiff(empty=None, truncate=True, tile=(16, 16)))
    assert out.shape == (32, 32)
    assert np.isnan(out).all()


def test_partly_sparse_strips_are_nan_only_where_absent() -> None:
    out = read_geotiff_bytes(_sparse_tiff(empty={1}, rowsperstrip=16))
    assert np.isfinite(out[:16]).all()
    assert out[0, 0] == 1.0
    assert np.isnan(out[16:]).all()


def test_partly_sparse_tiles_are_nan_only_where_absent() -> None:
    out = read_geotiff_bytes(_sparse_tiff(empty={1, 2}, tile=(16, 16)))
    assert np.isfinite(out[:16, :16]).all()
    assert np.isnan(out[:16, 16:]).all()
    assert np.isnan(out[16:, :16]).all()
    assert np.isfinite(out[16:, 16:]).all()


def test_dense_tiff_is_unchanged() -> None:
    values = np.arange(32 * 32, dtype=np.float32).reshape(32, 32)
    buf = BytesIO()
    tifffile.imwrite(buf, values, metadata=None, rowsperstrip=16)
    np.testing.assert_array_equal(read_geotiff_bytes(buf.getvalue()), values)
