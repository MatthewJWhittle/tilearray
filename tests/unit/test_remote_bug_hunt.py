"""Hostile unit tests — expected to go red and expose library bugs.

These are deliberately aggressive. A first-run green result is suspicious:
strengthen the assertion or document why the behaviour is intentionally OK.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import respx
from PIL import Image

from tilearray import create_array, inspect_endpoint, preview_array
from tilearray.array import _soft_fill_array
from tilearray.errors import (
    ConfigurationError,
    NetworkError,
    ParseError,
    ServiceError,
    ValidationError,
)
from tilearray.inspect import inspect_capabilities
from tilearray.mosaic_plan import MosaicPlan
from tilearray.service.base import BaseService, TileGeometry
from tilearray.service.wms import WMSService, format_wms_bbox
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parents[1] / "data" / "inspect_fixtures"

_EXCEPTION_REPORT = """<?xml version="1.0"?>
<ExceptionReport xmlns="http://www.opengis.net/ows/1.1" version="1.1.0">
  <Exception exceptionCode="InvalidParameterValue" locator="layer">
    <ExceptionText>Unknown layer: nope</ExceptionText>
  </Exception>
</ExceptionReport>
"""

_HTML_LOGIN = """<!DOCTYPE html>
<html><head><title>Sign in</title></head>
<body><h1>Login portal</h1><form>password</form></body></html>
"""


def _geotiff_decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    return np.ones((request.height or 1, request.width or 1), dtype=np.float32)


# ---------------------------------------------------------------------------
# Attack: OGC ExceptionReport must not look like empty capabilities
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("service_type", ["WMTS", "WMS", "WCS"])
def test_exception_report_caps_are_loud_not_empty(service_type: str) -> None:
    """OGC ExceptionReport body must fail loudly — not '0 layers found'."""

    with pytest.raises(
        (ParseError, ServiceError, ValidationError, ConfigurationError)
    ) as ei:
        inspect_capabilities(
            _EXCEPTION_REPORT,
            service_type=service_type,
            base_url="https://cache.example.org/v1/service",
        )
    message = str(ei.value).lower()
    assert "exception" in message or "ogc" in message
    assert (
        "unknown layer" in message
        or "invalidparametervalue" in message.lower()
        or "exceptiontext" in message
    )


@respx.mock
def test_opaque_probe_all_exception_reports_is_actionable() -> None:
    """Opaque URL where every protocol probe returns ExceptionReport → clear error."""

    url = "https://cache.example.org/spatialdata/opaque"
    respx.get(url).mock(return_value=httpx.Response(200, text=_EXCEPTION_REPORT))

    with pytest.raises((ConfigurationError, ParseError, ServiceError)) as ei:
        inspect_endpoint(url)
    message = str(ei.value).lower()
    # Must not succeed as empty WMTS; must mention probe failure or exception.
    assert "exception" in message or "probe" in message or "unable to detect" in message


@respx.mock
def test_opaque_wms_html_then_real_wms_xml_recovers() -> None:
    """First WMTS probe HTML, then WMS XML — discovery should land on WMS."""

    url = "https://agency.example.org/spatialdata/mapper"
    wms_xml = (FIXTURES / "wms_get_capabilities.xml").read_text(encoding="utf-8")

    # Probe order: WMTS → WMS → WCS. Route by query service=.
    def _route(request: httpx.Request) -> httpx.Response:
        service = (request.url.params.get("service") or "").upper()
        if service == "WMTS":
            return httpx.Response(200, text=_HTML_LOGIN)
        if service == "WMS":
            return httpx.Response(200, text=wms_xml)
        return httpx.Response(404, text="no")

    respx.get(url).mock(side_effect=_route)
    inspection = inspect_endpoint(url)
    assert inspection.service_type is ServiceTypeEnum.WMS
    assert any(r.identifier.startswith("Vegetation") for r in inspection.resources)


@respx.mock
def test_opaque_wcs_discovery_skips_html_wmts_wms() -> None:
    url = "https://agency.example.org/spatialdata/elevation"
    wcs_xml = (FIXTURES / "wcs_get_capabilities.xml").read_text(encoding="utf-8")

    def _route(request: httpx.Request) -> httpx.Response:
        service = (request.url.params.get("service") or "").upper()
        if service in {"WMTS", "WMS"}:
            return httpx.Response(200, text=_HTML_LOGIN)
        if service == "WCS":
            return httpx.Response(200, text=wcs_xml)
        return httpx.Response(500, text="err")

    respx.get(url).mock(side_effect=_route)
    inspection = inspect_endpoint(url)
    assert inspection.service_type is ServiceTypeEnum.WCS
    assert any(
        "Elevation" in (r.title or "") or "DTM" in r.identifier
        for r in inspection.resources
    )


# ---------------------------------------------------------------------------
# Attack: soft-fail × uint8 must not silently zero-fill
# ---------------------------------------------------------------------------


def test_soft_fill_rejects_nan_into_integer_dtype() -> None:
    """NaN soft-fill into uint8 becomes zeros today — that hides failures as data."""

    request = TileRequest(
        url="https://example.com/t",
        params={},
        output_format=Format.PNG,
        width=2,
        height=2,
    )
    with pytest.raises((ValidationError, ValueError, TypeError)):
        _soft_fill_array(request, np.dtype("uint8"), float("nan"), 1)


def test_create_array_soft_fail_with_uint8_dtype_is_loud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Public path: soft_fail=True + dtype=uint8 must not soft-fill as zeros."""

    rows, cols = 1, 1

    class GridService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.GEOTIFF

        def __init__(self) -> None:
            super().__init__("https://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            width, height = chunk_size
            return MosaicPlan(
                tiles=(
                    TileRequest(
                        url="https://example.com/tile/0",
                        params={},
                        output_format=Format.GEOTIFF,
                        crs=bbox.crs,
                        bbox=bbox,
                        width=width,
                        height=height,
                        retries=0,
                    ),
                )
            )

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: GridService()
    )
    monkeypatch.setattr(
        "tilearray.array.fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=b"",
            content_type="text/plain",
            status_code=404,
            headers={},
            url=request.url,
            success=False,
            error_message="HTTP 404",
        ),
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)

    with pytest.raises((ValidationError, ValueError, TypeError)):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(4, 4),
            grid_shape=(rows, cols),
            output_format=Format.GEOTIFF,
            band_policy="first_band",
            soft_fail=True,
            soft_fail_max_fraction=1.0,
            dtype="uint8",
            compute=True,
            show_progress=False,
            tile_decoder=_geotiff_decoder,
            max_concurrent_requests=1,
            rate_limit_per_second=None,
            fetch_retries=0,
        )


