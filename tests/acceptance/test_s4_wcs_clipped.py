"""S4 — GeoServer-style clipped WCS response alignment (#97 → #95/#94)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import tifffile

from tilearray.decode import decode_tile_bytes, read_geotiff_bytes

from .catalog import scenario_by_id

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s4_wcs_clipped"


def test_s4_fixture_is_smaller_than_requested_grid() -> None:
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    raw = (FIXTURE / "clipped_response.tif").read_bytes()
    array = tifffile.imread(FIXTURE / "clipped_response.tif")
    assert array.ndim == 3
    assert array.shape[0] == expected["requested"]["band_count"]
    assert array.shape[1] == expected["server_response"]["height"]
    assert array.shape[2] == expected["server_response"]["width"]
    assert array.shape[-2:] != (
        expected["requested"]["height"],
        expected["requested"]["width"],
    )

    decoded = decode_tile_bytes(
        raw,
        reader=read_geotiff_bytes,
        band_policy="preserve",
    )
    # Current decode returns the server raster as-is (no request-grid pad).
    assert decoded.shape[-2:] == (
        expected["server_response"]["height"],
        expected["server_response"]["width"],
    )


def test_s4_baseline_classified_fail() -> None:
    assert scenario_by_id("S4")["baseline"] == "fail"


@pytest.mark.xfail(
    strict=True,
    reason="Clipped WCS response must pad/align onto request grid — #95",
)
def test_s4_desired_output_keeps_request_grid_with_nodata_outside() -> None:
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    raw = (FIXTURE / "clipped_response.tif").read_bytes()
    decoded = decode_tile_bytes(
        raw,
        reader=read_geotiff_bytes,
        band_policy="preserve",
    )
    req_h = expected["requested"]["height"]
    req_w = expected["requested"]["width"]
    offset_x = expected["server_response"]["offset_x"]
    offset_y = expected["server_response"]["offset_y"]

    # Desired contract (#95): assemble onto the request grid without stretching.
    assert decoded.shape[-2:] == (req_h, req_w)
    assert np.isnan(decoded[..., 0, 0])
    assert not np.isnan(decoded[..., offset_y, offset_x])
