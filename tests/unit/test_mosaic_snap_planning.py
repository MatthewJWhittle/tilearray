"""Mosaic resolution snap and tile planning keep requested pixel counts."""

from __future__ import annotations

import pytest

from tilearray.preview import preview_array
from tilearray.service.base import BaseService
from tilearray.tiles import resolution_axis_tile_sizes, snap_bbox_outward
from tilearray.types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest

pytestmark = pytest.mark.unit


class _DummyService(BaseService):
    service_type = ServiceTypeEnum.WCS
    output_format = Format.GEOTIFF

    def build_tile_request(self, tile, **options):  # type: ignore[no-untyped-def]
        return TileRequest(
            url=self.base_url,
            params={},
            bbox=tile.bbox,
            width=tile.width,
            height=tile.height,
            output_format=Format.GEOTIFF,
        )


def test_unaligned_integer_span_plans_exact_pixel_count_without_sliver() -> None:
    """5120 m at 10 m, half-pixel offset → 512×512 in four 256-px tiles."""
    bbox = BoundingBox(
        min_x=-10297015.0,
        min_y=4364542.0,
        max_x=-10291895.0,
        max_y=4369662.0,
        crs=CRS.EPSG_3857,
    )
    tiles = list(
        _DummyService("https://example.com").plan_tiles(
            bbox, (256, 256), resolution=(10.0, 10.0)
        )
    )

    assert len(tiles) == 4
    assert {(t.width, t.height) for t in tiles} == {(256, 256)}
    mosaic_w = max(t.bbox.max_x for t in tiles) - min(t.bbox.min_x for t in tiles)
    mosaic_h = max(t.bbox.max_y for t in tiles) - min(t.bbox.min_y for t in tiles)
    assert mosaic_w == 5120.0
    assert mosaic_h == 5120.0


def test_origin_aligned_integer_span_unchanged() -> None:
    """EA-style 512 m box at 1 m stays 512×512 in four tiles."""
    bbox = BoundingBox(
        min_x=431900.0,
        min_y=382700.0,
        max_x=432412.0,
        max_y=383212.0,
        crs=CRS.EPSG_27700,
    )
    tiles = list(
        _DummyService("https://example.com").plan_tiles(
            bbox, (256, 256), resolution=(1.0, 1.0)
        )
    )

    assert len(tiles) == 4
    assert {(t.width, t.height) for t in tiles} == {(256, 256)}
    snapped = snap_bbox_outward(bbox, (1.0, 1.0))
    assert snapped.min_x == bbox.min_x
    assert snapped.min_y == bbox.min_y
    assert snapped.max_x == bbox.max_x
    assert snapped.max_y == bbox.max_y


def test_non_integer_span_uses_round() -> None:
    """5125 m at 10 m → round to 512 px (documented round rule)."""
    bbox = BoundingBox(
        min_x=0.0,
        min_y=0.0,
        max_x=5125.0,
        max_y=5125.0,
        crs=CRS.EPSG_3857,
    )
    resolution = (10.0, 10.0)
    snapped = snap_bbox_outward(bbox, resolution)
    n_px = int(round((snapped.max_x - snapped.min_x) / resolution[0]))
    n_py = int(round((snapped.max_y - snapped.min_y) / resolution[1]))
    assert n_px == 512
    assert n_py == 512

    tiles = list(
        _DummyService("https://example.com").plan_tiles(
            bbox, (256, 256), resolution=resolution
        )
    )
    assert len(tiles) == 4
    assert {(t.width, t.height) for t in tiles} == {(256, 256)}


def test_plan_tiles_puts_remainder_on_trailing_mosaic_edge() -> None:
    """513 px at chunk 256 → short leftover is south/east only, never leading."""
    bbox = BoundingBox(
        min_x=0.0, min_y=0.0, max_x=5130.0, max_y=5130.0, crs=CRS.EPSG_3857
    )
    tiles = list(
        _DummyService("https://example.com").plan_tiles(
            bbox, (256, 256), resolution=(10.0, 10.0)
        )
    )
    assert resolution_axis_tile_sizes(513, 256) == [256, 256, 1]
    assert len(tiles) == 9
    south = [t for t in tiles if t.bbox.min_y == min(x.bbox.min_y for x in tiles)]
    east = [t for t in tiles if t.bbox.max_x == max(x.bbox.max_x for x in tiles)]
    assert all(t.height == 1 for t in south)
    assert all(t.width == 1 for t in east)
    north = [t for t in tiles if t.bbox.max_y == max(x.bbox.max_y for x in tiles)]
    west = [t for t in tiles if t.bbox.min_x == min(x.bbox.min_x for x in tiles)]
    assert all(t.height == 256 for t in north)
    assert all(t.width == 256 for t in west)


def test_preview_matches_plan_pixel_count_for_unaligned_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _DummyService("https://example.com/wcs")
    monkeypatch.setattr("tilearray._planning.get_service", lambda *a, **k: service)
    bbox = (-10297015.0, 4364542.0, -10291895.0, 4369662.0)
    preview = preview_array(
        "https://example.com/wcs",
        bbox,
        "EPSG:3857",
        service_type=ServiceTypeEnum.WCS,
        chunk_size=(256, 256),
        output_format=Format.GEOTIFF,
        resolution=(10.0, 10.0),
    )
    assert preview.pixel_height == 512
    assert preview.pixel_width == 512
    assert preview.request_count == 4
