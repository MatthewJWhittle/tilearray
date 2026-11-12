"""Public Create/Compute surfaces raise TileArrayError — not bare pydantic/PIL."""

from __future__ import annotations

import httpx
import pytest
import respx

from tilearray import TileArrayError, XYZConfig, compute_mosaic, create_array
from tilearray.errors import ValidationError

pytestmark = pytest.mark.unit


def test_zero_area_bbox_is_validation_error() -> None:
    cfg = XYZConfig.from_url("https://tiles.example.com/{z}/{x}/{y}.png", zoom=3)
    with pytest.raises(ValidationError) as ei:
        create_array(cfg, (1, 1, 1, 1), bbox_crs="EPSG:4326")
    assert isinstance(ei.value, TileArrayError)


def test_inverted_bbox_is_validation_error() -> None:
    cfg = XYZConfig.from_url("https://tiles.example.com/{z}/{x}/{y}.png", zoom=3)
    with pytest.raises(ValidationError) as ei:
        create_array(cfg, (10, 0, 0, 10), bbox_crs="EPSG:4326")
    assert isinstance(ei.value, TileArrayError)


def test_json_200_body_is_tilearray_error() -> None:
    cfg = XYZConfig.from_url("https://tiles.example.com/{z}/{x}/{y}.png", zoom=3)
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__regex=r".*").mock(
            return_value=httpx.Response(
                200,
                json={"error": "quota"},
                headers={"content-type": "application/json"},
            )
        )
        with pytest.raises(TileArrayError):
            compute_mosaic(create_array(cfg, (0, 0, 10, 10), bbox_crs="EPSG:4326"))
