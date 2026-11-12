"""Failing-tests base for hands-on bugs #151–#164 (2026-11-12).

These encode the *expected correct* behaviour. On current main they must go
**red**. Do not xfail/skip/soft-mark them — fix PRs stacked on this base turn
them green. Offline + faithful mocks only (no live network).
"""

from __future__ import annotations

import warnings
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import respx
from PIL import Image

from tilearray import (
    TileArrayError,
    WMSConfig,
    XYZConfig,
    compute_mosaic,
    create_array,
    inspect_capabilities,
    preview_array,
)
from tilearray._entry import from_public_args
from tilearray._http_client import make_sync_client
from tilearray.decode import decoder_for_format, read_geotiff_bytes
from tilearray.errors import ConfigurationError, ParseError, ValidationError
from tilearray.service.base import TileGeometry
from tilearray.service.wcs import WCSService
from tilearray.service.wms import WMSService
from tilearray.service.wmts import WMTSService
from tilearray.service.xyz import XYZService
from tilearray.tiles import fetch_tile
from tilearray.types import (
    CRS,
    BoundingBox,
    CoverageDescription,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
    canonicalize_crs_input,
)

pytestmark = pytest.mark.unit

DATA = Path(__file__).resolve().parents[1] / "data"
PRED3_TIF = DATA / "wcs_tiles" / "float32_predictor3_deflate.tif"

_WMTS_DIM_CAPS = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>daily</ows:Identifier>
      <Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style>
      <Format>image/jpeg</Format>
      <Dimension>
        <ows:Identifier>Time</ows:Identifier>
        <Default>2026-07-15</Default>
        <Value>2026-07-15</Value>
      </Dimension>
      <TileMatrixSetLink><TileMatrixSet>GoogleMapsCompatible</TileMatrixSet></TileMatrixSetLink>
      <ResourceURL format="image/jpeg" resourceType="tile"
        template="https://example.com/wmts/daily/{Style}/{Time}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg"/>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>GoogleMapsCompatible</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>1</ows:Identifier>
        <ScaleDenominator>279541132.0143589</ScaleDenominator>
        <TopLeftCorner>-20037508.34278925 20037508.34278925</TopLeftCorner>
        <TileWidth>256</TileWidth><TileHeight>256</TileHeight>
        <MatrixWidth>2</MatrixWidth><MatrixHeight>2</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>
"""

_WMTS_FORMAT_STYLE_CAPS = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>kvp_only</ows:Identifier>
      <Style isDefault="true"><ows:Identifier>normal</ows:Identifier></Style>
      <Format>image/jpeg</Format>
      <TileMatrixSetLink><TileMatrixSet>GoogleMapsCompatible</TileMatrixSet></TileMatrixSetLink>
    </Layer>
    <Layer>
      <ows:Identifier>png_rest</ows:Identifier>
      <Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style>
      <Format>image/png</Format>
      <TileMatrixSetLink><TileMatrixSet>GoogleMapsCompatible</TileMatrixSet></TileMatrixSetLink>
      <ResourceURL format="image/png" resourceType="tile"
        template="https://example.com/wmts/png_rest/{Style}/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png"/>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>GoogleMapsCompatible</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:EPSG::3857</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>1</ows:Identifier>
        <ScaleDenominator>279541132.0143589</ScaleDenominator>
        <TopLeftCorner>-20037508.34278925 20037508.34278925</TopLeftCorner>
        <TileWidth>256</TileWidth><TileHeight>256</TileHeight>
        <MatrixWidth>2</MatrixWidth><MatrixHeight>2</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>
"""

_WMTS_URN_CRS_CAPS = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>layer</ows:Identifier>
      <Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style>
      <Format>image/png</Format>
      <TileMatrixSetLink><TileMatrixSet>google3857</TileMatrixSet></TileMatrixSetLink>
      <ResourceURL format="image/png" resourceType="tile"
        template="https://example.com/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.png"/>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>google3857</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:EPSG:6.18.3:3857</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>1</ows:Identifier>
        <ScaleDenominator>279541132.0143589</ScaleDenominator>
        <TopLeftCorner>-20037508.34278925 20037508.34278925</TopLeftCorner>
        <TileWidth>256</TileWidth><TileHeight>256</TileHeight>
        <MatrixWidth>2</MatrixWidth><MatrixHeight>2</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>
