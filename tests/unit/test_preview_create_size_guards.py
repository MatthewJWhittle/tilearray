"""Preview estimates huge tile counts without materialising; Create refuses them."""

from __future__ import annotations

import pytest

from tilearray import WMSConfig, XYZConfig, create_array, preview_array
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


def test_wms_preview_estimates_huge_resolution_plan_without_materialising() -> None:
    """WMS/WCS resolution plans use BaseService O(1) estimate (#161 Bugbot)."""
    cfg = WMSConfig.from_url("https://example.com/wms", layers="a")
    # ~100 km box at 1 m resolution → ~1e10 pixels / 256² ≈ huge tile count.
    pv = preview_array(
        cfg,
        (400000, 100000, 500000, 200000),
        bbox_crs="EPSG:27700",
        resolution=(1, 1),
        max_tile_requests=10_000,
    )
    assert pv.request_count > 100_000
    assert len(pv.tile_requests) == 0
