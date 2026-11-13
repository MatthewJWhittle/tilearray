"""Offline: a WCS that hangs on scaleSize still finishes at native resolution (#177)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest
import respx

from tilearray import create_array
from tilearray.fetch_presets import EA_LIDAR_BENCH_BBOX
from tilearray.service.config import WCSConfig
from tilearray.types import CRS, Format

pytestmark = pytest.mark.unit

_FIXTURE_TIF = (
    Path(__file__).resolve().parents[1] / "data" / "wcs_tiles" / "ea_lidar_64x64.tif"
)

_DESCRIBE = """<?xml version="1.0" encoding="UTF-8"?>
<wcs:CoverageDescriptions xmlns:wcs="http://www.opengis.net/wcs/2.0"
 xmlns:gml="http://www.opengis.net/gml/3.2" xmlns:gmlcov="http://www.opengis.net/gmlcov/1.0">
  <wcs:CoverageDescription gml:id="hang-dem">
    <gml:boundedBy>
      <gml:Envelope srsName="http://www.opengis.net/def/crs/EPSG/0/27700"
        axisLabels="E N" uomLabels="m m" srsDimension="2">
        <gml:lowerCorner>0 0</gml:lowerCorner>
        <gml:upperCorner>1000000 1000000</gml:upperCorner>
      </gml:Envelope>
    </gml:boundedBy>
    <wcs:CoverageId>hang-dem</wcs:CoverageId>
    <gml:domainSet>
      <gml:RectifiedGrid gml:id="grid_hang" dimension="2">
        <gml:limits>
          <gml:GridEnvelope>
            <gml:low>0 0</gml:low>
            <gml:high>999999 999999</gml:high>
          </gml:GridEnvelope>
        </gml:limits>
        <gml:axisLabels>i j</gml:axisLabels>
        <gml:origin>
          <gml:Point gml:id="p0" srsName="http://www.opengis.net/def/crs/EPSG/0/27700">
            <gml:pos>0.5 999999.5</gml:pos>
          </gml:Point>
        </gml:origin>
        <gml:offsetVector srsName="http://www.opengis.net/def/crs/EPSG/0/27700">1.0 0.0</gml:offsetVector>
        <gml:offsetVector srsName="http://www.opengis.net/def/crs/EPSG/0/27700">0.0 -1.0</gml:offsetVector>
      </gml:RectifiedGrid>
    </gml:domainSet>
    <wcs:ServiceParameters>
      <wcs:nativeFormat>image/tiff</wcs:nativeFormat>
    </wcs:ServiceParameters>
  </wcs:CoverageDescription>
</wcs:CoverageDescriptions>
"""


@respx.mock
def test_create_array_native_finishes_when_server_hangs_on_scalesize() -> None:
    """Fake host hangs whenever scaleSize is present; native request must not hang."""
    tiff_bytes = _FIXTURE_TIF.read_bytes()
    seen_scalesize = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_scalesize
        params = dict(request.url.params)
        request_name = params.get("request", "")
        if request_name == "DescribeCoverage":
            return httpx.Response(200, text=_DESCRIBE)
        if request_name == "GetCoverage":
            if "scaleSize" in params:
                seen_scalesize = True
                # Hold the connection open like EA does on scaleSize at native.
                time.sleep(30)
                return httpx.Response(500, text="should not reach")
            return httpx.Response(
                200,
                content=tiff_bytes,
                headers={"content-type": "image/tiff"},
            )
        return httpx.Response(404, text=f"unexpected {request_name}")

    respx.route(host="hang.example.com").mock(side_effect=handler)

    config = WCSConfig.from_url(
        "https://hang.example.com/wcs",
        "hang-dem",
        crs=CRS.EPSG_27700,
        output_format=Format.GEOTIFF,
        chunk_size=(64, 64),
        resolution=(1.0, 1.0),
        fetch_timeout=5.0,
        fetch_retries=0,
    )

    started = time.perf_counter()
    result = create_array(
        config,
        EA_LIDAR_BENCH_BBOX,
        bbox_crs=CRS.EPSG_27700,
    )
    computed: Any = result.compute()
    elapsed = time.perf_counter() - started

    assert not seen_scalesize
    assert elapsed < 10.0
    assert computed.shape == (64, 64)
    assert np.isfinite(computed).any()
