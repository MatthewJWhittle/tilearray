"""Unit tests for shared tile request composition."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from tilearray.fetch import TileFetcher
from tilearray.service.base import TileGeometry
from tilearray.service.config import XYZConfig
from tilearray.service.requests import compose_tile_request, merge_str_mappings
from tilearray.service.xyz import _tile_bounds
from tilearray.tiles import fetch_tile
from tilearray.types import CRS, Format

OSM_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


@pytest.mark.unit
def test_merge_str_mappings_overlays_options() -> None:
    merged = merge_str_mappings(
        {"User-Agent": "base"},
        {"User-Agent": "override", "X-Test": "1"},
    )

    assert merged == {"User-Agent": "override", "X-Test": "1"}


@pytest.mark.unit
def test_compose_tile_request_wires_config_headers_and_params() -> None:
    tile = TileGeometry(
        bbox=_tile_bounds(1, 1, 10, CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=1,
        tile_y=1,
        zoom=10,
    )
    config = {
        "headers": {"User-Agent": "tilearray-test/1.0"},
        "params": {"layer": "roads"},
    }
    options = {
        "headers": {"X-Peer-Test": "enabled"},
        "params": {"style": "default"},
    }

    request = compose_tile_request(
        config=config,
        options=options,
        url="https://example.com/tile",
        bbox=tile.bbox,
        width=tile.width,
        height=tile.height,
        crs=tile.crs,
        params={"service": "xyz"},
        output_format=Format.PNG,
    )

    assert request.headers == {
        "User-Agent": "tilearray-test/1.0",
        "X-Peer-Test": "enabled",
    }
    assert request.params == {
        "layer": "roads",
        "service": "xyz",
        "style": "default",
    }


@pytest.mark.unit
@respx.mock
def test_xyz_service_compose_tile_request_headers_reach_fetch() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, content=b"ok")

    respx.get(url__regex=r"https://tile\.openstreetmap\.org/.*").mock(
        side_effect=handler
    )

    config = XYZConfig.from_url(
        OSM_TEMPLATE,
        zoom=10,
        headers={"User-Agent": "tilearray-peer-test/1.0"},
        params={"layer": "roads"},
    )
    service = config.build_service()
    tile = TileGeometry(
        bbox=_tile_bounds(1, 1, 10, CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=1,
        tile_y=1,
        zoom=10,
    )

    request = service.build_tile_request(tile, headers={"X-Peer-Test": "enabled"})
    response = fetch_tile(request)

    assert response.success is True
    assert captured.get("user-agent") == "tilearray-peer-test/1.0"
    assert captured.get("x-peer-test") == "enabled"
    assert request.params["layer"] == "roads"
