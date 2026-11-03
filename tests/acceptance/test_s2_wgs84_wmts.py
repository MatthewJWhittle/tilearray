"""S2 — WGS84 WMTS georeferencing without slippy fallback (#97 → #93/#60)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilearray.service.wmts import WMTSParser, WMTSService
from tilearray.types import CRS, BoundingBox, Format

from .catalog import scenario_by_id
from .golden import tile_bounds_nw, tile_span

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s2_wgs84_wmts"


def _matrix() -> dict:
    return json.loads((FIXTURE / "matrix_level_16.json").read_text(encoding="utf-8"))


def test_s2_independent_golden_bounds_for_wgs84_matrix() -> None:
    matrix = _matrix()
    span_x, span_y = tile_span(
        matrix["scale_denominator"],
        matrix["tile_width"],
        matrix["tile_height"],
        "geographic",
    )
    bounds = tile_bounds_nw(
        top_left_x=matrix["top_left_x_lon"],
        top_left_y=matrix["top_left_y_lat"],
        col=matrix["tile_col"],
        row=matrix["tile_row"],
        span_x=span_x,
        span_y=span_y,
    )
    expected = matrix["expected_bounds"]
    assert bounds[0] == pytest.approx(expected["min_x"])
    assert bounds[1] == pytest.approx(expected["min_y"])
    assert bounds[2] == pytest.approx(expected["max_x"])
    assert bounds[3] == pytest.approx(expected["max_y"])


def test_s2_baseline_classified_fail() -> None:
    assert scenario_by_id("S2")["baseline"] == "fail"


@pytest.mark.xfail(
    strict=True,
    reason="Native WGS84 WMTS matrix axis order + metres-per-unit — #93",
)
def test_s2_desired_planning_matches_independent_golden() -> None:
    matrix = _matrix()
    xml = (FIXTURE / "capabilities_snippet.xml").read_text(encoding="utf-8")
    caps = WMTSParser().parse_get_capabilities(xml)
    service = WMTSService(
        "https://example.invalid/wmts",
        layer="v0Lw1953KMdH",
        tile_matrix_set="WGS84",
        tile_matrix=16,
        output_format=Format.PNG,
        crs=CRS.EPSG_4326,
    )
    service._capabilities = caps

    expected = matrix["expected_bounds"]
    bbox = BoundingBox(
        min_x=expected["min_x"],
        min_y=expected["min_y"],
        max_x=expected["max_x"],
        max_y=expected["max_y"],
        crs=CRS.EPSG_4326,
    )
    tiles = list(service.plan_tiles(bbox, (256, 256)))
    assert len(tiles) == 1
    planned = tiles[0]
    assert planned.tile_x == matrix["tile_col"]
    assert planned.tile_y == matrix["tile_row"]
    assert planned.bbox.min_x == pytest.approx(expected["min_x"])
    assert planned.bbox.min_y == pytest.approx(expected["min_y"])
    assert planned.bbox.max_x == pytest.approx(expected["max_x"])
    assert planned.bbox.max_y == pytest.approx(expected["max_y"])
