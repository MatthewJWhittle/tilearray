"""S3 — many custom-extent patches via ordinary bbox loop (#97 → #87/#61)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tilearray.array import create_array
from tilearray.fetch import TileFetcher
from tilearray.service.base import BaseService, TileGeometry
from tilearray.types import (
    CRS,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

from .catalog import scenario_by_id

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "s3_multi_bbox"


@pytest.fixture(autouse=True)
def reset_fetcher_instances() -> Any:
    TileFetcher.reset_instances()
    yield
    TileFetcher.reset_instances()


def _decoder(response: TileResponse, request: TileRequest) -> np.ndarray:
    height = request.height or 16
    width = request.width or 16
    return np.full((height, width), 7.0, dtype=np.float32)


def test_s3_ordinary_loop_yields_georeferenced_arrays_and_reuses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert scenario_by_id("S3")["baseline"] == "pass"
    payload = json.loads((FIXTURE / "bboxes.json").read_text(encoding="utf-8"))
    chunk = tuple(payload["chunk_size"])
    cache_dir = tmp_path / "tiles"
    fetch_urls: list[str] = []

    class PatchService(BaseService):
        service_type = ServiceTypeEnum.XYZ
        output_format = Format.PNG

        def __init__(self) -> None:
            super().__init__("https://example.invalid/tiles")

        def generate_tile_requests(
            self,
            bbox: BoundingBox,
            chunk_size: tuple[int, int],
            **options: Any,
        ) -> list[TileRequest]:
            width, height = chunk_size
            # Quantise URL to a coarse cell so overlapping AOIs can share cache keys.
            cell_x = round(bbox.min_x, 2)
            cell_y = round(bbox.min_y, 2)
            return [
                TileRequest(
                    url=f"https://example.invalid/tiles/{cell_x}_{cell_y}.png",
                    params={},
                    output_format=Format.PNG,
                    crs=bbox.crs,
                    bbox=bbox,
                    width=width,
                    height=height,
                )
            ]

        def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
            raise AssertionError("unused")

    def fake_fetch(request: TileRequest, **kwargs: Any) -> TileResponse:
        fetch_urls.append(request.url)
        return TileResponse(
            data=b"png-bytes",
            content_type="image/png",
            status_code=200,
            headers={},
            url=request.url,
            success=True,
            error_message=None,
        )

    import tilearray.array as array_module
    import tilearray.decode as decode_module

    monkeypatch.setattr(array_module, "get_service", lambda *a, **k: PatchService())
    monkeypatch.setattr(array_module, "fetch_tile", fake_fetch)
    decode_module.register_tile_decoder(Format.PNG, _decoder)

    arrays = []
    for item in payload["bboxes"]:
        bbox = BoundingBox.from_tuple(tuple(item["bbox"]), crs=CRS.EPSG_4326)
        array = create_array(
            service_url="https://example.invalid/tiles/{z}/{x}/{y}.png",
            bbox=bbox,
            crs=CRS.EPSG_4326,
            chunk_size=chunk,
            grid_shape=(1, 1),
            cache_dir=cache_dir,
            show_progress=False,
        )
        computed = array.compute()
        arrays.append(computed)
        assert computed.shape[-2:] == (chunk[1], chunk[0])
        # Pixel-centre x should sit inside the requested bbox.
        assert bbox.min_x < float(computed.coords["x"].values[0]) < bbox.max_x

    assert len(arrays) == 3
    assert cache_dir.exists()
    assert list(cache_dir.glob("*.tile")), "expected disk cache entries"
    # After warm cache, recompute first AOI adds no new fetch.
    cold_count = len(fetch_urls)
    first_bbox = BoundingBox.from_tuple(
        tuple(payload["bboxes"][0]["bbox"]), crs=CRS.EPSG_4326
    )
    create_array(
        service_url="https://example.invalid/tiles/{z}/{x}/{y}.png",
        bbox=first_bbox,
        crs=CRS.EPSG_4326,
        chunk_size=chunk,
        grid_shape=(1, 1),
        cache_dir=cache_dir,
        show_progress=False,
    ).compute()
    assert len(fetch_urls) == cold_count
