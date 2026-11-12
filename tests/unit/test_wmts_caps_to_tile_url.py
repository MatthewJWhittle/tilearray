"""WMTS caps Dimension / Style / Format flow into tile URLs and KVP params."""

from __future__ import annotations

import pytest

from tests.unit.hands_on_examples import FORMAT_STYLE_CAPS, TIME_DIMENSION_CAPS
from tilearray import inspect_capabilities, preview_array
from tilearray.types import Format, ServiceTypeEnum

pytestmark = pytest.mark.unit


def test_time_dimension_default_fills_rest_template() -> None:
    caps = TIME_DIMENSION_CAPS.read_text()
    ins = inspect_capabilities(
        caps,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(
        layer="daily", tile_matrix_set="GoogleMapsCompatible", tile_matrix=1
    )
    pv = preview_array(cfg, (-10, 40, 10, 60), bbox_crs="EPSG:4326")
    url = pv.tile_requests[0].url
    assert "{Time}" not in url
    assert "{" not in url
    assert "2026-07-15" in url


def test_kvp_uses_layer_format_and_default_style() -> None:
    caps = FORMAT_STYLE_CAPS.read_text()
    ins = inspect_capabilities(
        caps,
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
    pv = preview_array(cfg, (-10, 40, 10, 60), bbox_crs="EPSG:4326")
    params = dict(pv.tile_requests[0].params)
    assert params.get("format") == "image/jpeg"
    assert params.get("style") == "normal"


def test_rest_style_placeholder_filled_from_default() -> None:
    caps = FORMAT_STYLE_CAPS.read_text()
    ins = inspect_capabilities(
        caps,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(
        layer="png_rest", tile_matrix_set="GoogleMapsCompatible", tile_matrix=1
    )
    pv = preview_array(cfg, (-10, 40, 10, 60), bbox_crs="EPSG:4326")
    assert "/png_rest/default/" in pv.tile_requests[0].url