"""

_WMTS_EPSG4326_CAPS = """<?xml version='1.0' encoding='UTF-8'?>
<Capabilities xmlns="http://www.opengis.net/wmts/1.0" xmlns:ows="http://www.opengis.net/ows/1.1">
  <Contents>
    <Layer>
      <ows:Identifier>world</ows:Identifier>
      <Style isDefault="true"><ows:Identifier>default</ows:Identifier></Style>
      <Format>image/jpeg</Format>
      <TileMatrixSetLink><TileMatrixSet>EPSG4326</TileMatrixSet></TileMatrixSetLink>
      <ResourceURL format="image/jpeg" resourceType="tile"
        template="https://example.com/wmts/world/{TileMatrixSet}/{TileMatrix}/{TileRow}/{TileCol}.jpg"/>
    </Layer>
    <TileMatrixSet>
      <ows:Identifier>EPSG4326</ows:Identifier>
      <ows:SupportedCRS>urn:ogc:def:crs:EPSG::4326</ows:SupportedCRS>
      <TileMatrix>
        <ows:Identifier>0</ows:Identifier>
        <ScaleDenominator>279541132.0143589</ScaleDenominator>
        <TopLeftCorner>90.0 -180.0</TopLeftCorner>
        <TileWidth>256</TileWidth><TileHeight>256</TileHeight>
        <MatrixWidth>2</MatrixWidth><MatrixHeight>1</MatrixHeight>
      </TileMatrix>
    </TileMatrixSet>
  </Contents>
