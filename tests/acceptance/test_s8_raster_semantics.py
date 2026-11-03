"""S8 — raster/image semantics (picture vs numeric) (#97 → #94)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from tilearray.decode import (
    apply_band_policy,
    decode_tile_bytes,
    read_geotiff_bytes,
    read_image_bytes,
)

from .catalog import scenario_by_id

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s8_raster_semantics"


def test_s8_baseline_classified_pass() -> None:
    assert scenario_by_id("S8")["baseline"] == "pass"


def test_s8_grayscale_rgb_rgba_known_values() -> None:
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))

    gray = decode_tile_bytes(
        (FIXTURE / "grayscale.png").read_bytes(),
        reader=read_image_bytes,
        band_policy="preserve",
    )
    assert gray.shape == tuple(expected["grayscale"]["shape"])
    assert float(gray.mean()) == pytest.approx(expected["grayscale"]["mean"])

    rgb = decode_tile_bytes(
        (FIXTURE / "rgb.png").read_bytes(),
        reader=read_image_bytes,
        band_policy="preserve",
    )
    assert rgb.shape == tuple(expected["rgb"]["shape"])
    assert [float(rgb[..., i].mean()) for i in range(3)] == pytest.approx(
        expected["rgb"]["bands"]
    )

    rgba = decode_tile_bytes(
        (FIXTURE / "rgba.png").read_bytes(),
        reader=read_image_bytes,
        band_policy="preserve",
    )
    assert rgba.shape == tuple(expected["rgba"]["shape"])
    assert float(rgba[0, 0, 3]) == pytest.approx(0.0)


def test_s8_multiband_tiff_preserves_bands_and_nan() -> None:
    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    decoded = decode_tile_bytes(
        (FIXTURE / "multiband_nodata.tif").read_bytes(),
        reader=read_geotiff_bytes,
        band_policy="preserve",
    )
    # tifffile path may return (bands, y, x) or (y, x, bands); normalise.
    if decoded.ndim == 3 and decoded.shape[0] == 3:
        assert np.isnan(decoded[0, 0, 0])
        first_band = apply_band_policy(decoded, "first_band")
        assert first_band.shape == (8, 8)
    else:
        assert decoded.shape[-1] == 3 or decoded.ndim == 2
    assert list(expected["multiband_nodata"]["nan_at"]) == [0, 0, 0]


def test_s8_palette_png_is_rendered_picture_not_coverage_values() -> None:
    """Palette PNGs are display imagery; do not treat indices as analysis values."""

    expected = json.loads((FIXTURE / "expected.json").read_text(encoding="utf-8"))
    img = Image.open(FIXTURE / "palette.png")
    assert img.mode == expected["palette"]["mode"]
    indices = np.array(img.getdata(), dtype=np.uint8).reshape(img.size[1], img.size[0])
    assert (
        sorted(int(v) for v in np.unique(indices))
        == expected["palette"]["unique_indices"]
    )

    # Current decode keeps palette indices (2-D). That is still display-sidecar
    # imagery, not a WCS coverage — #94 owns richer palette→RGB policy.
    decoded = decode_tile_bytes(
        (FIXTURE / "palette.png").read_bytes(),
        reader=read_image_bytes,
        band_policy="preserve",
    )
    assert decoded.ndim == 2
    assert (
        sorted(int(v) for v in np.unique(decoded))
        == expected["palette"]["unique_indices"]
    )
