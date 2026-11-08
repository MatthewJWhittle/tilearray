"""Place a decoded tile onto the request canvas (fit / clip / resize / reject).

Interface: decoded array + :class:`~tilearray.types.TileRequest` geometry
(+ response bytes for clipped GeoTIFF tags) → request-shaped array, or a
loud :class:`~tilearray.errors.ValidationError`. Owns place/resize/reject
orchestration; decode stays pixels-from-bytes.
"""

from __future__ import annotations

import logging
import math
import warnings
from dataclasses import dataclass
from io import BytesIO
from typing import cast

import numpy as np
import tifffile

from .decode import (
    NDArrayAny,
    ensure_band_last,
    geotiff_bytes_from_response,
)
from .errors import ValidationError
from .types import CRS, BoundingBox, TileRequest, TileResponse

# Absolute tolerance for comparing Affine scale / integer pixel offsets.
_AFFINE_ATOL = 1e-6


@dataclass(frozen=True)
class GeoTransform:
    """North-up Affine ``(a, b, c, d, e, f)`` mapping pixel ``(col, row)`` → ``(x, y)``."""

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (self.a, self.b, self.c, self.d, self.e, self.f)


@dataclass(frozen=True)
class GeoTIFFGeoref:
    """Georeferencing read from GeoTIFF tags (placement only; never mosaic CRS)."""

    transform: GeoTransform
    epsg: int | None = None


def _parse_geokey_epsg(page: object) -> int | None:
    """Extract EPSG code from GeoKeyDirectory when present (Projected or Geographic)."""

    tags = getattr(page, "tags", None)
    if tags is None:
        return None
    directory = tags.get("GeoKeyDirectoryTag") or tags.get("GeoKeyDirectory")
    if directory is None:
        return None
    values = tuple(int(v) for v in directory.value)
    if len(values) < 4:
        return None
    number_of_keys = values[3]
    entries = values[4:]
    # Each key: KeyID, TIFFTagLocation, Count, Value_Offset
    for index in range(0, min(len(entries), number_of_keys * 4), 4):
        key_id, location, _count, value_offset = entries[index : index + 4]
        if location != 0:
            continue
        # GeographicTypeGeoKey=2048, ProjectedCSTypeGeoKey=3072
        if key_id in (2048, 3072) and value_offset > 0:
            return int(value_offset)
    return None


def read_geotiff_georef(source: str | BytesIO | bytes) -> GeoTIFFGeoref | None:
    """
    Read north-up Affine (+ optional EPSG) from GeoTIFF tags.

    Requires ``ModelPixelScale`` + ``ModelTiepoint``. Rotated /
    ``ModelTransformation``-only rasters return ``None`` (caller must fail
    rather than guess). Used for clipped-coverage placement only — mosaic
    CRS/transform attrs stay request-planned.
    """

    if isinstance(source, (bytes, bytearray)):
        source = BytesIO(bytes(source))

    tifffile_logger = logging.getLogger("tifffile")
    previous_level = tifffile_logger.level
    tifffile_logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            with tifffile.TiffFile(source) as tif_file:
                page = tif_file.pages.first
                scale_tag = page.tags.get("ModelPixelScaleTag") or page.tags.get(
                    "ModelPixelScale"
                )
                tie_tag = page.tags.get("ModelTiepointTag") or page.tags.get(
                    "ModelTiepoint"
                )
                transform_tag = page.tags.get(
                    "ModelTransformationTag"
                ) or page.tags.get("ModelTransformation")
                if scale_tag is None or tie_tag is None:
                    return None
                if transform_tag is not None:
                    # Ambiguous when both scale/tiepoint and a full transform exist.
                    return None
                scale = tuple(float(v) for v in scale_tag.value)
                tie = tuple(float(v) for v in tie_tag.value)
                if len(scale) < 2 or len(tie) < 6:
                    return None
                scale_x, scale_y = scale[0], scale[1]
                if scale_x == 0.0 or scale_y == 0.0:
                    return None
                i, j, _k, x, y, _z = tie[:6]
                # North-up Affine from scale + tiepoint (GeoTIFF raster space).
                transform = GeoTransform(
                    a=scale_x,
                    b=0.0,
                    c=x - scale_x * i,
                    d=0.0,
                    e=-scale_y,
                    f=y + scale_y * j,
                )
                return GeoTIFFGeoref(transform=transform, epsg=_parse_geokey_epsg(page))
    finally:
        tifffile_logger.setLevel(previous_level)


