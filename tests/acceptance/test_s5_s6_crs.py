"""S5/S6 — WCS 2.0.1 subsets and PDOK EPGS typo (#97 → #92/#88/#96)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tilearray.errors import ValidationError
from tilearray.service.wcs import WCSParser, WCSService
from tilearray.service.wms import WMSService
from tilearray.types import CRS, BoundingBox, Format

from .catalog import scenario_by_id

pytestmark = pytest.mark.unit

S5 = Path(__file__).resolve().parent / "fixtures" / "s5_wcs_201"
S6 = Path(__file__).resolve().parent / "fixtures" / "s6_pdok_wms"


def test_s5_fake_capabilities_parse_and_crs_unsupported() -> None:
    assert scenario_by_id("S5")["baseline"] == "unsupported"
    expected = json.loads((S5 / "expected_request.json").read_text(encoding="utf-8"))
    xml = (S5 / "capabilities.xml").read_text(encoding="utf-8")
    caps = WCSParser("https://example.invalid/geoserver/wcs").parse_get_capabilities(
        xml
    )
    assert caps.coverages[0].identifier == expected["coverage_id"]
    assert any(fmt == Format.GEOTIFF for fmt in caps.supported_formats)

    with pytest.raises((ValueError, ValidationError)):
        CRS.from_string(str(expected["crs"]))


def test_s5_getcoverage_request_shape_with_supported_crs_standin() -> None:
    """Until #92, exercise subset/width/height/MIME with an enum CRS stand-in."""

    expected = json.loads((S5 / "expected_request.json").read_text(encoding="utf-8"))
    service = WCSService(
        "https://example.invalid/geoserver/wcs",
        coverage_id=str(expected["coverage_id"]),
        output_format=Format.GEOTIFF,
        crs=CRS.EPSG_27700,
        version=str(expected["version"]),
    )
    bbox = BoundingBox.from_tuple(
        tuple(expected["subset_bbox_epsg32632"]),
        crs=CRS.EPSG_27700,
    )
    from tilearray.service.base import TileGeometry

    request = service.build_tile_request(
        TileGeometry(
            bbox=bbox,
            width=int(expected["width"]),
            height=int(expected["height"]),
            crs=CRS.EPSG_27700,
        )
    )
    assert request.params["width"] == str(expected["width"])
    assert request.params["height"] == str(expected["height"])
    assert request.output_format == Format.GEOTIFF
    assert isinstance(request.params.get("subset"), list)
    assert request.params["subset"]


def test_s6_epgs_typo_rejected_early_without_rewrite() -> None:
    assert scenario_by_id("S6")["baseline"] == "user_config_error"
    params = json.loads((S6 / "params.json").read_text(encoding="utf-8"))

    with pytest.raises(ValidationError, match="Invalid CRS format") as exc_info:
        CRS.from_string(str(params["invalid_crs"]))
    message = str(exc_info.value)
    assert "EPGS" in message or "Invalid CRS" in message
    # Must not silently become EPSG:28992 / a valid enum member.
    assert "EPSG:28992" not in message or "EPGS" in message

    with pytest.raises((ValueError, ValidationError)):
        CRS.from_string(str(params["corrected_crs"]))


def test_s6_corrected_crs_still_unsupported_for_getmap_build() -> None:
    params = json.loads((S6 / "params.json").read_text(encoding="utf-8"))
    # Correct CRS string is still outside the enum — do not rewrite the typo.
    with pytest.raises((ValueError, ValidationError)):
        WMSService(
            "https://example.invalid/wms",
            layers=str(params["layer"]),
            crs=str(params["corrected_crs"]),
            output_format=Format.JPEG,
        )
