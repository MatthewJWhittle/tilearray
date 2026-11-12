"""Versioned OGC EPSG URNs canonicalise to EPSG:code."""

from __future__ import annotations

import pytest

from tests.unit.hands_on_examples import VERSIONED_URN_CAPS
from tilearray import inspect_capabilities, preview_array
from tilearray.types import ServiceTypeEnum, canonicalize_crs_input

pytestmark = pytest.mark.unit


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
def test_versioned_epsg_urn_canonicalises(raw: str, expected: str) -> None:
    assert canonicalize_crs_input(raw) == expected


def test_wmts_caps_with_versioned_supportedcrs_previews() -> None:
    caps = VERSIONED_URN_CAPS.read_text()
    ins = inspect_capabilities(
        caps,
        service_type=ServiceTypeEnum.WMTS,
        base_url="https://example.com/wmts",
    )
    cfg = ins.build_config(layer="layer", tile_matrix_set="google3857", tile_matrix=1)
    # Must not raise ValidationError about SupportedCRS / EPSG:6.
    pv = preview_array(cfg, (-10, 40, 10, 60), bbox_crs="EPSG:4326")
    assert pv.request_count >= 1