def pixel_offsets_for_placement(
    *,
    request_transform: GeoTransform,
    response_transform: GeoTransform,
) -> tuple[int, int]:
    """
    Integer ``(row_offset, col_offset)`` placing response pixels on the request grid.

    Requires matching north-up resolution and an integer pixel shift. Raises
    :class:`ValidationError` when alignment would need resampling or rotation.
    """

    req = request_transform
    resp = response_transform
    if not (
        math.isclose(req.b, 0.0, abs_tol=_AFFINE_ATOL)
        and math.isclose(req.d, 0.0, abs_tol=_AFFINE_ATOL)
        and math.isclose(resp.b, 0.0, abs_tol=_AFFINE_ATOL)
        and math.isclose(resp.d, 0.0, abs_tol=_AFFINE_ATOL)
    ):
        raise ValidationError(
            "Clipped coverage response has a rotated Affine; "
            "tilearray will not resample or warp pixels to the request grid."
        )
    if not (
        math.isclose(req.a, resp.a, rel_tol=0.0, abs_tol=_AFFINE_ATOL)
        and math.isclose(req.e, resp.e, rel_tol=0.0, abs_tol=_AFFINE_ATOL)
    ):
        raise ValidationError(
            "Clipped coverage response resolution "
            f"(a={resp.a}, e={resp.e}) does not match the requested tile "
            f"(a={req.a}, e={req.e}); refusing to resample."
        )
    if req.a == 0.0 or req.e == 0.0:
        raise ValidationError("Requested tile Affine has zero scale")

    col_offset = (resp.c - req.c) / req.a
    row_offset = (resp.f - req.f) / req.e
    if not (
        math.isclose(col_offset, round(col_offset), abs_tol=_AFFINE_ATOL)
        and math.isclose(row_offset, round(row_offset), abs_tol=_AFFINE_ATOL)
    ):
        raise ValidationError(
            "Clipped coverage response is not aligned to the requested pixel grid "
            f"(row_offset={row_offset}, col_offset={col_offset})."
        )
    return int(round(row_offset)), int(round(col_offset))


def place_array_on_canvas(
    array: NDArrayAny,
    *,
    target_height: int,
    target_width: int,
    row_offset: int,
    col_offset: int,
    fill_value: float = float("nan"),
) -> NDArrayAny:
    """
    Copy ``array`` onto a target canvas at an integer offset; pad with ``fill_value``.

    Outside-coverage padding is legitimate nodata for a clipped WCS response —
    distinct from soft-fail fill of a whole failed tile.
    """

    array = ensure_band_last(array)
    if array.ndim == 2:
        canvas = np.full(
            (target_height, target_width),
            fill_value,
            dtype=np.result_type(array, np.float32),
        )
    elif array.ndim == 3:
        n_bands = array.shape[2]
        canvas = np.full(
            (target_height, target_width, n_bands),
            fill_value,
            dtype=np.result_type(array, np.float32),
        )
    else:
        raise ValidationError(
            "tile array must be 2D or 3D (y, x[, band]) for clipped placement"
        )

    src_row_start = max(0, -row_offset)
    src_col_start = max(0, -col_offset)
    dst_row_start = max(0, row_offset)
    dst_col_start = max(0, col_offset)
    src_row_end = min(array.shape[0], target_height - row_offset)
    src_col_end = min(array.shape[1], target_width - col_offset)
    if src_row_end <= src_row_start or src_col_end <= src_col_start:
        return cast(NDArrayAny, canvas)

    dst_row_end = dst_row_start + (src_row_end - src_row_start)
    dst_col_end = dst_col_start + (src_col_end - src_col_start)
    canvas[dst_row_start:dst_row_end, dst_col_start:dst_col_end, ...] = array[
        src_row_start:src_row_end, src_col_start:src_col_end, ...
    ]
    return cast(NDArrayAny, canvas)


def _request_geotransform(bbox: BoundingBox, height: int, width: int) -> GeoTransform:
    """North-up Affine for a request bbox + pixel shape (same convention as mosaic)."""

    if height < 1 or width < 1:
        raise ValidationError(f"Request shape must be positive; got {(height, width)}")
    pixel_width = (bbox.max_x - bbox.min_x) / width
    pixel_height = (bbox.max_y - bbox.min_y) / height
    return GeoTransform(
        a=pixel_width,
        b=0.0,
        c=bbox.min_x,
        d=0.0,
        e=-pixel_height,
        f=bbox.max_y,
    )


def _epsg_code_from_crs(crs: CRS | None) -> int | None:
    if crs is None:
        return None
    value = crs.value if hasattr(crs, "value") else str(crs)
    text = str(value).upper()
    if text.startswith("EPSG:"):
        try:
            return int(text.split(":", 1)[1])
        except ValueError:
            return None
    return None


