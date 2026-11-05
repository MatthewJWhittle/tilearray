#!/usr/bin/env python3
"""Minimal offline docs-journey smoke (Mode E stand-in).

Imports public entry points and runs one opaque-URL inspect + one palette
plot-contract path under respx — not full Jupyter. Safe for PR / nightly
without network. Exit 0 on success.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import respx
from PIL import Image

from tilearray import create_array, inspect_endpoint, plan_array
from tilearray.decode import read_image_bytes
from tilearray.types import CRS, Format

ROOT = Path(__file__).resolve().parents[1]


def _wmts_caps() -> str:
    return (
        ROOT / "tests" / "data" / "inspect_fixtures" / "wmts_get_capabilities.xml"
    ).read_text(encoding="utf-8")


def _palette_png() -> bytes:
    image = Image.new("P", (4, 4))
    palette = [0] * 768
    palette[0:3] = [10, 20, 30]
    image.putpalette(palette)
    buf = BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    opaque = "https://cache.example.org/v1/service"
    with respx.mock:
        respx.get(opaque).mock(return_value=httpx.Response(200, text=_wmts_caps()))
        inspection = inspect_endpoint(opaque)
        config = inspection.build_config(
            layer="blue_marble",
            tile_matrix_set="GoogleMapsCompatible",
            target_resolution=100_000.0,
        )
        plan = plan_array(
            config,
            bbox=(-10.0, -10.0, 10.0, 10.0),
            bbox_crs=CRS.EPSG_4326,
        )
        assert plan.request_count >= 1

    # Palette plot-contract via decode (create_array path is locked in pytest).
    rgb = read_image_bytes(_palette_png(), expand_palette=True)
    assert rgb.ndim == 3 and rgb.shape[-1] >= 3
    assert rgb.dtype == np.uint8 or float(np.max(rgb)) <= 1.0

    # Import surface used by quickstart / guides
    assert callable(create_array)
    assert Format.PNG is not None
    print("docs_journey_smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
