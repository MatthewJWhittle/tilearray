"""S7 — SoilGrids-style max width/height assemble (#97 → #61/#88/#50)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from tilearray.service.base import TileGeometry
from tilearray.service.wcs import WCSService
from tilearray.types import CRS, BoundingBox, Format

from .catalog import scenario_by_id

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s7_soilgrids_wcs"


@respx.mock
def test_s7_chunked_plan_stays_within_fake_max_dims_without_subpixel() -> None:
    assert scenario_by_id("S7")["baseline"] == "pass"
    policy = json.loads((FIXTURE / "policy.json").read_text(encoding="utf-8"))
    max_w = int(policy["max_width"])
    max_h = int(policy["max_height"])

    service = WCSService(
        "https://example.invalid/soilgrids/wcs",
        coverage_id="clay",
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_4326,
    )
    bbox = BoundingBox.from_tuple(
        tuple(policy["aoi_bbox_epsg4326"]),
        crs=CRS.EPSG_4326,
    )
    resolution = tuple(policy["resolution_deg"])
    tiles = list(
        service.plan_tiles(
            bbox,
            tuple(policy["chunk_size"]),
            resolution=resolution,
        )
    )
    assert tiles, "expected multiple sub-requests under the fake max dims"
    for tile in tiles:
        assert tile.width <= max_w
        assert tile.height <= max_h
        # #50 regression: no subpixel leftover dimensions
        assert tile.width >= 1
        assert tile.height >= 1
        assert float(tile.width).is_integer() or isinstance(tile.width, int)
        assert isinstance(tile.width, int)
        assert isinstance(tile.height, int)

    # Fake server rejects oversized single requests; accepts chunked ones.
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        width = int(params.get("width", "0"))
        height = int(params.get("height", "0"))
        if width > max_w or height > max_h:
            return httpx.Response(400, text="max width/height exceeded")
        return httpx.Response(
            200, content=b"II*\x00", headers={"content-type": "image/tiff"}
        )

    route = respx.route(host="example.invalid").mock(side_effect=handler)
    for tile in tiles:
        built = service.build_tile_request(
            TileGeometry(
                bbox=tile.bbox,
                width=tile.width,
                height=tile.height,
                crs=CRS.EPSG_4326,
            )
        )
        response = httpx.get(built.url, params=built.params)
        assert response.status_code == 200
    assert route.call_count == len(tiles)