# ---------------------------------------------------------------------------
# Attack: WMS GetMap CRS vs bbox CRS mismatch must fail loud
# ---------------------------------------------------------------------------


def test_wms_getmap_rejects_bbox_crs_mismatch_with_output_crs() -> None:
    """Tile bbox in metres + GetMap crs=EPSG:4326 must not silently swap axes."""

    bbox_27700 = BoundingBox(
        min_x=400_000.0,
        min_y=300_000.0,
        max_x=401_000.0,
        max_y=301_000.0,
        crs=CRS.EPSG_27700,
    )
    geom = TileGeometry(bbox=bbox_27700, width=256, height=256, crs=CRS.EPSG_27700)
    service = WMSService(
        "https://example.com/wms",
        layers="Vegetation_Object_Model_2022",
        crs=CRS.EPSG_27700,
        output_format=Format.PNG,
    )
    with pytest.raises(ValidationError, match="(?i)crs|bbox|reproject|output_crs"):
        service.build_tile_request(geom, output_crs=CRS.EPSG_4326)


def test_wms13_axis_order_for_4326_is_lat_lon() -> None:
    """Known-good: WMS 1.3 geographic BBOX is lat,lon,lat,lon — keep locked."""

    bbox = BoundingBox(
        min_x=-1.5, min_y=50.5, max_x=-1.0, max_y=51.0, crs=CRS.EPSG_4326
    )
    assert format_wms_bbox(bbox, CRS.EPSG_4326, "1.3.0") == "50.5,-1.5,51.0,-1.0"
    assert format_wms_bbox(bbox, CRS.EPSG_4326, "1.1.1") == "-1.5,50.5,-1.0,51.0"


# ---------------------------------------------------------------------------
# Attack: inspect build_config hostility
# ---------------------------------------------------------------------------


