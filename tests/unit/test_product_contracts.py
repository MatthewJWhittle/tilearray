"""PR-gated product-contract tests (Modes A–D).

Named surface for user-visible journeys: URL detection taxonomy, opaque-URL
inspect, soft-fail phases, and plot-ready mosaic shape. Keep these offline
(no ``net`` / ``slow`` markers) so PR green includes product validation, not
only didactic fixtures.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import respx

from tilearray.array import create_array
from tilearray.errors import ConfigurationError, NetworkError
from tilearray.inspect import inspect_endpoint
from tilearray.plan import plan_array
from tilearray.service.base import BaseService, TileGeometry, detect_service_type
from tilearray.service.config import WMTSConfig
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "data" / "inspect_fixtures"


def _wmts_caps() -> str:
    return (FIXTURES / "wmts_get_capabilities.xml").read_text(encoding="utf-8")


def _png_decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    return np.ones((request.height or 1, request.width or 1, 3), dtype=np.uint8)


def _geotiff_decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    return np.ones((request.height or 1, request.width or 1), dtype=np.float32)


# ---------------------------------------------------------------------------
# Mode A — URL detection taxonomy (not host-specific names)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://example.com/v1/service?SERVICE=WMTS",
            ServiceTypeEnum.WMTS,
        ),
        (
            "https://example.com/path/wmts",
            ServiceTypeEnum.WMTS,
        ),
        (
            "https://tiles.example/{z}/{x}/{y}.png",
            ServiceTypeEnum.XYZ,
        ),
    ],
    ids=["query-SERVICE", "token-path", "xyz-template"],
)
def test_detect_service_type_taxonomy_resolvable(
    url: str, expected: ServiceTypeEnum
) -> None:
    assert detect_service_type(url) is expected


def test_detect_service_type_opaque_path_is_actionable() -> None:
    """Opaque OGC fronts (no protocol token) must fail loudly with typed-config hint."""

    with pytest.raises(ConfigurationError) as exc_info:
        detect_service_type("https://cache.example.org/v1/service")
    message = str(exc_info.value)
    assert "Unable to detect service type" in message
    assert "WMTSConfig" in message or "typed" in message.lower() or "Config" in message
    assert "inspect_endpoint" in message or "service_type" in message


# ---------------------------------------------------------------------------
# Mode B — opaque URL → inspect → build_config → plan (offline)
# ---------------------------------------------------------------------------


@respx.mock
def test_opaque_url_inspect_journey_to_plan() -> None:
    """Opaque base_url + real-shaped caps → config → plan identifiers/CRS."""

    opaque = "https://cache.example.org/v1/service"
    respx.get(opaque).mock(return_value=httpx.Response(200, text=_wmts_caps()))

    inspection = inspect_endpoint(opaque)
    assert inspection.service_type is ServiceTypeEnum.WMTS

    config = inspection.build_config(
        layer="blue_marble",
        tile_matrix_set="GoogleMapsCompatible",
        target_resolution=100_000.0,
    )
    assert isinstance(config, WMTSConfig)
    assert config.layer == "blue_marble"
    assert config.tile_matrix_set == "GoogleMapsCompatible"
    assert config.tile_matrix is not None

    # Lon/lat window over the world so matrix 0 planning stays a single tile.
    plan = plan_array(
        config,
        bbox=(-10.0, -10.0, 10.0, 10.0),
        bbox_crs=CRS.EPSG_4326,
    )
    assert plan.crs in {"EPSG:3857", "3857"} or plan.crs == CRS.EPSG_3857.value
    assert plan.bbox_crs in {"EPSG:4326", "4326"}
    assert plan.request_count >= 1
    assert plan.matrix_id is not None


@respx.mock
def test_opaque_url_explicit_service_type_skips_guesswork() -> None:
    opaque = "https://cache.example.org/spatialdata/endpoint"
    respx.get(opaque).mock(return_value=httpx.Response(200, text=_wmts_caps()))

    inspection = inspect_endpoint(opaque, service_type="wmts")
    assert inspection.service_type is ServiceTypeEnum.WMTS
    assert inspection.resources


def test_docs_and_stage_gate_use_opaque_inspect_happy_path() -> None:
    """Critical call shapes must not rely on didactic ``…/wmts`` alone."""

    wmts_guide = (ROOT / "docs" / "guides" / "wmts.md").read_text(encoding="utf-8")
    assert "inspect_endpoint(" in wmts_guide
    assert "cache.kartverket.no/v1/service" in wmts_guide
    assert "target_resolution" in wmts_guide
    assert "bbox_crs" in wmts_guide

    notebook = ROOT / "notebooks" / "stage_gate_0_3.ipynb"
    nb = json.loads(notebook.read_text(encoding="utf-8"))
    sources = "\n".join(
        "".join(cell.get("source", []))
        for cell in nb.get("cells", [])
        if cell.get("cell_type") == "code"
    )
    assert "inspect_endpoint" in sources
    assert re.search(r"target_resolution\s*=", sources)
    assert re.search(r"bbox_crs\s*=", sources)
    assert "/v1/service" in sources or "WMTS_URL" in sources


# ---------------------------------------------------------------------------
# Mode C — soft-fail phase table (product decision: probe stays hard)
# ---------------------------------------------------------------------------


def _phase_grid_service(
    rows: int,
    cols: int,
    *,
    fmt: Format,
) -> type[BaseService]:
    class GridService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = fmt

        def __init__(self) -> None:
            super().__init__("https://example.com")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            width, height = chunk_size
            grid_rows, grid_cols = options.get("grid_shape", (rows, cols))
            span_x = (bbox.max_x - bbox.min_x) / grid_cols
            span_y = (bbox.max_y - bbox.min_y) / grid_rows
            tiles: list[TileRequest] = []
            for row in range(grid_rows):
                for col in range(grid_cols):
                    min_x = bbox.min_x + col * span_x
                    max_x = min_x + span_x
                    min_y = bbox.min_y + (grid_rows - 1 - row) * span_y
                    max_y = min_y + span_y
                    idx = row * grid_cols + col
                    tiles.append(
                        TileRequest(
                            url=f"https://example.com/tile/{idx}",
                            params={},
                            output_format=fmt,
                            crs=bbox.crs,
                            bbox=BoundingBox(
                                min_x=min_x,
                                min_y=min_y,
                                max_x=max_x,
                                max_y=max_y,
                                crs=bbox.crs,
                            ),
                            width=width,
                            height=height,
                            retries=0,
                        )
                    )
            return tiles

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            return self.generate_tile_requests(tile.bbox, (tile.width, tile.height))[0]

    return GridService


def test_soft_fail_phase_pre_graph_probe_stays_hard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1: preserve-policy formats probe before soft-fail is bound → hard fail."""

    rows, cols = 1, 2
    monkeypatch.setattr(
        "tilearray._planning.get_service",
        lambda *a, **k: _phase_grid_service(rows, cols, fmt=Format.PNG)(),
    )

    def fail_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        return TileResponse(
            data=b"",
            content_type="text/plain",
            status_code=404,
            headers={},
            url=request.url,
            success=False,
            error_message="HTTP 404",
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", fail_fetch)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=1, crs=CRS.EPSG_4326)

    with pytest.raises(NetworkError):
        create_array(
            service_url="https://example.com",
            bbox=bbox,
            bbox_crs=CRS.EPSG_4326,
            service_type=ServiceTypeEnum.XYZ,
            chunk_size=(4, 4),
            grid_shape=(rows, cols),
            output_format=Format.PNG,
            soft_fail=True,
            soft_fail_max_fraction=1.0,
            compute=False,
            tile_decoder=_png_decoder,
            max_concurrent_requests=2,
            rate_limit_per_second=None,
            fetch_retries=0,
        )


def test_soft_fail_phase_post_graph_soft_fills(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 2: after the graph exists, exhausted tile-local 404 soft-fills + attrs."""

    rows, cols = 2, 2  # 4 tiles; one 404 → 0.25 ≤ max_fraction 1.0
    monkeypatch.setattr(
        "tilearray._planning.get_service",
        lambda *a, **k: _phase_grid_service(rows, cols, fmt=Format.GEOTIFF)(),
    )

    def selective_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        idx = int(request.url.rsplit("/", 1)[-1])
        if idx == 0:
            return TileResponse(
                data=b"missing",
                content_type="text/plain",
                status_code=404,
                headers={},
                url=request.url,
                success=False,
                error_message="HTTP 404",
            )
        return TileResponse(
            data=b"ok",
            content_type="image/tiff",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        )

    monkeypatch.setattr("tilearray.array.fetch_tile", selective_fetch)
    bbox = BoundingBox(min_x=0, min_y=0, max_x=4, max_y=4, crs=CRS.EPSG_4326)
    result = create_array(
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
        compute=True,
        tile_decoder=_geotiff_decoder,
        max_concurrent_requests=2,
        rate_limit_per_second=None,
        fetch_retries=0,
    )
    tile_errors = result.attrs["tilearray_tile_errors"]
    assert tile_errors["armed"] is True
    assert tile_errors["soft_failed"] == 1
    assert tile_errors["aborted"] is False
    assert int(np.isnan(result.values).sum()) == 4 * 4


def test_soft_fail_phase_auth_and_429_still_abort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 3: auth 401 and exhausted 429 abort even with soft_fail=True."""

    rows, cols = 1, 2
    bbox = BoundingBox(min_x=0, min_y=0, max_x=2, max_y=1, crs=CRS.EPSG_4326)

    for status in (401, 429):
        monkeypatch.setattr(
            "tilearray._planning.get_service",
            lambda *a, **k: _phase_grid_service(rows, cols, fmt=Format.GEOTIFF)(),
        )

        def _make_fail(code: int):
            def fail_status(request: TileRequest, **kwargs: Any) -> TileResponse:
                return TileResponse(
                    data=b"nope",
                    content_type="text/plain",
                    status_code=code,
                    headers={"Retry-After": "0"} if code == 429 else {},
                    url=request.url,
                    success=False,
                    error_message=f"HTTP {code}",
                )

            return fail_status

        monkeypatch.setattr("tilearray.array.fetch_tile", _make_fail(status))
        with pytest.raises(NetworkError):
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
                compute=True,
                tile_decoder=_geotiff_decoder,
                max_concurrent_requests=2,
                rate_limit_per_second=None,
                fetch_retries=0,
            )


def test_soft_fail_docs_and_stage_gate_state_probe_phase() -> None:
    """Docs/notebooks must name the probe-hard exception beside soft-fail."""

    how = (ROOT / "docs" / "how-it-works.md").read_text(encoding="utf-8")
    assert "soft_fail" in how or "soft-fail" in how.lower()
    assert "probe" in how.lower()

    troubleshooting = (ROOT / "docs" / "troubleshooting.md").read_text(encoding="utf-8")
    assert "probe" in troubleshooting.lower()
    assert "401" in troubleshooting

    nb = json.loads(
        (ROOT / "notebooks" / "stage_gate_0_2.ipynb").read_text(encoding="utf-8")
    )
    text = "\n".join(
        "".join(cell.get("source", [])) for cell in nb.get("cells", [])
    )
    assert "band_policy" in text and "first_band" in text
    assert "probe" in text.lower()


# ---------------------------------------------------------------------------
# Mode D — plot / analysis-ready mosaic shape (offline palette fixture)
# ---------------------------------------------------------------------------


def _palette_png_bytes(*, expand_colors: bool = True) -> bytes:
    """Real-shaped palette PNG (not a solid-color didactic tile)."""

    from io import BytesIO

    from PIL import Image

    image = Image.new("P", (8, 8))
    palette = [0] * 768
    # Checkerboard of two non-gray RGB entries so imshow(rgb="band") is meaningful.
    palette[0:3] = [200, 40, 40]
    palette[3:6] = [40, 40, 200]
    image.putpalette(palette)
    pixels = image.load()
    assert pixels is not None
    for y in range(8):
        for x in range(8):
            pixels[x, y] = 0 if (x + y) % 2 == 0 else 1
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def test_expand_palette_mosaic_is_plot_ready_for_imshow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After expand_palette=True, dims/dtype suit ``xarray.plot.imshow(rgb="band")``."""

    from tilearray import _planning as planning_module
    from tilearray import array as array_module

    raw = _palette_png_bytes()
    bbox = BoundingBox(min_x=0, min_y=0, max_x=8, max_y=8, crs=CRS.EPSG_3857)

    class PaletteXYZ:
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.PNG

        def generate_tile_requests(
            self, *args: Any, **kwargs: Any
        ) -> list[TileRequest]:
            del args, kwargs
            return [
                TileRequest(
                    url="http://example.com/0/0/0.png",
                    params={},
                    output_format=Format.PNG,
                    crs=CRS.EPSG_3857,
                    bbox=bbox,
                    width=8,
                    height=8,
                )
            ]

    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: PaletteXYZ())
    monkeypatch.setattr(
        array_module,
        "fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=raw,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        ),
    )

    result = create_array(
        service_url="http://example.com/{z}/{x}/{y}.png",
        bbox=bbox,
        bbox_crs=CRS.EPSG_3857,
        chunk_size=(8, 8),
        output_format=Format.PNG,
        expand_palette=True,
        compute=True,
        show_progress=False,
    )

    assert "band" in result.dims
    assert result.sizes["band"] >= 3
    assert result.dtype == np.uint8 or (
        np.issubdtype(result.dtype, np.floating)
        and float(result.max()) <= 1.0 + 1e-6
    ), (
        "plot-ready RGB must be uint8 or float in [0, 1] — "
        f"got dtype={result.dtype}, max={float(result.max())}"
    )
    assert result.attrs.get("palette_mode") == "rgb"
    # Not a sparse index field that imshow(rgb="band") turns into cyan speckles.
    assert float(result.values[..., 0].max()) >= 40


def test_default_palette_indices_stay_single_band_gray(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default indices path remains single-band (documented gray / index plot)."""

    from tilearray import _planning as planning_module
    from tilearray import array as array_module

    raw = _palette_png_bytes()
    bbox = BoundingBox(min_x=0, min_y=0, max_x=8, max_y=8, crs=CRS.EPSG_3857)

    class PaletteXYZ:
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.PNG

        def generate_tile_requests(
            self, *args: Any, **kwargs: Any
        ) -> list[TileRequest]:
            del args, kwargs
            return [
                TileRequest(
                    url="http://example.com/0/0/0.png",
                    params={},
                    output_format=Format.PNG,
                    crs=CRS.EPSG_3857,
                    bbox=bbox,
                    width=8,
                    height=8,
                )
            ]

    monkeypatch.setattr(planning_module, "get_service", lambda *a, **k: PaletteXYZ())
    monkeypatch.setattr(
        array_module,
        "fetch_tile",
        lambda request, **kwargs: TileResponse(
            data=raw,
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
        ),
    )

    result = create_array(
        service_url="http://example.com/{z}/{x}/{y}.png",
        bbox=bbox,
        bbox_crs=CRS.EPSG_3857,
        chunk_size=(8, 8),
        output_format=Format.PNG,
        expand_palette=False,
        compute=True,
        show_progress=False,
    )

    assert result.ndim == 2 or (
        "band" in result.dims and result.sizes.get("band", 1) == 1
    )
    assert result.attrs.get("palette_mode") == "indices"
