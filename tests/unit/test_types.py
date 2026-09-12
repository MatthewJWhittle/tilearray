"""Unit tests for shared type models."""

from __future__ import annotations

from datetime import datetime

import pytest

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
    with pytest.raises(ValueError, match="Invalid CRS format"):
        CRS.from_string("4326")


def test_bounding_box_from_tuple_accepts_crs_variants() -> None:
    bbox = BoundingBox.from_tuple((0, 0, 1, 1), crs="EPSG:4326")
    assert bbox.crs == CRS.EPSG_4326

    bbox_int = BoundingBox.from_tuple((0, 0, 1, 1), crs=4326)
    assert bbox_int.crs == CRS.EPSG_4326

    bbox_enum = BoundingBox.from_tuple((0, 0, 1, 1), crs=CRS.EPSG_4326)
    assert bbox_enum.crs == CRS.EPSG_4326


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