def test_build_config_wrong_matrix_set_lists_known() -> None:
    xml = (FIXTURES / "wmts_get_capabilities.xml").read_text(encoding="utf-8")
    inspection = inspect_capabilities(
        xml, service_type="WMTS", base_url="https://example.com/wmts"
    )
    with pytest.raises(ValidationError, match="GoogleMapsCompatible"):
        inspection.build_config(
            layer="blue_marble", tile_matrix_set="utm33n", tile_matrix=0
        )


def test_build_config_output_crs_rejected_on_wmts_config() -> None:
    xml = (FIXTURES / "wmts_get_capabilities.xml").read_text(encoding="utf-8")
    inspection = inspect_capabilities(
        xml, service_type="WMTS", base_url="https://example.com/wmts"
    )
    config = inspection.build_config(
        layer="blue_marble",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix=0,
    )
    # Passing output_crs through create_array/plan must stay loud.
    with pytest.raises(ValidationError, match="output_crs"):
        preview_array(
            config,
            bbox=(-10, -10, 10, 10),
            bbox_crs=CRS.EPSG_4326,
            output_crs=CRS.EPSG_3857,
        )


def test_build_config_wms_rejects_unknown_style_when_advertised() -> None:
    """If caps list styles, a typo style should fail — not silently send garbage."""

    xml = (FIXTURES / "wms_get_capabilities.xml").read_text(encoding="utf-8")
    inspection = inspect_capabilities(
        xml, service_type="WMS", base_url="https://example.com/wms"
    )
    with pytest.raises(ValidationError, match="(?i)style"):
        inspection.build_config(
            layers="Vegetation_Object_Model_2022",
            styles="not-a-real-style",
        )


# ---------------------------------------------------------------------------
# Attack: HTML / ExceptionReport masquerading as successful tiles
# ---------------------------------------------------------------------------


@respx.mock
def test_html_200_tile_is_actionable_not_pillow_gibberish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP 200 + HTML login body must fail with portal/HTML hint, not Pillow noise."""

    url = "https://tiles.example.org/0/0/0.png"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            text=_HTML_LOGIN,
            headers={"content-type": "text/html; charset=utf-8"},
        )
    )

    class OneTile(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.PNG

        def __init__(self) -> None:
            super().__init__("https://tiles.example.org")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            w, h = chunk_size
            return MosaicPlan(
                tiles=(
                    TileRequest(
                        url=url,
                        params={},
                        output_format=Format.PNG,
                        crs=bbox.crs,
                        bbox=bbox,
                        width=w,
                        height=h,
                        retries=0,
                    ),
                )
            )

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    monkeypatch.setattr("tilearray._planning.get_service", lambda *a, **k: OneTile())
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)

    with pytest.raises((NetworkError, ValidationError, ParseError, ServiceError)) as ei:
        create_array(
            service_url="https://tiles.example.org",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(4, 4),
            grid_shape=(1, 1),
            output_format=Format.PNG,
            compute=True,
            show_progress=False,
            max_concurrent_requests=1,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
    message = str(ei.value).lower()
    assert "cannot identify image file" not in message
    assert any(token in message for token in ("html", "login", "portal", "not tile")), (
        message
    )


@respx.mock
def test_exception_report_labeled_image_png_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """WMS ExceptionReport with Content-Type image/png must not look like corrupt PNG."""

    url = "https://maps.example.org/wms"
    respx.get(url).mock(
        return_value=httpx.Response(
            200,
            text=_EXCEPTION_REPORT,
            headers={"content-type": "image/png"},
        )
    )

    class OneWms(BaseService):
        service_type = ServiceTypeEnum.WMS
        output_format = Format.PNG

        def __init__(self) -> None:
            super().__init__(url)

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            w, h = chunk_size
            return MosaicPlan(
                tiles=(
                    TileRequest(
                        url=url,
                        params={"SERVICE": "WMS", "REQUEST": "GetMap"},
                        output_format=Format.PNG,
                        crs=bbox.crs,
                        bbox=bbox,
                        width=w,
                        height=h,
                        retries=0,
                    ),
                )
            )

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    monkeypatch.setattr("tilearray._planning.get_service", lambda *a, **k: OneWms())
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)

    with pytest.raises((NetworkError, ValidationError, ParseError, ServiceError)) as ei:
        create_array(
            service_url=url,
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.WMS,
            layers="Vegetation_Object_Model_2022",
            chunk_size=(4, 4),
            grid_shape=(1, 1),
            output_format=Format.PNG,
            compute=True,
            show_progress=False,
            max_concurrent_requests=1,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
    message = str(ei.value).lower()
    assert "cannot identify image file" not in message
    assert any(token in message for token in ("exception", "ogc", "unknown layer")), (
        message
    )


# ---------------------------------------------------------------------------
# Attack: mosaic attrs must not claim palette indices for truecolor RGB
# ---------------------------------------------------------------------------


def test_rgb_jpeg_attrs_do_not_claim_palette_indices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Truecolor JPEG mosaics must not advertise palette_mode='indices'."""

    buf = BytesIO()
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    class JpegTile(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.JPEG

        def __init__(self) -> None:
            super().__init__("https://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            w, h = chunk_size
            return MosaicPlan(
                tiles=(
                    TileRequest(
                        url="https://example.com/tile.jpg",
                        params={},
                        output_format=Format.JPEG,
                        crs=bbox.crs,
                        bbox=bbox,
                        width=w,
                        height=h,
                        retries=0,
                    ),
                )
            )

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    monkeypatch.setattr("tilearray._planning.get_service", lambda *a, **k: JpegTile())
    monkeypatch.setattr(
        "tilearray.array.fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=jpeg_bytes,
            content_type="image/jpeg",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        ),
    )
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)
    arr = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(4, 4),
        grid_shape=(1, 1),
        output_format=Format.JPEG,
        compute=True,
        show_progress=False,
        max_concurrent_requests=1,
        rate_limit_per_second=None,
        fetch_retries=0,
    )
    assert arr.ndim == 3 and arr.shape[-1] == 3
    # indices is only meaningful for palette PNGs — RGB must not claim it.
    assert arr.attrs.get("palette_mode") != "indices"
    assert arr.attrs.get("palette_mode") in {None, "rgb", "truecolor"}


