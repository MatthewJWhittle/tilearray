"""Contract tests for the shared create/plan entry builder."""

from __future__ import annotations

import pytest

from tilearray._entry import ArrayEntry, from_public_args
from tilearray.errors import ValidationError
from tilearray.service.config import XYZConfig
from tilearray.types import CRS, ServiceTypeEnum

pytestmark = pytest.mark.unit

OSM_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
BBOX = (-0.001, 51.5, 0.001, 51.501)


def test_from_public_args_returns_array_entry() -> None:
    config = XYZConfig.from_url(OSM_TEMPLATE, zoom=10)
    entry = from_public_args(config, BBOX, CRS.EPSG_4326)

    assert isinstance(entry, ArrayEntry)
    assert entry.service.service_type == ServiceTypeEnum.XYZ
    assert entry.fetch_policy.max_concurrent >= 1
    assert len(entry.tile_requests) >= 1
    assert entry.request.bbox_crs == CRS.EPSG_4326
    assert entry.request.target_crs == CRS.EPSG_3857
    assert "crs" in entry.tile_options


def test_from_public_args_rejects_crs_kwarg() -> None:
    config = XYZConfig.from_url(OSM_TEMPLATE, zoom=10)
    with pytest.raises(ValidationError, match="no longer accepts crs="):
        from_public_args(config, BBOX, CRS.EPSG_4326, crs=CRS.EPSG_4326)


def test_from_public_args_rejects_service_type_mismatch() -> None:
    config = XYZConfig.from_url(OSM_TEMPLATE, zoom=10)
    with pytest.raises(ValidationError, match="service_type does not match"):
        from_public_args(
            config,
            BBOX,
            CRS.EPSG_4326,
            service_type=ServiceTypeEnum.WMS,
        )


def test_from_public_args_rejects_output_crs_on_xyz() -> None:
    config = XYZConfig.from_url(OSM_TEMPLATE, zoom=10)
    with pytest.raises(ValidationError, match="output_crs"):
        from_public_args(config, BBOX, CRS.EPSG_4326, output_crs=CRS.EPSG_3857)


def test_from_public_args_applies_call_max_concurrent() -> None:
    config = XYZConfig.from_url(OSM_TEMPLATE, zoom=10, max_concurrent_requests=8)
    entry = from_public_args(
        config,
        BBOX,
        CRS.EPSG_4326,
        max_concurrent_requests=2,
    )
    assert entry.fetch_policy.max_concurrent == 8
    assert entry.fetch_policy.call_max_concurrent == 2