</Capabilities>
"""


def _png_bytes(height: int, width: int, bands: int = 3) -> bytes:
    arr = np.zeros((height, width, bands), dtype=np.uint8)
    buf = BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _geometry(
    *,
    min_x: float = 0.0,
    min_y: float = 0.0,
    max_x: float = 1.0,
    max_y: float = 1.0,
    crs: CRS = CRS.EPSG_4326,
    width: int = 256,
    height: int = 256,
) -> TileGeometry:
    return TileGeometry(
        bbox=BoundingBox(
            min_x=min_x, min_y=min_y, max_x=max_x, max_y=max_y, crs=crs
        ),
        width=width,
        height=height,
        crs=crs,
    )


# ---------------------------------------------------------------------------
# #151 — params= must merge, not TypeError
# ---------------------------------------------------------------------------


def test_151_wms_caller_params_merge_into_tile_request() -> None:
    """Caller params= must appear once alongside GetMap keys (no TypeError)."""
    req = WMSService(
        "https://example.com/wms", layers="a", output_format=Format.PNG
    ).build_tile_request(_geometry(), params={"key": "x"})
    assert req.params.get("key") == "x"
    assert req.params.get("service") == "WMS" or req.params.get("REQUEST") == "GetMap" or "GetMap" in str(
        req.params.get("request")
    )


def test_151_wcs_caller_params_merge_into_tile_request() -> None:
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:28992"),
            axis_labels={"EPSG:28992": ("x", "y")},
            native_format=Format.GEOTIFF,
        )
    )
    req = svc.build_tile_request(
        _geometry(
            max_x=2560,
            max_y=2560,
            crs=CRS("EPSG:28992"),
            width=256,
            height=256,
        ),
        params={"key": "x"},
    )
    assert req.params.get("key") == "x"


def test_151_wmts_kvp_caller_params_merge_into_tile_request() -> None:
    geom = TileGeometry(
        bbox=BoundingBox(
            min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326
        ),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=0,
        tile_y=0,
        zoom=1,
    )
    req = WMTSService(
        "https://example.com/wmts",
        layer="a",
        tile_matrix_set="GoogleMapsCompatible",
        tile_matrix="1",
        output_format=Format.PNG,
    ).build_tile_request(geom, params={"key": "x"})
    assert req.params.get("key") == "x"


def test_151_xyz_params_still_carry() -> None:
    """Regression guard: XYZ already worked — keep key once."""
    geom = TileGeometry(
        bbox=BoundingBox(
            min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326
        ),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
        tile_x=0,
        tile_y=0,
        zoom=3,
    )
    req = XYZService(
        "https://tiles.example.com/{z}/{x}/{y}.png", zoom=3
    ).build_tile_request(geom, params={"key": "x"})
    assert req.params.get("key") == "x" or "key=x" in req.url

# ---------------------------------------------------------------------------
# #152 — thin RGB(A) tiles keep (y, x[, band]) layout
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("height", [1, 2, 3, 4])
@pytest.mark.parametrize("bands", [1, 3, 4])
def test_152_thin_png_decodes_band_last(height: int, bands: int) -> None:
    raw = _png_bytes(height, 256, bands if bands > 1 else 3)
    if bands == 1:
        # True greyscale PNG
        buf = BytesIO()
        Image.fromarray(np.zeros((height, 256), dtype=np.uint8)).save(buf, format="PNG")
        raw = buf.getvalue()
    elif bands == 4:
        buf = BytesIO()
        Image.fromarray(np.zeros((height, 256, 4), dtype=np.uint8)).save(
            buf, format="PNG"
        )
        raw = buf.getvalue()

    resp = TileResponse(
        data=raw,
        content_type="image/png",
        status_code=200,
        headers={},
        url="",
        success=True,
    )
    req = TileRequest(
        url="https://example.com/wms",
        params={},
        output_format=Format.PNG,
        crs=CRS.EPSG_4326,
        width=256,
        height=height,
        bbox=BoundingBox(
            min_x=0, min_y=0, max_x=1, max_y=0.01, crs=CRS.EPSG_4326
        ),
    )
    arr = decoder_for_format(Format.PNG)(resp, req)
    if bands == 1:
        assert arr.shape == (height, 256) or arr.shape == (height, 256, 1)
    else:
        assert arr.shape == (height, 256, bands)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_152_thin_png_narrow_width_band_last(width: int) -> None:
    raw = _png_bytes(256, width, 3)
    resp = TileResponse(
        data=raw,
        content_type="image/png",
        status_code=200,
        headers={},
        url="",
        success=True,
    )
    req = TileRequest(
        url="https://example.com/wms",
        params={},
        output_format=Format.PNG,
        crs=CRS.EPSG_4326,
        width=width,
        height=256,
        bbox=BoundingBox(
            min_x=0, min_y=0, max_x=0.01, max_y=1, crs=CRS.EPSG_4326
        ),
    )
    assert decoder_for_format(Format.PNG)(resp, req).shape == (256, width, 3)


# ---------------------------------------------------------------------------
# #153 — PREDICTOR=3 GeoTIFF: decode or loud ConfigurationError
# ---------------------------------------------------------------------------


def test_153_predictor3_geotiff_is_tilearray_error_or_decodes() -> None:
    """Missing imagecodecs must not leak bare ValueError (#72 boundary)."""
    raw = PRED3_TIF.read_bytes()
    assert len(raw) == 314
    try:
        arr = read_geotiff_bytes(raw)
    except TileArrayError as exc:
        message = str(exc).lower()
        assert "imagecodecs" in message
        assert isinstance(exc, ConfigurationError) or isinstance(exc, ParseError)
        return
    assert arr.shape == (4, 4)
    assert arr.dtype == np.float32


def test_153_missing_imagecodecs_wrapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """tifffile's missing-codec ValueError becomes ConfigurationError."""

    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise ValueError(
            "<PREDICTOR.FLOATINGPOINT: 3> requires the 'imagecodecs' package"
        )

    monkeypatch.setattr("tifffile.TiffFile.asarray", boom, raising=False)
    # Prefer patching the decode helper's asarray call site if attribute path differs.
    import tifffile

    original_init = tifffile.TiffFile.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self.asarray = boom  # type: ignore[method-assign]

    monkeypatch.setattr(tifffile.TiffFile, "__init__", patched_init)

    with pytest.raises(ConfigurationError, match="imagecodecs"):
        read_geotiff_bytes(PRED3_TIF.read_bytes())


# ---------------------------------------------------------------------------
# #154 — WCS 2.0.1 must send scaleSize
# ---------------------------------------------------------------------------


def test_154_wcs201_build_tile_request_includes_scalesize() -> None:
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:28992"),
            axis_labels={"EPSG:28992": ("x", "y")},
            native_format=Format.GEOTIFF,
        )
    )
    req = svc.build_tile_request(
        _geometry(
            max_x=2560,
            max_y=2560,
            crs=CRS("EPSG:28992"),
            width=256,
            height=256,
        )
    )
    scale = req.params.get("scaleSize")
    assert scale is not None
    assert "256" in str(scale)
    # Vendor width/height may remain for ArcGIS; scaleSize is required for MapServer.
    assert "x(256)" in str(scale).replace(" ", "") or "x(256)" in str(scale)


