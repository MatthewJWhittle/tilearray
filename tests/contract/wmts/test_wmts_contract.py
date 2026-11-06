"""Offline WMTS contract tests backed by recorded HTTP (VCR cassettes).

Locks Kartverket topo / utm33n (EPSG:25833) GetCapabilities parsing and
capabilities-driven tile planning against independently computed bounds.
Default CI replays cassettes only — no outbound network.
"""

from __future__ import annotations

from io import BytesIO

import numpy as np
import pytest
from PIL import Image
from pytest import MonkeyPatch

from tilearray.array import create_array
from tilearray.service.config import WMTSConfig
from tilearray.service.wmts import WMTSService
from tilearray.types import CRS, BoundingBox, Format

# Public Kartverket WMTS cache — see docs/example-sources.md / guides/wmts.md
KARTVERKET_WMTS_URL = "https://cache.kartverket.no/v1/service"
KARTVERKET_LAYER = "topo"
KARTVERKET_TMS = "utm33n"
KARTVERKET_MATRIX = 8  # capabilities id is zero-padded "08"

# Documented TopLeftCorner / ScaleDenominator for utm33n matrix "08"
# (locked by the GetCapabilities cassette). Independent OGC maths:
#   pixel_m = ScaleDenominator * 0.00028
_UTM33_SCALE_08 = 302232.1428571428
_UTM33_TOP_LEFT_X = -2_500_000.0
_UTM33_TOP_LEFT_Y = 9_045_984.0
_OGC_PIXEL_M = 0.00028
_PIXEL_M = _UTM33_SCALE_08 * _OGC_PIXEL_M
_TILE_SPAN_M = 256 * _PIXEL_M

# Known tile covering a small Oslo-area lon/lat window at matrix 08
_TILE_COL = 127
_TILE_ROW = 110
_EXPECTED_MIN_X = _UTM33_TOP_LEFT_X + _TILE_COL * _TILE_SPAN_M
_EXPECTED_MAX_X = _UTM33_TOP_LEFT_X + (_TILE_COL + 1) * _TILE_SPAN_M
_EXPECTED_MAX_Y = _UTM33_TOP_LEFT_Y - _TILE_ROW * _TILE_SPAN_M
_EXPECTED_MIN_Y = _UTM33_TOP_LEFT_Y - (_TILE_ROW + 1) * _TILE_SPAN_M

# Query inset inside that tile (lon/lat) — planning must snap to the tile grid
_QUERY_BBOX_4326 = BoundingBox(
    min_x=10.6,
    min_y=59.85,
    max_x=10.8,
    max_y=59.95,
    crs=CRS.EPSG_4326,
)


def _png_bytes() -> bytes:
    array = np.full((256, 256), 120, dtype=np.uint8)
    with BytesIO() as buffer:
        Image.fromarray(array, mode="L").save(buffer, format="PNG")
        return buffer.getvalue()


