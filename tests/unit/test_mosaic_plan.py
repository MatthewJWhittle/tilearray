"""Contract tests for typed MosaicPlan (native grid vs query-window)."""

from __future__ import annotations

import pytest

from tilearray._planning import ArrayRequest
from tilearray.mosaic_plan import MosaicPlan, native_mosaic_meta
from tilearray.service.base import BaseService, TileGeometry
from tilearray.service.xyz import XYZService
from tilearray.types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest

pytestmark = pytest.mark.unit

OSM_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"


def _request_for(geom: TileGeometry) -> TileRequest:
    return TileRequest(
        url="https://example.com/tile",
        params={},
        output_format=Format.PNG,
        crs=geom.crs,
        bbox=geom.bbox,
        width=geom.width,
        height=geom.height,
    )


def test_native_mosaic_meta_none_without_tile_indices() -> None:
    geom = TileGeometry(
        bbox=BoundingBox(min_x=0.0, min_y=0.0, max_x=1.0, max_y=1.0, crs=CRS.EPSG_4326),
        width=256,
        height=256,
        crs=CRS.EPSG_4326,
    )
    assert native_mosaic_meta([geom]) is None


def test_mosaic_plan_from_planning_sets_union_and_grid() -> None:
    geoms = [
        TileGeometry(
            bbox=BoundingBox(
                min_x=0.0, min_y=10.0, max_x=10.0, max_y=20.0, crs=CRS.EPSG_3857
            ),
            width=256,
            height=256,
            crs=CRS.EPSG_3857,
            tile_x=0,
            tile_y=0,
        ),
        TileGeometry(
            bbox=BoundingBox(
                min_x=10.0, min_y=10.0, max_x=20.0, max_y=20.0, crs=CRS.EPSG_3857
            ),
            width=256,
            height=256,
            crs=CRS.EPSG_3857,
            tile_x=1,
            tile_y=0,
        ),
    ]
    plan = MosaicPlan.from_planning([_request_for(g) for g in geoms], geoms)
    assert plan.grid_shape == (1, 2)
    assert plan.mosaic_bbox is not None
    assert plan.mosaic_bbox.min_x == pytest.approx(0.0)
    assert plan.mosaic_bbox.max_x == pytest.approx(20.0)
    assert plan.mosaic_bbox.min_y == pytest.approx(10.0)
    assert plan.mosaic_bbox.max_y == pytest.approx(20.0)
    assert plan.mosaic_bbox.crs == CRS.EPSG_3857
    assert len(plan) == 2
    assert plan[0].bbox.min_x == pytest.approx(0.0)


def test_xyz_generate_tile_requests_returns_mosaic_plan() -> None:
    service = XYZService(OSM_TEMPLATE, zoom=12)
    bbox = BoundingBox(
        min_x=-0.2,
        min_y=51.4,
        max_x=0.2,
        max_y=51.6,
        crs=CRS.EPSG_4326,
    )
    plan = service.generate_tile_requests(bbox, (256, 256), zoom=12)
    assert isinstance(plan, MosaicPlan)
    assert plan.grid_shape is not None
    assert plan.mosaic_bbox is not None
    assert plan.mosaic_bbox.crs == CRS.EPSG_3857
    assert len(plan.tiles) == plan.grid_shape[0] * plan.grid_shape[1]


def test_query_window_service_plan_has_no_native_mosaic() -> None:
    class QueryService(BaseService):
        service_type = ServiceTypeEnum.WMS

        def build_tile_request(
            self, tile: TileGeometry, **options: object
        ) -> TileRequest:
            return _request_for(tile)

    service = QueryService("https://example.com/wms")
    bbox = BoundingBox(min_x=0.0, min_y=0.0, max_x=10.0, max_y=10.0, crs=CRS.EPSG_4326)
    plan = service.generate_tile_requests(bbox, (256, 256), grid_shape=(2, 2))
    assert isinstance(plan, MosaicPlan)
    assert len(plan.tiles) == 4
    assert plan.grid_shape is None
    assert plan.mosaic_bbox is None


def test_array_request_applies_mosaic_plan_without_service_attrs() -> None:
    service = XYZService(OSM_TEMPLATE, zoom=10)
    bbox = BoundingBox(
        min_x=10.0,
        min_y=69.5,
        max_x=10.4,
        max_y=69.8,
        crs=CRS.EPSG_4326,
    )
    request = ArrayRequest.from_inputs(
        service_url=OSM_TEMPLATE,
        service_config=None,
        bbox_input=bbox,
        bbox_crs_input=CRS.EPSG_4326,
        chunk_size_input=(256, 256),
        grid_shape_input=None,
        output_format_input=Format.PNG,
        cache_dir_input=None,
        service_options_input={"zoom": 10, "service_type": ServiceTypeEnum.XYZ},
    )
    assert not hasattr(service, "inferred_grid_shape")
    assert not hasattr(service, "inferred_mosaic_bbox")

    tiles, _options = request.plan_tile_requests(service)
    assert len(tiles) > 1
    assert request.grid_shape[0] * request.grid_shape[1] == len(tiles)
    assert request.bbox.crs == CRS.EPSG_3857
    assert request.target_crs == CRS.EPSG_3857
    assert request.requested_bbox is not None
    assert request.requested_bbox.crs == CRS.EPSG_4326
