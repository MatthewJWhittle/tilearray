# pyright: reportMissingImports=false, reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false

"""High-level array construction utilities built on service abstractions."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import tempfile
import warnings
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    cast,
)

import numpy as np
import xarray as xr
from dask.array import block as da_block
from dask.array import from_delayed as da_from_delayed
from dask.delayed import Delayed, delayed
from geotiff import GeoTiff  # type: ignore[import-untyped]
from geotiff.geotiff import TiffFile  # type: ignore[import-untyped]
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field

from .fetch import FetchPolicy, FetchProgress, ProgressCallback
from .service import get_service
from .service.base import BaseService
from .service.config import ServiceConfig as ServiceConfigModel
from .tiles import fetch_tile
from .types import (
    CRS,
    BBoxTuple,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
    TileResponse,
)

try:  # pragma: no cover - optional dependency
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover - optional dependency
    _PILImage = None

try:  # pragma: no cover - optional dependency
    import imageio.v2 as _imageio  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency
    _imageio = None

if TYPE_CHECKING:
    from dask.array.core import Array as DaskArray
else:  # pragma: no cover - typing aid
    DaskArray = Any

NDArrayFloat = NDArray[np.floating[Any]]
TileDecoder = Callable[[TileResponse, TileRequest], NDArrayFloat]

_DECODER_REGISTRY: dict[Format, TileDecoder] = {}


def register_tile_decoder(fmt: Format, decoder: TileDecoder) -> None:
    """Register a tile decoder for a particular output format."""

    _DECODER_REGISTRY[fmt] = decoder


def _decoder_for_format(fmt: Format | str | None) -> TileDecoder | None:
    if isinstance(fmt, Format):
        return _DECODER_REGISTRY.get(fmt)
    if isinstance(fmt, str):
        try:
            fmt_enum = Format(fmt)
        except ValueError:
            return None
        return _DECODER_REGISTRY.get(fmt_enum)
    return None


class ArrayRequest(BaseModel):
    service_config: ServiceConfigModel | None = None
    service_url: str
    bbox: BoundingBox
    target_crs: CRS
    chunk_size: tuple[int, int]
    grid_shape: tuple[int, int]
    resolution: tuple[float, float] | None = None
    output_format: Format | None = None
    cache_dir: Path | None = None
    service_options: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @property
    def chunk_height(self) -> int:
        return self.chunk_size[0]

    @property
    def chunk_width(self) -> int:
        return self.chunk_size[1]

    @property
    def chunk_pixels(self) -> tuple[int, int]:
        return self.chunk_width, self.chunk_height

    @property
    def cache_path(self) -> Path | None:
        return self.cache_dir

    @property
    def service_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {}
        if self.service_config is not None:
            return kwargs
        kwargs.update(self.service_options)
        kwargs.setdefault("crs", self.target_crs)
        if self.output_format is not None:
            kwargs.setdefault("output_format", self.output_format)
        return kwargs

    @classmethod
    def from_inputs(
        cls,
        *,
        service_url: str,
        service_config: ServiceConfigModel | None,
        bbox_input: BoundingBox | BBoxTuple,
        crs_input: CRS | str | int,
        chunk_size_input: tuple[int, int] | None,
        grid_shape_input: tuple[int, int] | None,
        output_format_input: Format | None,
        cache_dir_input: str | Path | None,
        service_options_input: dict[str, Any],
    ) -> ArrayRequest:
        target_crs = _coerce_crs(crs_input)
        normalized_bbox = _normalize_bbox(bbox_input, target_crs)

        defaults = service_config.array_defaults() if service_config else {}

        chunk_candidate = chunk_size_input or defaults.get("chunk_size") or (256, 256)
        chunk_height, chunk_width = _validate_chunk_size(chunk_candidate)

        user_options = dict(service_options_input)
        user_resolution = user_options.pop("resolution", None)
        resolution = user_resolution or defaults.get("resolution")

        fallback_grid = defaults.get("grid_shape")
        grid = _resolve_grid_shape(grid_shape_input, fallback_grid)
        if (
            grid_shape_input is None
            and fallback_grid is None
            and resolution is not None
        ):
            res_x, res_y = resolution
            if res_x <= 0 or res_y <= 0:
                raise ValueError("resolution values must be positive")
            span_x = normalized_bbox.max_x - normalized_bbox.min_x
            span_y = normalized_bbox.max_y - normalized_bbox.min_y
            cols = (
                max(1, int(math.ceil(span_x / (chunk_width * res_x))))
                if chunk_width > 0
                else 1
            )
            rows = (
                max(1, int(math.ceil(span_y / (chunk_height * res_y))))
                if chunk_height > 0
                else 1
            )
            grid = (rows, cols)

        cache_candidate = cache_dir_input or defaults.get("cache_dir")
        cache_path = (
            Path(cache_candidate).expanduser().resolve() if cache_candidate else None
        )

        effective_format = output_format_input
        if (
            effective_format is None
            and service_config
            and service_config.output_format is not None
        ):
            effective_format = service_config.output_format

        return cls(
            service_config=service_config,
            service_url=service_url,
            bbox=normalized_bbox,
            target_crs=target_crs,
            chunk_size=(chunk_height, chunk_width),
            grid_shape=grid,
            resolution=resolution,
            output_format=effective_format,
            cache_dir=cache_path,
            service_options=user_options,
        )

    def build_service(self, service_type: ServiceTypeEnum | None) -> BaseService:
        if self.service_config is not None:
            return self.service_config.build_service()
        return get_service(
            self.service_url, service_type=service_type, **self.service_kwargs
        )

    def effective_format(self, service: BaseService) -> Format | None:
        if self.output_format is not None:
            return self.output_format
        service_format = getattr(service, "output_format", None)
        if isinstance(service_format, Format):
            return service_format
        if isinstance(service_format, str):
            try:
                return Format(service_format)
            except ValueError:
                return None
        return None

    def tile_options(self) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if self.service_config is not None:
            options.update(self.service_config.tile_kwargs())
        options.update(self.service_options)
        options.setdefault("crs", self.target_crs)
        if self.output_format is not None:
            options.setdefault("output_format", self.output_format)
        options["grid_shape"] = self.grid_shape
        if self.resolution is not None:
            options.setdefault("resolution", self.resolution)
        return options

    def plan_tile_requests(
        self, service: BaseService
    ) -> tuple[list[TileRequest], dict[str, Any]]:
        tile_options = self.tile_options()
        tile_requests_iter = service.generate_tile_requests(
            self.bbox,
            self.chunk_pixels,
            **dict(tile_options),
        )
        tile_requests = list(tile_requests_iter)

        inferred_grid = getattr(service, "inferred_grid_shape", None)
        if (
            inferred_grid is not None
            and self.grid_shape == (1, 1)
            and len(tile_requests) > 1
        ):
            self.grid_shape = inferred_grid

        return tile_requests, tile_options

    def array_attrs(
        self,
        service: BaseService,
        tile_options: dict[str, Any],
        effective_format: Format | None,
    ) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "crs": self.target_crs.value,
            "service_url": self.service_url,
            "service_type": service.service_type.value,
        }
        if effective_format is not None:
            attrs["output_format"] = effective_format.value
        coverage_id = tile_options.get("coverage_id") or getattr(
            service, "coverage_id", None
        )
        if coverage_id:
            attrs["coverage_id"] = coverage_id
        return attrs


def _resolve_decoder(
    request: ArrayRequest, service: BaseService, explicit: TileDecoder | None
) -> TileDecoder:
    if explicit is not None:
        return explicit

    fmt = request.effective_format(service)
    if fmt is not None:
        decoder = _decoder_for_format(fmt)
        if decoder is not None:
            return decoder

    raise RuntimeError(
        f"No tile decoder available for format {fmt!r}; please provide tile_decoder."
    )


def _delayed_call(func: Callable[..., Any], *args: Any) -> Delayed:
    """Typed helper around ``dask.delayed`` to satisfy static analysis."""

    return cast(Delayed, delayed(func)(*args))


def _organize_tiles(
    tile_requests: Sequence[TileRequest],
    rows: int,
    cols: int,
) -> list[list[TileRequest]]:
    if len(tile_requests) != rows * cols:
        raise ValueError(
            f"Service produced {len(tile_requests)} tile requests; expected {rows * cols}"
        )

    annotated: list[tuple[TileRequest, BoundingBox]] = []
    for request in tile_requests:
        if request.bbox is None:
            raise ValueError("TileRequest is missing spatial metadata (bbox)")
        annotated.append((request, request.bbox))

    sorted_tiles = [
        request
        for request, _ in sorted(
            annotated,
            key=lambda item: (-item[1].max_y, item[1].min_x),
        )
    ]

    grid: list[list[TileRequest]] = []
    idx = 0
    for _ in range(rows):
        row_tiles: list[TileRequest] = []
        for _ in range(cols):
            row_tiles.append(sorted_tiles[idx])
            idx += 1
        grid.append(row_tiles)
    return grid


def _resolve_fetch_policy(
    service_config: ServiceConfigModel | None,
    service_options: dict[str, Any],
) -> FetchPolicy:
    base = (
        service_config.fetch_policy() if service_config is not None else FetchPolicy()
    )
    return FetchPolicy(
        max_concurrent=service_options.pop(
            "max_concurrent_requests", base.max_concurrent
        ),
        max_connections=service_options.pop("max_connections", base.max_connections),
        retries=service_options.pop("fetch_retries", base.retries),
        timeout=service_options.pop("fetch_timeout", base.timeout),
        rate_limit_per_second=service_options.pop(
            "rate_limit_per_second", base.rate_limit_per_second
        ),
        rate_limiter=service_options.pop("rate_limiter", base.rate_limiter),
        adaptive_concurrency=service_options.pop(
            "adaptive_concurrency", base.adaptive_concurrency
        ),
        initial_concurrent=service_options.pop(
            "initial_concurrent_requests", base.initial_concurrent
        ),
        min_concurrent=service_options.pop(
            "min_concurrent_requests", base.min_concurrent
        ),
    )


def create_array(
    service_url: str | ServiceConfigModel,
    bbox: BoundingBox | BBoxTuple,
    crs: CRS | str | int,
    *,
    service_type: ServiceTypeEnum | None = None,
    chunk_size: tuple[int, int] | None = None,
    grid_shape: tuple[int, int] | None = None,
    output_format: Format | None = None,
    cache_dir: str | Path | None = None,
    compute: bool = False,
    dtype: str | np.dtype[Any] = np.dtype("float32"),
    tile_decoder: TileDecoder | None = None,
    on_progress: ProgressCallback | None = None,
    **service_options: Any,
) -> xr.DataArray:
    """Create an xarray ``DataArray`` backed by Dask from a remote service."""

    target_crs = _coerce_crs(crs)
    normalized_bbox = _normalize_bbox(bbox, target_crs)

    service_config = (
        service_url if isinstance(service_url, ServiceConfigModel) else None
    )
    base_service_url = (
        service_config.base_url if service_config else cast(str, service_url)
    )

    if (
        service_config
        and service_type is not None
        and service_type != service_config.service_type
    ):
        raise ValueError(
            "Provided service_type does not match the ServiceConfig service_type"
        )

    options = dict(service_options)
    fetch_policy = _resolve_fetch_policy(service_config, options)

    request = ArrayRequest.from_inputs(
        service_url=base_service_url,
        service_config=service_config,
        bbox_input=bbox,
        crs_input=crs,
        chunk_size_input=chunk_size,
        grid_shape_input=grid_shape,
        output_format_input=output_format,
        cache_dir_input=cache_dir,
        service_options_input=options,
    )

    service = request.build_service(service_type)
    decoder = _resolve_decoder(request, service, tile_decoder)

    tile_requests, tile_options = request.plan_tile_requests(service)

    rows, cols = request.grid_shape
    tile_grid = _organize_tiles(tile_requests, rows, cols)

    cache_path = request.cache_path
    chunk_height, chunk_width = request.chunk_size
    dtype_np = np.dtype(dtype)
    progress = FetchProgress(total=len(tile_requests), on_progress=on_progress)

    blocks: list[list[DaskArray]] = []
    for row_tiles in tile_grid:
        row_blocks: list[DaskArray] = []
        for tile_request in row_tiles:
            height = tile_request.height or chunk_height
            width = tile_request.width or chunk_width
            delayed_tile = _delayed_call(
                _load_tile_array,
                tile_request,
                cache_path,
                decoder,
                dtype_np,
                fetch_policy,
                progress,
            )
            row_blocks.append(
                da_from_delayed(
                    delayed_tile,
                    shape=(height, width),
                    dtype=dtype_np,
                )
            )
        blocks.append(row_blocks)

    data = da_block(blocks)

    effective_format = request.effective_format(service)
    attrs = request.array_attrs(service, tile_options, effective_format)

    normalized_bbox = request.bbox
    target_crs = request.target_crs
    y_coords = np.linspace(normalized_bbox.min_y, normalized_bbox.max_y, data.shape[0])
    x_coords = np.linspace(normalized_bbox.min_x, normalized_bbox.max_x, data.shape[1])

    data_array = xr.DataArray(
        data,
        coords={"y": y_coords, "x": x_coords},
        dims=("y", "x"),
        attrs=attrs,
    )

    if compute:
        computed = data_array.compute()
        if progress.errors:
            warnings.warn(
                f"{len(progress.errors)} tile fetch(es) failed; "
                "failed regions are filled with NaN. "
                f"First error: {progress.errors[0]}",
                stacklevel=2,
            )
        return computed
    return data_array


def load_array(*args: Any, compute: bool = True, **kwargs: Any) -> xr.DataArray:
    """Convenience wrapper around :func:`create_array`."""

    return create_array(*args, compute=compute, **kwargs)


def _coerce_crs(crs: CRS | str | int) -> CRS:
    if isinstance(crs, CRS):
        return crs
    if isinstance(crs, str):
        crs_upper = crs.upper()
        return (
            CRS.from_epsg(crs_upper)
            if crs_upper.startswith("EPSG:")
            else CRS.from_integer(int(crs))
        )
    return CRS.from_integer(crs)


def _normalize_bbox(bbox: BoundingBox | BBoxTuple, crs: CRS) -> BoundingBox:
    if isinstance(bbox, BoundingBox):
        return bbox if bbox.crs == crs else bbox.to_crs(crs)
    return BoundingBox.from_tuple(bbox, crs)


def _resolve_grid_shape(
    explicit: tuple[int, int] | None,
    fallback: tuple[int, int] | None,
) -> tuple[int, int]:
    if explicit is not None:
        return _validate_grid_shape(explicit)
    if fallback is not None:
        return _validate_grid_shape(fallback)
    return (1, 1)


def _validate_grid_shape(grid: tuple[int, int]) -> tuple[int, int]:
    rows, cols = grid
    if rows <= 0 or cols <= 0:
        raise ValueError("grid_shape must contain positive integers")
    return rows, cols


def _validate_chunk_size(chunk_size: tuple[int, int]) -> tuple[int, int]:
    width, height = chunk_size
    if width <= 0 or height <= 0:
        raise ValueError("chunk_size dimensions must be positive integers")
    return height, width


def _resize_tile_array(
    array: NDArrayFloat, target_height: int, target_width: int
) -> NDArrayFloat:
    actual_height, actual_width = array.shape

    if actual_height == target_height and actual_width == target_width:
        return array

    if target_height <= 0 or target_width <= 0:
        raise ValueError("target dimensions must be positive")

    if actual_height < target_height or actual_width < target_width:
        raise ValueError(
            f"Decoded tile has shape {(actual_height, actual_width)}, expected at least {(target_height, target_width)}"
        )

    if actual_height % target_height == 0 and actual_width % target_width == 0:
        factor_y = actual_height // target_height
        factor_x = actual_width // target_width
        reshaped = array.reshape(target_height, factor_y, target_width, factor_x)
        return cast(NDArrayFloat, np.nanmean(reshaped, axis=(1, 3)))

    y_edges = np.linspace(0, actual_height, target_height + 1, dtype=int)
    x_edges = np.linspace(0, actual_width, target_width + 1, dtype=int)
    y_edges[0] = 0
    y_edges[-1] = actual_height
    x_edges[0] = 0
    x_edges[-1] = actual_width

    resized = np.empty((target_height, target_width), dtype=array.dtype)
    for row in range(target_height):
        y_start, y_end = y_edges[row], y_edges[row + 1]
        for col in range(target_width):
            x_start, x_end = x_edges[col], x_edges[col + 1]
            block = array[y_start:y_end, x_start:x_end]
            resized[row, col] = np.nanmean(block) if block.size else np.nan
    return resized


def _load_tile_array(
    request: TileRequest,
    cache_dir: Path | None,
    decoder: TileDecoder,
    dtype: np.dtype[Any],
    fetch_policy: FetchPolicy | None = None,
    progress: FetchProgress | None = None,
) -> NDArrayFloat:
    response = _fetch_with_cache(request, cache_dir, fetch_policy)
    if progress is not None:
        progress.tick(request, response)
    if not response.success:
        height = request.height or 0
        width = request.width or 0
        return np.full((height, width), np.nan, dtype=dtype)

    array = decoder(response, request)
    if array.ndim != 2:
        raise ValueError("tile_decoder must return a 2D array")

    target_height = request.height or array.shape[0]
    target_width = request.width or array.shape[1]

    array = _resize_tile_array(array, target_height, target_width)

    return np.asarray(array, dtype=dtype)


def _fetch_with_cache(
    request: TileRequest,
    cache_dir: Path | None,
    fetch_policy: FetchPolicy | None = None,
) -> TileResponse:
    if cache_dir is not None:
        cached = _read_cache(cache_dir, request)
        if cached is not None:
            return TileResponse(
                data=cached,
                content_type=request.output_format.value
                if request.output_format
                else "",
                status_code=200,
                headers={},
                url=request.url,
                success=True,
                error_message=None,
            )

    response = fetch_tile(request, policy=fetch_policy)
    if cache_dir is not None and response.success:
        cached_data = bytes(response.data)
        if cached_data:
            _write_cache(cache_dir, request, cached_data)
    return response


def _cache_key(request: TileRequest) -> str:
    payload = {
        "url": request.url,
        "params": sorted((str(k), str(v)) for k, v in request.params.items()),
        "format": request.output_format.value if request.output_format else None,
        "bbox": request.bbox.model_dump() if request.bbox else None,
        "width": request.width,
        "height": request.height,
    }
    serialized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _read_cache(cache_dir: Path, request: TileRequest) -> bytes | None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(request)}.tile"
    return path.read_bytes() if path.exists() else None


def _write_cache(cache_dir: Path, request: TileRequest, data: bytes) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{_cache_key(request)}.tile"
    path.write_bytes(data)


def _read_geotiff_array(path: str) -> NDArrayFloat:
    """Read raster values from a GeoTIFF file on disk."""

    tifffile_logger = logging.getLogger("tifffile")
    previous_level = tifffile_logger.level
    tifffile_logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            with TiffFile(path) as tif_file:
                data = np.asarray(tif_file.asarray(), dtype=np.float64)
                page = tif_file.pages[0]
                nodata_tag = page.tags.get("GDAL_NODATA")
                if nodata_tag is not None:
                    try:
                        nodata = float(nodata_tag.value)
                    except (TypeError, ValueError):
                        nodata = None
                    if nodata is not None and np.isfinite(nodata):
                        data[data == nodata] = np.nan
    finally:
        tifffile_logger.setLevel(previous_level)

    if data.ndim > 2:
        data = data[0]

    invalid = ~np.isfinite(data)
    sentinel = np.abs(data) > 1e20
    if invalid.any() or sentinel.any():
        data = data.copy()
        data[invalid | sentinel] = np.nan
    return cast(NDArrayFloat, np.asarray(data, dtype=np.float32))


def _decode_geotiff(response: TileResponse, request: TileRequest) -> NDArrayFloat:
    raw_bytes = bytes(response.data)
    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        tmp.write(raw_bytes)
        tmp.flush()
        try:
            return _read_geotiff_array(tmp.name)
        except Exception:  # pragma: no cover - fallback path
            tif = GeoTiff(tmp.name, as_crs=None)
            data = cast(NDArrayFloat, np.asarray(tif.read(), dtype=np.float32))
            if data.ndim > 2:
                data = data[0]
            invalid = ~np.isfinite(data)
            sentinel = np.abs(data) > 1e20
            if invalid.any() or sentinel.any():
                data = data.copy()
                data[invalid | sentinel] = np.nan
            return np.asarray(data, dtype=np.float32)


def _decode_raster_image(response: TileResponse, request: TileRequest) -> NDArrayFloat:
    raw_bytes = bytes(response.data)
    data: NDArrayFloat | None = None

    if _PILImage is not None:  # pragma: no cover - depends on optional library
        with BytesIO(raw_bytes) as bio:
            with _PILImage.open(bio) as img:
                data = cast(NDArrayFloat, np.asarray(img))
    elif _imageio is not None:  # pragma: no cover
        with BytesIO(raw_bytes) as bio:
            data = cast(NDArrayFloat, np.asarray(_imageio.imread(bio)))

    if data is None:  # pragma: no cover
        msg = (
            "PNG/JPEG decoding requires Pillow or imageio. "
            "Install one of these packages or provide a custom tile_decoder."
        )
        raise RuntimeError(msg)

    if data.ndim == 3:
        data = data[..., 0]

    return cast(NDArrayFloat, np.asarray(data, dtype=np.float32))


register_tile_decoder(Format.GEOTIFF, _decode_geotiff)
if _PILImage is not None or _imageio is not None:  # pragma: no cover - registration
    register_tile_decoder(Format.PNG, _decode_raster_image)
    register_tile_decoder(Format.JPEG, _decode_raster_image)
