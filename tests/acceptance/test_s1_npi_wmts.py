"""S1 — NPI WMTS EPSG:25833 tile bounds (#97 → #92/#93)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilearray.errors import ValidationError
from tilearray.service.wmts import WMTSParser
from tilearray.types import CRS, BoundingBox

from .catalog import scenario_by_id
from .golden import tile_bounds_nw, tile_span

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s1_npi_wmts"


def _matrix() -> dict:
    return json.loads((FIXTURE / "matrix_level_17.json").read_text(encoding="utf-8"))


def test_s1_independent_golden_bounds_match_published_answer() -> None:
    matrix = _matrix()
    span_x, span_y = tile_span(
        matrix["scale_denominator"],
        matrix["tile_width"],
        matrix["tile_height"],
        "projected",
    )
    assert span_x == pytest.approx(matrix["expected_edge_length"])
    assert span_y == pytest.approx(matrix["expected_edge_length"])

    bounds = tile_bounds_nw(
        top_left_x=matrix["top_left_x"],
        top_left_y=matrix["top_left_y"],
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

    # Approximate hand-drawn polygon is close but must not be treated as truth.
    approx = matrix["approximate_user_bbox"]
    assert abs(bounds[0] - approx[0]) < 1.0
    assert bounds[0] != pytest.approx(approx[0], abs=1e-6)


def test_s1_capabilities_snippet_parses_matrix_but_crs_unsupported() -> None:
    xml = (FIXTURE / "capabilities_snippet.xml").read_text(encoding="utf-8")
    caps = WMTSParser().parse_get_capabilities(xml)
    tms = caps.tile_matrix_sets["default028mm"]
    matrix = tms.matrices["17"]
    assert matrix.scale_denominator == pytest.approx(590.588978796931)
    assert matrix.top_left_x == pytest.approx(-5120900.0)
    assert matrix.top_left_y == pytest.approx(9998100.0)
    # Parser currently collapses unknown SupportedCRS to a default enum member.
    assert tms.supported_crs in {CRS.EPSG_3857, CRS.EPSG_4326}


def test_s1_baseline_epsg25833_unsupported_by_crs_enum() -> None:
    scenario = scenario_by_id("S1")
    assert scenario["baseline"] == "unsupported"
    with pytest.raises((ValueError, ValidationError)):
        CRS.from_string("EPSG:25833")
    with pytest.raises((ValueError, ValidationError)):
        BoundingBox.from_tuple(
            (514863.0, 8682927.0, 514905.0, 8682970.0),
            crs="EPSG:25833",
        )