# ---------------------------------------------------------------------------
# #155 — base-URL query must survive GetMap
# ---------------------------------------------------------------------------


def test_155_base_url_query_preserved_on_fetch() -> None:
    seen: list[str] = []
    png = _png_bytes(8, 8, 3)
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
        req = svc.build_tile_request(_geometry(width=8, height=8))
        fetch_tile(req)
    assert seen, "expected at least one HTTP GET"
    assert "map=" in seen[0]
    assert "/maps/demo.map" in seen[0] or "%2Fmaps%2Fdemo.map" in seen[0]


# ---------------------------------------------------------------------------
# #156 — WMTS {Time} filled from Dimension default
# ---------------------------------------------------------------------------


def test_156_wmts_time_dimension_filled_from_default() -> None:
    ins = inspect_capabilities(
        _WMTS_DIM_CAPS,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(
        layer="daily", tile_matrix_set="GoogleMapsCompatible", tile_matrix=1
    )
    url = from_public_args(cfg, (-10, 40, 10, 60), "EPSG:4326").tile_requests[0].url
    assert "{Time}" not in url
    assert "{" not in url
    assert "2026-07-15" in url

# ---------------------------------------------------------------------------
# #157 — WMTS Format/Style from caps
# ---------------------------------------------------------------------------


def test_157_wmts_kvp_uses_layer_format_and_default_style() -> None:
    ins = inspect_capabilities(
        _WMTS_FORMAT_STYLE_CAPS,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    kvp = next(r for r in ins.layers if r.identifier == "kvp_only")
    assert "image/jpeg" in tuple(kvp.formats)
    assert "normal" in tuple(kvp.styles)

    cfg = ins.build_config(
        layer="kvp_only", tile_matrix_set="GoogleMapsCompatible", tile_matrix=1
    )
    assert cfg.output_format in (Format.JPEG, "image/jpeg")
    assert cfg.style == "normal"
    params = dict(
        from_public_args(cfg, (-10, 40, 10, 60), "EPSG:4326").tile_requests[0].params
    )
    assert params.get("format") == "image/jpeg"
    assert params.get("style") == "normal"


def test_157_wmts_rest_style_placeholder_filled() -> None:
    ins = inspect_capabilities(
        _WMTS_FORMAT_STYLE_CAPS,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(
        layer="png_rest", tile_matrix_set="GoogleMapsCompatible", tile_matrix=1
    )
    url = from_public_args(cfg, (-10, 40, 10, 60), "EPSG:4326").tile_requests[0].url
    assert "/png_rest/default/" in url


# ---------------------------------------------------------------------------
# #158 — lat-first WCS subsets + no short subsettingCRS when native
# ---------------------------------------------------------------------------


def test_158_wcs_lat_first_subset_axes_and_crs() -> None:
    svc = WCSService("https://example.com/wcs", coverage_id="dem")
    svc.set_coverage_metadata(
        CoverageDescription(
            identifier="dem",
            native_crs=CRS("EPSG:4258"),
            axis_labels={"EPSG:4258": ("y", "x")},
            native_format=Format.GEOTIFF,
        )
    )
    req = svc.build_tile_request(
        _geometry(
            min_x=2.8,
            min_y=50.775,
            max_x=2.825,
            max_y=50.79,
            crs=CRS("EPSG:4258"),
            width=250,
            height=150,
        )
    )
    subsets = req.params["subset"]
    assert isinstance(subsets, list)
    assert any(s.startswith("y(50.775") for s in subsets)
    assert any(s.startswith("x(2.8") for s in subsets)
    # Native CRS: omit short-form subsettingCRS (GeoServer rejects EPSG:NNNN).
    scrs = req.params.get("subsettingCRS")
    assert scrs is None or str(scrs).startswith("http://www.opengis.net/def/crs/")


# ---------------------------------------------------------------------------
# #159 — HTTP redirects followed
# ---------------------------------------------------------------------------


def test_159_sync_client_follows_redirects() -> None:
    client = make_sync_client()
    try:
        assert client.follow_redirects is True
    finally:
        client.close()


def test_159_tile_fetch_follows_301() -> None:
    png = _png_bytes(256, 256, 3)
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


# ---------------------------------------------------------------------------
# #160 — output_crs + resolution planned in mosaic CRS units
# ---------------------------------------------------------------------------


def test_160_output_crs_resolution_plans_reasonable_grid() -> None:
    cfg = WMSConfig.from_url("https://example.com/wms", layers="a")
    with respx.mock(assert_all_called=False):
        pv = preview_array(
            cfg,
            (-3.25, 55.92, -3.10, 55.98),
            bbox_crs="EPSG:4326",
            output_crs="EPSG:27700",
            resolution=(20, 20),
        )
    # Edinburgh box at 20 m is hundreds of pixels / a handful of 256 tiles —
    # never ~130k one-pixel requests.
    assert pv.request_count < 50
    assert pv.request_count >= 1
    assert pv.mosaic_crs in ("EPSG:27700", CRS("EPSG:27700"))
    # Pixel size in mosaic CRS units ≈ 20 m.
    assert pv.planned_pixel_size is not None
    assert abs(pv.planned_pixel_size[0] - 20.0) < 1e-6
    assert abs(pv.planned_pixel_size[1] - 20.0) < 1e-6


# ---------------------------------------------------------------------------
# #161 — preview estimates without materialising; create guards huge AOIs
# ---------------------------------------------------------------------------


def test_161_preview_does_not_build_every_tile_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "build_tile_request must not run to estimate preview request_count"
        )

    monkeypatch.setattr(XYZService, "build_tile_request", boom)
    cfg = XYZConfig.from_url(
        "https://tiles.example.com/{z}/{x}/{y}.png", zoom=14
    )
    pv = preview_array(cfg, (-6, 50, 2, 56), bbox_crs="EPSG:4326")
    # GB @ z14 ≈ 166_896 tiles (exact count from index range).
    assert pv.request_count > 100_000


def test_161_create_array_refuses_huge_request_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError(
            "create_array must refuse huge plans before building TileRequests"
        )

    monkeypatch.setattr(XYZService, "build_tile_request", boom)
    cfg = XYZConfig.from_url(
        "https://tiles.example.com/{z}/{x}/{y}.png", zoom=14
    )
    with pytest.raises(ValidationError, match=r"(?i)request|tile|limit|too many"):
        create_array(cfg, (-6, 50, 2, 56), bbox_crs="EPSG:4326")


# ---------------------------------------------------------------------------
# #162 — public boundary raises TileArrayError
# ---------------------------------------------------------------------------


def test_162_zero_area_bbox_is_tilearray_validation_error() -> None:
    cfg = XYZConfig.from_url(
        "https://tiles.example.com/{z}/{x}/{y}.png", zoom=3
    )
    with pytest.raises(ValidationError) as ei:
        create_array(cfg, (1, 1, 1, 1), bbox_crs="EPSG:4326")
    assert isinstance(ei.value, TileArrayError)


def test_162_inverted_bbox_is_tilearray_validation_error() -> None:
    cfg = XYZConfig.from_url(
        "https://tiles.example.com/{z}/{x}/{y}.png", zoom=3
    )
    with pytest.raises(ValidationError) as ei:
        create_array(cfg, (10, 0, 0, 10), bbox_crs="EPSG:4326")
    assert isinstance(ei.value, TileArrayError)


def test_162_json_200_body_is_tilearray_error() -> None:
    cfg = XYZConfig.from_url(
        "https://tiles.example.com/{z}/{x}/{y}.png", zoom=3
    )
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*").mock(
            return_value=httpx.Response(
                200,
                json={"error": "quota"},
                headers={"content-type": "application/json"},
            )
        )
        with pytest.raises(TileArrayError):
            compute_mosaic(
                create_array(cfg, (0, 0, 10, 10), bbox_crs="EPSG:4326")
            )


# ---------------------------------------------------------------------------
# #163 — versioned OGC URN CRS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("urn:ogc:def:crs:EPSG::3857", "EPSG:3857"),
        ("urn:ogc:def:crs:EPSG:6.3:4326", "EPSG:4326"),
        ("urn:ogc:def:crs:EPSG:6.18:3857", "EPSG:3857"),
        ("urn:ogc:def:crs:EPSG:6.18.3:3857", "EPSG:3857"),
        ("URN:OGC:DEF:CRS:EPSG:9.9.1:27700", "EPSG:27700"),
    ],
)
def test_163_versioned_epsg_urn_canonicalises(raw: str, expected: str) -> None:
    assert canonicalize_crs_input(raw) == expected