def resize_tile_array(
    array: NDArrayAny, target_height: int, target_width: int
) -> NDArrayAny:
    """Downsample an oversize tile to the request shape (never upsample/stretch)."""

    if array.ndim == 3:
        resized_bands = [
            resize_tile_array(array[..., band], target_height, target_width)
            for band in range(array.shape[2])
        ]
        return cast(NDArrayAny, np.stack(resized_bands, axis=-1))

    actual_height, actual_width = array.shape

    if actual_height == target_height and actual_width == target_width:
        return array

    if target_height <= 0 or target_width <= 0:
        raise ValidationError("target dimensions must be positive")

    if actual_height < target_height or actual_width < target_width:
        raise ValidationError(
            f"Decoded tile has shape {(actual_height, actual_width)}, "
            f"expected at least {(target_height, target_width)}. "
            "Clipped GeoTIFF responses need ModelPixelScale/ModelTiepoint "
            "tags so pixels can be placed without stretching."
        )

    # Downsample in float space so nanmean accepts integer source tiles.
    float_array = np.asarray(array, dtype=np.float64)
    if actual_height % target_height == 0 and actual_width % target_width == 0:
        factor_y = actual_height // target_height
        factor_x = actual_width // target_width
        reshaped = float_array.reshape(target_height, factor_y, target_width, factor_x)
        return cast(NDArrayAny, np.nanmean(reshaped, axis=(1, 3)))

    y_edges = np.linspace(0, actual_height, target_height + 1, dtype=int)
    x_edges = np.linspace(0, actual_width, target_width + 1, dtype=int)
    y_edges[0] = 0
    y_edges[-1] = actual_height
    x_edges[0] = 0
    x_edges[-1] = actual_width

    resized = np.empty((target_height, target_width), dtype=np.float64)
    for row in range(target_height):
        y_start, y_end = y_edges[row], y_edges[row + 1]
        for col in range(target_width):
            x_start, x_end = x_edges[col], x_edges[col + 1]
            block = float_array[y_start:y_end, x_start:x_end]
            resized[row, col] = np.nanmean(block) if block.size else np.nan
    return cast(NDArrayAny, resized)


def _place_clipped_geotiff_tile(
    array: NDArrayAny,
    request: TileRequest,
    response: TileResponse,
    *,
    fill_value: float = float("nan"),
) -> NDArrayAny:
    """
    Place a smaller-than-requested GeoTIFF onto the request canvas using tags.

    Pads uncovered cells with ``fill_value`` (coverage nodata, not soft-fail).
    """

    target_height = request.height
    target_width = request.width
    if (
        request.bbox is None
        or target_height is None
        or target_width is None
        or target_height < 1
        or target_width < 1
    ):
        raise ValidationError(
            "Clipped coverage placement requires tile bbox, width, and height"
        )

    tiff_bytes = geotiff_bytes_from_response(response)
    if tiff_bytes[:2] not in (b"II", b"MM"):
        raise ValidationError(
            f"Decoded tile has shape {array.shape[:2]}, expected "
            f"{(target_height, target_width)}, and the response is not a GeoTIFF "
            "with georeferencing tags for exact placement."
        )
    georef = read_geotiff_georef(tiff_bytes)
    if georef is None:
        raise ValidationError(
            f"Decoded tile has shape {array.shape[:2]}, expected "
            f"{(target_height, target_width)}, but the GeoTIFF lacks usable "
            "ModelPixelScale/ModelTiepoint georeferencing. "
            "Refusing to stretch or guess pixel placement."
        )

    request_epsg = _epsg_code_from_crs(request.crs or request.bbox.crs)
    if (
        georef.epsg is not None
        and request_epsg is not None
        and georef.epsg != request_epsg
    ):
        raise ValidationError(
            f"Clipped coverage CRS EPSG:{georef.epsg} does not match the "
            f"requested tile EPSG:{request_epsg}; refusing to reproject."
        )

    request_transform = _request_geotransform(request.bbox, target_height, target_width)
    row_offset, col_offset = pixel_offsets_for_placement(
        request_transform=request_transform,
        response_transform=georef.transform,
    )
    return place_array_on_canvas(
        array,
        target_height=target_height,
        target_width=target_width,
        row_offset=row_offset,
        col_offset=col_offset,
        fill_value=fill_value,
    )


def fit_tile_to_request(
    array: NDArrayAny,
    request: TileRequest,
    response: TileResponse,
) -> NDArrayAny:
    """Match decoded pixels to the requested tile shape without silent stretch.

    Exact shape → pass-through. Undersize GeoTIFF with usable tags → place +
    pad. Oversize → downsample. Anything else → :class:`ValidationError`.
    """

    target_height = request.height or array.shape[0]
    target_width = request.width or array.shape[1]
    actual_height, actual_width = array.shape[0], array.shape[1]

    if actual_height == target_height and actual_width == target_width:
        return array

    if actual_height < target_height or actual_width < target_width:
        return _place_clipped_geotiff_tile(array, request, response)

    return resize_tile_array(array, target_height, target_width)
