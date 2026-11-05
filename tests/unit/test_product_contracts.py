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

import httpx
import pytest
import respx

from tilearray.errors import ConfigurationError
from tilearray.inspect import inspect_endpoint
from tilearray.plan import plan_array
from tilearray.service.base import detect_service_type
from tilearray.service.config import WMTSConfig
from tilearray.types import CRS, ServiceTypeEnum

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "data" / "inspect_fixtures"


def _wmts_caps() -> str:
    return (FIXTURES / "wmts_get_capabilities.xml").read_text(encoding="utf-8")


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