# ---------------------------------------------------------------------------
# Attack: WMS multi-layer style arity + per-layer validation
# ---------------------------------------------------------------------------


def test_build_config_wms_multi_layer_rejects_unknown_style_on_second_layer() -> None:
    """Style validation must not only check the first layer's advertised styles."""

    xml = (FIXTURES / "wms_get_capabilities.xml").read_text(encoding="utf-8")
    inspection = inspect_capabilities(
        xml, service_type="WMS", base_url="https://example.com/wms"
    )
    # Layer 2022 advertises 'default'; 2021 advertises none. A typo style that
    # only matches the first layer's set must still fail for the pair if we
    # require styles known to *every* named layer that advertises styles —
    # and a made-up style must never pass just because the first layer lists it.
    with pytest.raises(ValidationError, match="(?i)style"):
        inspection.build_config(
            layers="Vegetation_Object_Model_2021,Vegetation_Object_Model_2022",
            styles="default,bogus-style",
        )


def test_build_config_wms_multi_layer_styles_arity_must_match() -> None:
    """Comma styles count must match comma layers count when styles are given."""

    xml = (FIXTURES / "wms_get_capabilities.xml").read_text(encoding="utf-8")
    inspection = inspect_capabilities(
        xml, service_type="WMS", base_url="https://example.com/wms"
    )
    with pytest.raises(ValidationError, match="(?i)style|layer"):
        inspection.build_config(
            layers="Vegetation_Object_Model_2022,Vegetation_Object_Model_2021",
            styles="default",  # one style, two layers — ambiguous
        )


# ---------------------------------------------------------------------------
# Attack: redirects on opaque inspect must not be silent
# ---------------------------------------------------------------------------


@respx.mock
def test_opaque_inspect_names_302_when_caps_redirect() -> None:
    """Clients follow redirects; inspect succeeds when the target serves caps."""

    url = "https://agency.example.org/spatialdata/mapper"
    dest = "https://agency.example.org/spatialdata/mapper/wms"
    caps = """<?xml version='1.0' encoding='UTF-8'?>
<WMS_Capabilities version="1.3.0">
  <Capability>
    <Request>
      <GetMap>
        <Format>image/png</Format>
      </GetMap>
    </Request>
    <Layer>
      <Name>demo</Name>
      <Title>Demo</Title>
      <CRS>EPSG:4326</CRS>
    </Layer>
  </Capability>
</WMS_Capabilities>"""

    def _route(request: httpx.Request) -> httpx.Response:
        target = str(request.url).split("?")[0].rstrip("/")
        if target == url.rstrip("/"):
            return httpx.Response(
                302, headers={"Location": dest + "?" + str(request.url.query)}
            )
        if target == dest.rstrip("/"):
            return httpx.Response(200, text=caps, headers={"content-type": "text/xml"})
        return httpx.Response(404, text="no")

    respx.route(method="GET").mock(side_effect=_route)

    inspection = inspect_endpoint(url)
    assert inspection.service_type == ServiceTypeEnum.WMS
    assert any(layer.identifier == "demo" for layer in inspection.layers)


