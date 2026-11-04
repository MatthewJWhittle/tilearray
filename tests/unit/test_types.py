"""Unit tests for shared type models."""

from __future__ import annotations

from datetime import datetime

import pytest
from pyproj import CRS as PyprojCRS

from tilearray.errors import ValidationError
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    TemporalExtent,
)

pytestmark = pytest.mark.unit


def test_crs_from_string_and_integer() -> None:
    assert CRS.from_string("EPSG:4326") == CRS.EPSG_4326
    assert CRS.from_integer(4326) == CRS.EPSG_4326
    assert CRS.from_epsg("EPSG:27700") == CRS.EPSG_27700
    assert CRS.from_epsg(3857) == CRS.EPSG_3857


def test_crs_from_string_rejects_invalid_format() -> None:
    with pytest.raises(ValidationError, match="Invalid CRS format"):
        CRS.from_string("4326")


def test_crs_parse_accepts_provider_codes_beyond_presets() -> None:
    for code in ("EPSG:25833", "EPSG:28992", "EPSG:32632"):
        parsed = CRS.parse(code)
        assert parsed == code
        assert parsed.value == code
        assert parsed.is_projected is True
        assert parsed.is_geographic is False
        assert parsed.north_axis_first is False
        assert parsed.default_subset_axes() == ("E", "N")


def test_crs_parse_accepts_integer_and_pyproj() -> None:
    assert CRS.parse(25833) == "EPSG:25833"
    assert CRS.parse(PyprojCRS.from_epsg(28992)) == "EPSG:28992"
    assert CRS("epsg:32632") == CRS.parse("EPSG:32632")


def test_crs_presets_remain_usable() -> None:
    assert CRS.EPSG_4326.value == "EPSG:4326"
    assert CRS.EPSG_3857 == "EPSG:3857"
    assert CRS.EPSG_32633.is_projected is True
    assert CRS.EPSG_27700.default_subset_axes() == ("E", "N")
    assert CRS.EPSG_3857.default_subset_axes() == ("x", "y")
    assert CRS.EPSG_4326.is_geographic is True
    assert CRS.EPSG_4326.north_axis_first is True
    assert CRS.EPSG_4326.default_subset_axes() == ("Long", "Lat")


def test_crs_typo_epgs_is_actionable() -> None:
    with pytest.raises(ValidationError, match=r"Did you mean 'EPSG:28992'"):
        CRS.parse("EPGS:28992")


def test_crs_rejects_unknown_authority() -> None:
    with pytest.raises(ValidationError, match="Invalid CRS"):
        CRS.parse("EPSG:9999999")


def test_bounding_box_from_tuple_accepts_crs_variants() -> None:
    bbox = BoundingBox.from_tuple((0, 0, 1, 1), crs="EPSG:4326")
    assert bbox.crs == CRS.EPSG_4326

    bbox_int = BoundingBox.from_tuple((0, 0, 1, 1), crs=4326)
    assert bbox_int.crs == CRS.EPSG_4326

    bbox_enum = BoundingBox.from_tuple((0, 0, 1, 1), crs=CRS.EPSG_4326)
    assert bbox_enum.crs == CRS.EPSG_4326

    bbox_provider = BoundingBox.from_tuple((0, 0, 1, 1), crs="EPSG:25833")
    assert bbox_provider.crs == "EPSG:25833"


def test_bounding_box_validation_rejects_inverted_axes() -> None:
    with pytest.raises(ValueError, match="min_x must be less than max_x"):
        BoundingBox(min_x=1, min_y=0, max_x=0, max_y=1, crs=CRS.EPSG_4326)

    with pytest.raises(ValueError, match="min_y must be less than max_y"):
        BoundingBox(min_x=0, min_y=1, max_x=1, max_y=0, crs=CRS.EPSG_4326)


def test_bounding_box_intersects_and_reprojects() -> None:
    left = BoundingBox(min_x=0, min_y=0, max_x=1, max_y=1, crs=CRS.EPSG_4326)
    right = BoundingBox(min_x=0.5, min_y=0.5, max_x=2, max_y=2, crs=CRS.EPSG_4326)
    separate = BoundingBox(min_x=5, min_y=5, max_x=6, max_y=6, crs=CRS.EPSG_4326)

    assert left.intersects(right) is True
    assert left.intersects(separate) is False

    reprojected = left.to_crs(CRS.EPSG_3857)
    assert reprojected.crs == CRS.EPSG_3857
    assert reprojected.max_x > reprojected.min_x

    utm = left.to_crs(CRS.parse("EPSG:32632"))
    assert utm.crs == "EPSG:32632"
    assert utm.max_x > utm.min_x


def test_bounding_box_attrs_round_trip_nonstandard_crs() -> None:
    bbox = BoundingBox(
        min_x=500000, min_y=6500000, max_x=501000, max_y=6501000, crs="EPSG:25833"
    )
    assert bbox.crs.value == "EPSG:25833"
    dumped = bbox.model_dump()
    assert dumped["crs"] == "EPSG:25833"
    restored = BoundingBox.model_validate(dumped)
    assert restored.crs == CRS.parse("EPSG:25833")


def test_temporal_extent_validation() -> None:
    valid = TemporalExtent(
        start_time=datetime(2020, 1, 1),
        end_time=datetime(2020, 12, 31),
    )
    assert valid.end_time is not None

    with pytest.raises(ValueError, match="end_time must be after start_time"):
        TemporalExtent(
            start_time=datetime(2021, 1, 1),
            end_time=datetime(2020, 1, 1),
        )


def test_format_enum_values() -> None:
    assert Format.GEOTIFF.value == "image/tiff"
    assert Format.PNG.value == "image/png"
