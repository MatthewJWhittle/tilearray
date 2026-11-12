"""World-edge AOIs refuse invalid tile plans (no out-of-domain indices, no silent clamp)."""

from __future__ import annotations

import pytest

from tests.unit.hands_on_examples import EPSG4326_WORLD_CAPS
from tilearray import XYZConfig, create_array, inspect_capabilities
from tilearray.errors import ValidationError
from tilearray.types import ServiceTypeEnum

pytestmark = pytest.mark.unit


def test_xyz_antimeridian_aoi_is_refused() -> None:
    cfg = XYZConfig.from_url("https://tiles.example.com/{z}/{x}/{y}.png", zoom=6)
    with pytest.raises(ValidationError):
        create_array(cfg, (175, -20, 185, -15), bbox_crs="EPSG:4326")


def test_xyz_polar_aoi_is_refused() -> None:
    cfg = XYZConfig.from_url("https://tiles.example.com/{z}/{x}/{y}.png", zoom=6)
    with pytest.raises(ValidationError):
        create_array(cfg, (-10, 80, 10, 89.9), bbox_crs="EPSG:4326")


def test_wmts_past_180_aoi_is_refused() -> None:
    caps = EPSG4326_WORLD_CAPS.read_text()
    ins = inspect_capabilities(
        caps,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(layer="world", tile_matrix_set="EPSG4326", tile_matrix=0)
    with pytest.raises(ValidationError):
        create_array(cfg, (178, -20, 182, -15), bbox_crs="EPSG:4326")