def test_soft_fill_multiband_png_preserves_band_axis(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Soft-fail pad for RGB PNG must keep a band axis — not collapse to HxW zeros."""

    buf = BytesIO()
    Image.new("RGB", (4, 4), color=(1, 2, 3)).save(buf, format="PNG")
    rgb = buf.getvalue()
    calls = {"n": 0}

    class GridService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.PNG

        def __init__(self) -> None:
            super().__init__("https://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            width, height = chunk_size
            # Two tiles so the first can probe RGB and the second soft-fails.
            return MosaicPlan(
                tiles=tuple(
                    [
                        TileRequest(
                            url=f"https://example.com/tile/{i}",
                            params={},
                            output_format=Format.PNG,
                            crs=bbox.crs,
                            bbox=bbox,
                            width=width,
                            height=height,
                            retries=0,
                        )
                        for i in range(2)
                    ]
                )
            )

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    def _fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        calls["n"] += 1
        if "tile/0" in request.url:
            return TileResponse(
                data=rgb,
                content_type="image/png",
                status_code=200,
                headers={},
                url=request.url,
                success=True,
            )
        return TileResponse(
            data=b"",
            content_type="text/plain",
            status_code=404,
            headers={},
            url=request.url,
            success=False,
            error_message="HTTP 404",
        )

    monkeypatch.setattr(
        "tilearray._planning.get_service", lambda *a, **k: GridService()
    )
    monkeypatch.setattr("tilearray.array.fetch_tile", _fetch)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=1, crs=CRS.EPSG_4326)
    arr = create_array(
        service_url="https://example.com",
        bbox=bbox,
        bbox_crs=CRS.EPSG_4326,
        service_type=ServiceTypeEnum.XYZ,
        chunk_size=(4, 4),
        grid_shape=(1, 2),
        output_format=Format.PNG,
        soft_fail=True,
        soft_fail_max_fraction=1.0,
        soft_fill_value=0.0,
        dtype="float32",
        compute=True,
        show_progress=False,
        max_concurrent_requests=1,
        rate_limit_per_second=None,
        fetch_retries=0,
    )
    assert arr.ndim == 3, f"soft-fill dropped band axis: shape={arr.shape}"
    assert arr.shape[-1] == 3


# ---------------------------------------------------------------------------
# Attack: 401 / 429 remain loud and actionable
# ---------------------------------------------------------------------------


@respx.mock
def test_tile_401_message_mentions_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://secure.example.org/tile/0/0/0.png"
    respx.get(url).mock(
        return_value=httpx.Response(401, text="Unauthorized", headers={})
    )

    class OneTile(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.PNG

        def __init__(self) -> None:
            super().__init__("https://secure.example.org")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> MosaicPlan:
            w, h = chunk_size
            return MosaicPlan(
                tiles=(
                    TileRequest(
                        url=url,
                        params={},
                        output_format=Format.PNG,
                        crs=bbox.crs,
                        bbox=bbox,
                        width=w,
                        height=h,
                        retries=0,
                    ),
                )
            )

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    monkeypatch.setattr("tilearray._planning.get_service", lambda *a, **k: OneTile())
    bbox = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)

    with pytest.raises(NetworkError) as ei:
        create_array(
            service_url="https://secure.example.org",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(4, 4),
            grid_shape=(1, 1),
            output_format=Format.PNG,
            compute=True,
            show_progress=False,
            max_concurrent_requests=1,
            rate_limit_per_second=None,
            fetch_retries=0,
        )
    message = str(ei.value).lower()
    assert "401" in message
    assert any(
        token in message for token in ("auth", "credential", "params", "headers")
    )
