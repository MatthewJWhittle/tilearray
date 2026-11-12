"""Base-URL query strings survive fetch; HTTP clients follow redirects."""

from __future__ import annotations

import httpx
import pytest
import respx

from tests.unit.hands_on_examples import png_bytes, tile_geometry
from tilearray import WMSConfig, compute_mosaic, create_array
from tilearray._http_client import make_sync_client
from tilearray.service.wms import WMSService
from tilearray.tiles import fetch_tile
from tilearray.types import Format

pytestmark = pytest.mark.unit


def test_mapserv_base_url_query_survives_getmap_fetch() -> None:
    """Host-shaped example: mapserv?map=/path/to.map must keep map= on the wire."""
    seen: list[str] = []
    png = png_bytes(8, 8, 3)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"https://example\.com/mapserv.*").mock(
            side_effect=lambda request: (
                seen.append(str(request.url)),
                httpx.Response(
                    200,
                    content=png,
                    headers={"content-type": "image/png"},
                ),
            )[1]
        )
        svc = WMSService(
            "https://example.com/mapserv?map=/maps/demo.map",
            layers="a",
            output_format=Format.PNG,
        )
        req = svc.build_tile_request(tile_geometry(width=8, height=8))
        fetch_tile(req)
    assert seen, "expected at least one HTTP GET"
    assert "map=" in seen[0]
    assert "/maps/demo.map" in seen[0] or "%2Fmaps%2Fdemo.map" in seen[0]


def test_sync_client_follows_redirects() -> None:
    client = make_sync_client()
    try:
        assert client.follow_redirects is True
    finally:
        client.close()


def test_create_follows_301_to_image_tile() -> None:
    png = png_bytes(256, 256, 3)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r"https://old\.example\.com/wms.*").mock(
            side_effect=lambda request: httpx.Response(
                301,
                headers={
                    "Location": "https://new.example.com/wms?"
                    + request.url.query.decode()
                },
                text="<html>Moved</html>",
            )
        )
        mock.get(url__regex=r"https://new\.example\.com/wms.*").mock(
            return_value=httpx.Response(
                200, content=png, headers={"content-type": "image/png"}
            )
        )
        cfg = WMSConfig.from_url("https://old.example.com/wms", layers="a")
        da = create_array(
            cfg,
            (0, 0, 2560, 2560),
            bbox_crs="EPSG:27700",
            resolution=(10, 10),
        )
        out = compute_mosaic(da)
    assert out.shape[0] > 0 and out.shape[1] > 0