def _mock_fetch_tile(payload: bytes):
    def _fetch(request, **_kwargs):  # type: ignore[no-untyped-def]
        from tilearray.types import TileResponse

        return TileResponse(
            data=payload,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    return _fetch


@pytest.fixture
def vcr_cassette_name(request: pytest.FixtureRequest) -> str:
    """Share one GetCapabilities cassette across Kartverket contract methods.

    Both tests hit the same opaque ``/v1/service`` caps URL; bodies only differ
    in layer order, which neither assert cares about.
    """
    if request.cls is TestKartverketWMTSContract:
        return "TestKartverketWMTSContract.get_capabilities_utm33n_matrix"
    test_class = request.cls
    if test_class:
        return f"{test_class.__name__}.{request.node.name}"
    return request.node.name


@pytest.mark.contract
@pytest.mark.vcr()
class TestKartverketWMTSContract:
    """Protocol contract for Kartverket topo WMTS (recorded HTTP)."""

    def test_get_capabilities_utm33n_matrix(self) -> None:
        service = WMTSService(
            KARTVERKET_WMTS_URL,
            layer=KARTVERKET_LAYER,
            tile_matrix_set=KARTVERKET_TMS,
            tile_matrix=KARTVERKET_MATRIX,
            style="default",
            output_format=Format.PNG,
            headers={
                "User-Agent": (
                    "tilearray-contract/0.2 "
                    "(+https://github.com/MatthewJWhittle/tilearray)"
                ),
            },
        )
        caps = service.get_capabilities()

        assert KARTVERKET_LAYER in caps.layers
        assert KARTVERKET_TMS in caps.tile_matrix_sets
        tms = caps.tile_matrix_sets[KARTVERKET_TMS]
        assert tms.supported_crs == CRS.parse("EPSG:25833")
        assert "08" in tms.matrices
        matrix = tms.matrices["08"]
        assert matrix.scale_denominator == pytest.approx(_UTM33_SCALE_08)
        assert matrix.top_left_x == pytest.approx(_UTM33_TOP_LEFT_X)
        assert matrix.top_left_y == pytest.approx(_UTM33_TOP_LEFT_Y)
        assert matrix.tile_width == 256
        assert matrix.tile_height == 256

    def test_plan_tiles_oslo_window_native_bounds(self) -> None:
        """Capabilities-driven planning: lon/lat AOI → EPSG:25833 tile bounds."""

        service = WMTSService(
            KARTVERKET_WMTS_URL,
            layer=KARTVERKET_LAYER,
            tile_matrix_set=KARTVERKET_TMS,
            tile_matrix=KARTVERKET_MATRIX,
            style="default",
            output_format=Format.PNG,
            headers={
                "User-Agent": (
                    "tilearray-contract/0.2 "
                    "(+https://github.com/MatthewJWhittle/tilearray)"
                ),
            },
        )
        # Auto-loads GetCapabilities (same path as create_array)
        tiles = list(service.plan_tiles(_QUERY_BBOX_4326, (256, 256)))

        assert len(tiles) == 1
        tile = tiles[0]
        assert tile.tile_x == _TILE_COL
        assert tile.tile_y == _TILE_ROW
        assert tile.tile_matrix_id == "08"
        assert tile.crs == CRS.parse("EPSG:25833")
        assert tile.bbox.min_x == pytest.approx(_EXPECTED_MIN_X)
        assert tile.bbox.max_x == pytest.approx(_EXPECTED_MAX_X)
        assert tile.bbox.min_y == pytest.approx(_EXPECTED_MIN_Y)
        assert tile.bbox.max_y == pytest.approx(_EXPECTED_MAX_Y)
        assert service.inferred_mosaic_bbox is not None
        assert service.inferred_mosaic_bbox.crs == CRS.parse("EPSG:25833")


@pytest.mark.contract
def test_create_array_kartverket_matrix_pixel_centres(
    monkeypatch: MonkeyPatch,
) -> None:
    """Offline create_array: mosaic CRS + pixel centres match planned tile."""

    monkeypatch.setattr(
        "tilearray.array.fetch_tile",
        _mock_fetch_tile(_png_bytes()),
    )

    from tilearray.service.wmts import WMTSTileMatrix

    matrix = WMTSTileMatrix(
        identifier="08",
        scale_denominator=_UTM33_SCALE_08,
        top_left_x=_UTM33_TOP_LEFT_X,
        top_left_y=_UTM33_TOP_LEFT_Y,
        matrix_width=512,
        matrix_height=512,
        tile_width=256,
        tile_height=256,
    )
    config = WMTSConfig.from_url(
        KARTVERKET_WMTS_URL,
        layer=KARTVERKET_LAYER,
        tile_matrix_set=KARTVERKET_TMS,
        tile_matrix=KARTVERKET_MATRIX,
        style="default",
        output_format=Format.PNG,
        chunk_size=(256, 256),
    )
    # Native-metre inset inside the known tile (avoids lon/lat transform here)
    bbox = (
        _EXPECTED_MIN_X + 100.0,
        _EXPECTED_MIN_Y + 100.0,
        _EXPECTED_MAX_X - 100.0,
        _EXPECTED_MAX_Y - 100.0,
    )
    result = create_array(
        config,
        bbox,
        "EPSG:25833",
        compute=True,
        matrix=matrix,
        matrix_crs=CRS.parse("EPSG:25833"),
        show_progress=False,
    )

    assert result.attrs["crs"] == "EPSG:25833"
    assert result.shape == (256, 256)
    assert float(result.x.min()) == pytest.approx(_EXPECTED_MIN_X + _PIXEL_M / 2.0)
    assert float(result.x.max()) == pytest.approx(_EXPECTED_MAX_X - _PIXEL_M / 2.0)
    assert float(result.y.max()) == pytest.approx(_EXPECTED_MAX_Y - _PIXEL_M / 2.0)
    assert float(result.y.min()) == pytest.approx(_EXPECTED_MIN_Y + _PIXEL_M / 2.0)
    assert "bbox_requested" in result.attrs