def test_163_wmts_caps_with_versioned_urn_parses() -> None:
    ins = inspect_capabilities(
        _WMTS_URN_CRS_CAPS,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(
        layer="layer", tile_matrix_set="google3857", tile_matrix=1
    )
    # Must not raise ValidationError about SupportedCRS / EPSG:6.
    entry = from_public_args(cfg, (-10, 40, 10, 60), "EPSG:4326")
    assert entry.tile_requests


# ---------------------------------------------------------------------------
# #164 — world edges: no invalid XYZ indices; WMTS not silent
# ---------------------------------------------------------------------------


def test_164_xyz_antimeridian_no_invalid_tile_x() -> None:
    service = XYZService("https://tiles.example.com/{z}/{x}/{y}.png", zoom=6)
    bbox = BoundingBox(
        min_x=175, min_y=-20, max_x=185, max_y=-15, crs=CRS.EPSG_4326
    )
    try:
        tiles = list(service.plan_tiles(bbox, (256, 256)))
    except ValidationError:
        return  # refuse policy is acceptable
    assert tiles
    assert all(0 <= t.tile_x <= 63 for t in tiles)
    assert all(0 <= t.tile_y <= 63 for t in tiles)


def test_164_xyz_polar_no_negative_tile_y() -> None:
    service = XYZService("https://tiles.example.com/{z}/{x}/{y}.png", zoom=6)
    bbox = BoundingBox(
        min_x=-10, min_y=80, max_x=10, max_y=89.9, crs=CRS.EPSG_4326
    )
    try:
        tiles = list(service.plan_tiles(bbox, (256, 256)))
    except ValidationError:
        return
    assert tiles
    assert all(0 <= t.tile_y <= 63 for t in tiles)


def test_164_wmts_past_180_not_silent() -> None:
    """Past-180° AOI must refuse or warn — never silently truncate with no signal."""
    ins = inspect_capabilities(
        _WMTS_EPSG4326_CAPS,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(
        layer="world", tile_matrix_set="EPSG4326", tile_matrix=0
    )
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            entry = from_public_args(
                cfg, (178, -20, 182, -15), "EPSG:4326"
            )
        except ValidationError:
            return  # refuse policy OK
        # If planning succeeds, a warning or attr must record the clamp.
        messages = " ".join(str(w.message).lower() for w in caught)
        attrs_blob = " ".join(
            f"{k}={v}".lower()
            for k, v in (getattr(entry, "__dict__", {}) or {}).items()
        )
        signal = messages + attrs_blob
        assert any(
            token in signal
            for token in ("truncat", "clamp", "antimeridian", "180", "warn")
        ), (
            "WMTS plan past 180° succeeded with no truncation/clamp signal "
            f"(requests={len(entry.tile_requests)})"
        )

