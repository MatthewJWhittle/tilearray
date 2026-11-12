"""World-edge AOIs: inclusive exact domain edges; refuse true out-of-domain."""

from __future__ import annotations

import pytest

from tests.unit.hands_on_examples import EPSG4326_WORLD_CAPS
from tilearray import XYZConfig, create_array, inspect_capabilities
from tilearray.errors import ValidationError
from tilearray.service.wmts import WMTSParser, WMTSService
from tilearray.service.xyz import XYZService, _tile_range_for_bbox
from tilearray.types import CRS, BoundingBox, ServiceTypeEnum

pytestmark = pytest.mark.unit

_WORLD_IN_DOMAIN = BoundingBox(
    min_x=-180.0,
    min_y=-85.0,
    max_x=180.0,
    max_y=85.0,
    crs=CRS.EPSG_4326,
)


def test_xyz_exact_180_world_bbox_plans_in_domain_tiles() -> None:
    """lon=180 is the exclusive slippy edge — last column is 2**z - 1, not refused."""

    zoom = 2
    scale = 2**zoom
    x_min, y_min, x_max, y_max = _tile_range_for_bbox(_WORLD_IN_DOMAIN, zoom)
    assert 0 <= x_min <= x_max < scale
    assert 0 <= y_min <= y_max < scale
    assert (x_min, x_max) == (0, scale - 1)

    service = XYZService("https://tiles.example.com/{z}/{x}/{y}.png", zoom=zoom)
    count = service.estimate_tile_count(_WORLD_IN_DOMAIN, (256, 256))
    tiles = list(service.plan_tiles(_WORLD_IN_DOMAIN, (256, 256)))
    assert count == len(tiles) == (x_max - x_min + 1) * (y_max - y_min + 1)
    assert all(0 <= t.tile_x < scale and 0 <= t.tile_y < scale for t in tiles)


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


def test_wmts_exact_180_world_bbox_plans_last_matrix_column() -> None:
    """Geographic WMTS matrix: max_x=180 selects col matrix_width-1 (half-open end)."""

    caps = WMTSParser().parse_get_capabilities(EPSG4326_WORLD_CAPS.read_text())
    service = WMTSService(
        "https://example.com/wmts",
        layer="world",
        tile_matrix_set="EPSG4326",
        tile_matrix=0,
        crs=CRS.EPSG_4326,
    )
    service.apply_capabilities(caps)

    # Level-0 EPSG:4326 world matrix is 2×1 covering [-180,180]×[-90,90].
    bbox = BoundingBox(
        min_x=-180.0, min_y=-90.0, max_x=180.0, max_y=90.0, crs=CRS.EPSG_4326
    )
    tiles = list(service.plan_tiles(bbox, (256, 256)))
    assert len(tiles) == 2
    assert {t.tile_x for t in tiles} == {0, 1}
    assert all(t.tile_y == 0 for t in tiles)

    estimated = service.estimate_tile_count(bbox, (256, 256))
    assert estimated == 2


def test_wmts_estimate_tile_count_matches_plan_slippy_fallback() -> None:
    service = WMTSService(
        "https://example.com/wmts",
        layer="osm",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=3,
        crs=CRS.EPSG_3857,
    )
    bbox = BoundingBox(
        min_x=-10.0, min_y=50.0, max_x=-5.0, max_y=55.0, crs=CRS.EPSG_4326
    )
    estimated = service.estimate_tile_count(bbox, (256, 256))
    planned = list(service.plan_tiles(bbox, (256, 256)))
    assert estimated == len(planned)
    assert estimated is not None and estimated > 0
