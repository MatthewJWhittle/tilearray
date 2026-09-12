"""Offline WCS contract tests backed by recorded HTTP (VCR cassettes).

Exercises GetCapabilities, DescribeCoverage, and GetCoverage against the
public EA Lidar DTM WCS endpoint using cassettes recorded once from live
responses. Default CI replays cassettes only — no outbound network.
"""

from __future__ import annotations

import pytest

from tilearray.service.wcs import WCSService
from tilearray.types import BoundingBox, CRS, Format

# Public EA Lidar DTM WCS — see example-sources.md
EA_LIDAR_WCS_URL = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)

# CoverageId from GetCapabilities (WCS 2.0 CoverageSummary/wcs:CoverageId)
ELEVATION_COVERAGE_ID = (
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
)

# Small 64×64 m subset in EPSG:27700 (England); matches integration smoke area
GET_COVERAGE_BBOX = BoundingBox(
    min_x=431900.0,
    min_y=382700.0,
    max_x=431964.0,
    max_y=382764.0,
    crs=CRS.EPSG_27700,
)
GET_COVERAGE_SIZE = (64, 64)


@pytest.mark.contract
@pytest.mark.vcr()
class TestEALidarWCSContract:
    """Protocol contract for EA Lidar DTM WCS (recorded HTTP)."""

    def test_get_capabilities(self):
        service = WCSService(EA_LIDAR_WCS_URL, crs=CRS.EPSG_4326)
        capabilities = service.get_capabilities()

        assert capabilities.service_url == EA_LIDAR_WCS_URL
        assert capabilities.coverages
        coverage_ids = {c.identifier for c in capabilities.coverages}
        assert ELEVATION_COVERAGE_ID in coverage_ids
        assert any(
            "Hillshade" in cid
            for cid in coverage_ids
            if cid != ELEVATION_COVERAGE_ID
        )

    def test_describe_coverage(self):
        service = WCSService(
            EA_LIDAR_WCS_URL,
            coverage_id=ELEVATION_COVERAGE_ID,
            crs=CRS.EPSG_4326,
        )
        description = service.describe_coverage(ELEVATION_COVERAGE_ID)

        assert description.identifier == ELEVATION_COVERAGE_ID
        assert description.spatial_extent is not None
        bbox = description.spatial_extent.bbox
        assert bbox is not None
        assert bbox.min_x < bbox.max_x
        assert bbox.min_y < bbox.max_y
        # England national DTM extent (EPSG:27700, metres)
        assert bbox.min_x >= 0
        assert bbox.max_x <= 700_000

    def test_get_coverage(self):
        service = WCSService(
            EA_LIDAR_WCS_URL,
            coverage_id=ELEVATION_COVERAGE_ID,
            output_format=Format.GEOTIFF,
            crs=CRS.EPSG_27700,
        )
        width, height = GET_COVERAGE_SIZE
        response = service.get_coverage(
            ELEVATION_COVERAGE_ID,
            GET_COVERAGE_BBOX,
            width,
            height,
            output_format=Format.GEOTIFF,
            crs=CRS.EPSG_27700,
        )

        assert response.success is True
        assert response.status_code == 200
        assert response.data is not None
        assert len(response.data) > 0
        # GeoTIFF magic bytes (little- or big-endian)
        assert response.data[:2] in (b"II", b"MM")
