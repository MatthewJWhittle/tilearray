"""Unit tests for WMS service adapter."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx
import xarray as xr
from PIL import Image
from pytest import MonkeyPatch

from tilearray.array import create_array
from tilearray.fetch import TileFetcher
from tilearray.service.base import TileGeometry
from tilearray.service.config import WMSConfig
from tilearray.service.wms import WMSService, format_wms_bbox
from tilearray.tiles import fetch_tile
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = pytest.mark.unit

WMS_URL = "https://example.com/wms"


@pytest.fixture(autouse=True)
def reset_fetcher_instances():
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


@pytest.fixture
def png_tile_bytes() -> bytes:
    array = np.full((128, 128), 64, dtype=np.uint8)
    with BytesIO() as buffer:
        Image.fromarray(array, mode="L").save(buffer, format="PNG")
        return buffer.getvalue()


def test_format_wms_bbox_epsg4326_v130() -> None:
    bbox = BoundingBox(min_x=-1.0, min_y=50.0, max_x=1.0, max_y=52.0, crs=CRS.EPSG_4326)
    assert format_wms_bbox(bbox, CRS.EPSG_4326, "1.3.0") == "50.0,-1.0,52.0,1.0"


def test_format_wms_bbox_projected_v130() -> None:
    bbox = BoundingBox(min_x=0, min_y=0, max_x=100, max_y=100, crs=CRS.EPSG_3857)
    assert format_wms_bbox(bbox, CRS.EPSG_3857, "1.3.0") == "0.0,0.0,100.0,100.0"


def test_wms_service_build_tile_request() -> None:
    service = WMSService(
        WMS_URL,
        layers="vegetation",
        version="1.3.0",
        output_format=Format.PNG,
        crs=CRS.EPSG_4326,
    )
    geometry = TileGeometry(
        bbox=BoundingBox(
            min_x=-0.1, min_y=51.4, max_x=0.1, max_y=51.6, crs=CRS.EPSG_4326
        ),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
    )

    request = service.build_tile_request(geometry)

    assert request.url == WMS_URL
    assert request.params["service"] == "WMS"
    assert request.params["request"] == "GetMap"
    assert request.params["version"] == "1.3.0"
    assert request.params["layers"] == "vegetation"
    assert request.params["crs"] == "EPSG:4326"
    assert request.params["bbox"] == "51.4,-0.1,51.6,0.1"
    assert request.params["width"] == "256"
    assert request.params["height"] == "256"
    assert request.params["format"] == "image/png"
    assert request.output_format == Format.PNG


def test_wms_service_build_tile_request_includes_headers() -> None:
    service = WMSService(
        WMS_URL,
        layers="vegetation",
        headers={"User-Agent": "tilearray-test/1.0"},
    )
    geometry = TileGeometry(
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=128,
        height=128,
        crs=CRS.EPSG_4326,
    )

    request = service.build_tile_request(
        geometry,
        headers={"X-Peer-Test": "enabled"},
    )

    assert request.headers == {
        "User-Agent": "tilearray-test/1.0",
        "X-Peer-Test": "enabled",
    }


def test_wms_service_requires_layers() -> None:
    with pytest.raises(ValueError, match="layers"):
        WMSService(WMS_URL, layers="")


def _mock_fetch_tile(png_tile_bytes: bytes):
    def fetch_tile_impl(request: TileRequest, **kwargs: object) -> TileResponse:
        return TileResponse(
            data=png_tile_bytes,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    return fetch_tile_impl


def test_create_array_with_wms_service(
    png_tile_bytes: bytes,
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr("tilearray.array.fetch_tile", _mock_fetch_tile(png_tile_bytes))

    config = WMSConfig.from_url(
        WMS_URL,
        layers="vegetation",
        output_format=Format.PNG,
        chunk_size=(128, 128),
        grid_shape=(1, 1),
        cache_dir=tmp_path,
    )
    bbox = BoundingBox(
        min_x=-0.05, min_y=51.45, max_x=0.05, max_y=51.55, crs=CRS.EPSG_4326
    )

    result = create_array(config, bbox, CRS.EPSG_4326, compute=True)

    assert isinstance(result, xr.DataArray)
    assert result.shape == (128, 128)
    assert result.attrs["service_type"] == ServiceTypeEnum.WMS.value
    assert float(result.mean()) == pytest.approx(64.0, rel=1e-3)


@respx.mock
def test_wms_config_headers_reach_http_fetch(png_tile_bytes: bytes) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(dict(request.headers))
        return httpx.Response(200, content=png_tile_bytes)

    respx.get(url__regex=r"https://example\.com/wms.*").mock(side_effect=handler)

    service = WMSService(
        WMS_URL,
        layers="vegetation",
        output_format=Format.PNG,
        headers={"User-Agent": "tilearray-peer-test/1.0"},
    )
    geometry = TileGeometry(
        bbox=BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326),
        width=128,
        height=128,
        crs=CRS.EPSG_4326,
    )
    request = service.build_tile_request(geometry)

    response = fetch_tile(request)

    assert response.success is True
    assert captured.get("user-agent") == "tilearray-peer-test/1.0"
    assert "service=WMS" in str(request.url) or request.params.get("service") == "WMS"
