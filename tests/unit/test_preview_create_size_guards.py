"""Preview estimates huge tile counts without materialising; Create refuses them."""

from __future__ import annotations

import pytest

from tilearray import XYZConfig, create_array, preview_array
from tilearray.errors import ValidationError

pytestmark = pytest.mark.unit

# Great Britain-ish AOI at XYZ z14 — order of 1e5 tiles.
_GB_Z14_BBOX = (-6, 50, 2, 56)


def test_preview_estimates_huge_tile_count_without_materialising_tiles() -> None:
    cfg = XYZConfig.from_url("https://tiles.example.com/{z}/{x}/{y}.png", zoom=14)
    pv = preview_array(cfg, _GB_Z14_BBOX, bbox_crs="EPSG:4326")
    assert pv.request_count > 100_000
    # Estimate path: do not assemble every TileRequest for the preflight count.
    assert len(pv.tile_requests) == 0


def test_create_refuses_huge_tile_plan() -> None:
    cfg = XYZConfig.from_url("https://tiles.example.com/{z}/{x}/{y}.png", zoom=14)
    with pytest.raises(ValidationError, match=r"(?i)request|tile|limit|too many"):
        create_array(cfg, _GB_Z14_BBOX, bbox_crs="EPSG:4326")
