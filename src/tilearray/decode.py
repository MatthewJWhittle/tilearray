"""Shared tile decode pipeline with pluggable band policy and response unwrapping."""

from __future__ import annotations

import logging
import math
import re
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from io import BytesIO
from typing import Literal, cast

import numpy as np
import tifffile
from numpy.typing import NDArray

from .errors import ConfigurationError, ValidationError
from .types import Format, TileRequest, TileResponse

try:  # pragma: no cover - optional dependency
    from PIL import Image as _PILImage
except ImportError:  # pragma: no cover - optional dependency
    _PILImage = None

try:  # pragma: no cover - optional dependency
    import imageio.v2 as _imageio  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional dependency
    _imageio = None

NDArrayFloat = NDArray[np.floating]
NDArrayAny = NDArray[np.generic]
BandPolicy = Literal["preserve", "first_band"]
ResponseUnwrapper = Callable[[bytes, TileResponse], bytes]
BytesReader = Callable[[bytes], NDArrayAny]
TileDecoder = Callable[[TileResponse, TileRequest], NDArrayAny]

# GeoTIFF defaults to preserve so multiband coverages are not silently reduced.
# Pass band_policy=\"first_band\" for an explicit single-band reduction.
DEFAULT_BAND_POLICIES: dict[Format, BandPolicy] = {
    Format.GEOTIFF: "preserve",
    Format.PNG: "preserve",
    Format.JPEG: "preserve",
}

# Absolute tolerance for comparing Affine scale / integer pixel offsets.
_AFFINE_ATOL = 1e-6

_DECODER_REGISTRY: dict[Format, TileDecoder] = {}


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


def identity_unwrapper(data: bytes, response: TileResponse) -> bytes:
    """Pass-through response unwrapper (default)."""

    del response
    return data


def _extract_multipart_boundary(content_type: str, data: bytes) -> str | None:
    match = re.search(r'boundary="?([^";]+)"?', content_type, flags=re.I)
    if match:
        return match.group(1)
    if data.startswith(b"--"):
        first_line = data.split(b"\n", 1)[0]
        if first_line.startswith(b"--") and len(first_line) > 2:
            return first_line[2:].decode("ascii", errors="ignore").strip()
    return None


def _effective_multipart_content_type(content_type: str, data: bytes) -> str:
    """Use body sniffing when headers claim image/tiff but bytes are multipart."""

    if "multipart/" in content_type.lower():
        return content_type
    boundary = _extract_multipart_boundary(content_type, data)
    if data.startswith(b"--") and boundary:
        return f'multipart/related; boundary="{boundary}"'
    return content_type


def sniff_effective_content_type(content_type: str, data: bytes) -> str:
    """
    Infer the effective MIME type from bytes, not headers alone.

    Used by decode and disk cache restore when ``Content-Type`` is missing,
    generic, or misleading (e.g. ``image/tiff`` on ``multipart/related``).
    """

    if not data:
        return content_type

    if data[:2] in (b"II", b"MM"):
        if content_type:
            return content_type
        return "image/tiff"

    if "multipart/" in content_type.lower() or data.startswith(b"--"):
        return _effective_multipart_content_type(content_type, data)

    return content_type


def _extract_tiff_part(data: bytes, content_type: str) -> bytes | None:
    effective_type = sniff_effective_content_type(content_type, data)
    boundary = _extract_multipart_boundary(effective_type, data)
    if not boundary:
        return None

    mime_bytes = f"Content-Type: {effective_type}\r\n\r\n".encode("ascii") + data
    message = BytesParser(policy=policy.default).parsebytes(mime_bytes)
    if not message.is_multipart():
        return None

    for part in message.iter_parts():
        part_type = (part.get_content_type() or "").lower()
        if part_type in {"image/tiff", "image/geotiff"}:
            payload = part.get_payload(decode=True)
            if isinstance(payload, bytes) and payload[:2] in (b"II", b"MM"):
                return payload
    return None


def unwrap_multipart(data: bytes, response: TileResponse) -> bytes:
    """
    Unwrap ``multipart/related`` WCS payloads (e.g. ArcGIS ImageServer).

    Returns the first ``image/tiff`` part when present; otherwise passes
    through raw bytes unchanged.
    """

    content_type = sniff_effective_content_type(response.content_type or "", data)
    if data[:2] in (b"II", b"MM"):
        return data

    if "multipart/" in content_type.lower() or data.startswith(b"--"):
        tiff_part = _extract_tiff_part(data, content_type)
        if tiff_part is not None:
            return tiff_part

    return data


def apply_band_policy(data: NDArrayAny, policy: BandPolicy) -> NDArrayAny:
    """Reduce or preserve band dimensions according to the configured policy."""

    if data.ndim <= 2 or policy == "preserve":
        return data

    if data.ndim == 3:
        bands_first = data.shape[0] <= 4 and data.shape[0] < min(
            data.shape[1], data.shape[2]
        )
        if bands_first:
            return cast(NDArrayAny, data[0])
        return cast(NDArrayAny, data[..., 0])

    return cast(NDArrayAny, data.reshape(data.shape[0], -1)[0])


def bands_first_layout(array: NDArrayAny) -> bool:
    """Return True when a 3D array looks like ``(band, y, x)`` rather than ``(y, x, band)``."""

    if array.ndim != 3:
        return False
    return bool(
        array.shape[0] <= 4 and array.shape[0] < min(array.shape[1], array.shape[2])
    )


def ensure_band_last(array: NDArrayAny) -> NDArrayAny:
    """Return ``(y, x)`` or ``(y, x, band)`` regardless of band-first source layout."""

    if array.ndim == 3 and bands_first_layout(array):
        return cast(NDArrayAny, np.moveaxis(array, 0, -1))
    return array


def decode_tile_bytes(
    raw_bytes: bytes,
    *,
    reader: BytesReader,
    band_policy: BandPolicy,
    unwrapper: ResponseUnwrapper | None = None,
    response: TileResponse | None = None,
    cast_float32: bool = True,
) -> NDArrayAny:
    """
    Run the shared decode pipeline: unwrap → read → band policy → optional float32.

    GeoTIFF elevation paths keep ``cast_float32=True`` so ``GDAL_NODATA`` / sentinels
    can be represented as NaN. Imagery readers may pass ``cast_float32=False`` to
    preserve native ``uint8`` until ``create_array(..., dtype=)`` applies its cast.
    """

    stub_response = response or TileResponse(
        data=raw_bytes,
        content_type="",
        status_code=200,
        headers={},
        url="",
        success=True,
    )
    effective_response = TileResponse(
        data=stub_response.data,
        content_type=sniff_effective_content_type(
            stub_response.content_type, bytes(stub_response.data)
        ),
        status_code=stub_response.status_code,
        headers=stub_response.headers,
        url=stub_response.url,
        success=stub_response.success,
        error_message=stub_response.error_message,
    )
    payload = (unwrapper or identity_unwrapper)(raw_bytes, effective_response)
    data = reader(payload)
    data = apply_band_policy(data, band_policy)
    data = ensure_band_last(data)
    if cast_float32:
        return cast(NDArrayAny, np.asarray(data, dtype=np.float32))
    return cast(NDArrayAny, np.asarray(data))


def read_geotiff_bytes(raw_bytes: bytes) -> NDArrayFloat:
    """Read a GeoTIFF payload from bytes via ``tifffile`` (no GDAL/zarr)."""

    return _read_geotiff_source(BytesIO(raw_bytes))


def _read_geotiff_file(path: str) -> NDArrayFloat:
    """Read a GeoTIFF from a filesystem path."""

    return _read_geotiff_source(path)


def _read_geotiff_source(source: str | BytesIO) -> NDArrayFloat:
    """
    Decode TIFF/GeoTIFF pixels with ``tifffile``.

    Applies GDAL_NODATA when present and maps non-finite / extreme sentinel
    values to NaN so elevation mosaics stay numerically honest. Output is
    always ``float32`` so NaN nodata is representable (integer sources are
    promoted).
    """

    tifffile_logger = logging.getLogger("tifffile")
    previous_level = tifffile_logger.level
    tifffile_logger.setLevel(logging.ERROR)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", category=UserWarning)
            with tifffile.TiffFile(source) as tif_file:
                data = np.asarray(tif_file.asarray(), dtype=np.float64)
                page = tif_file.pages.first
                nodata_tag = page.tags.get("GDAL_NODATA") if page is not None else None
                if nodata_tag is not None:
                    try:
                        nodata = float(nodata_tag.value)
                    except (TypeError, ValueError):
                        nodata = None
                    if nodata is not None and np.isfinite(nodata):
                        data[data == nodata] = np.nan
    finally:
        tifffile_logger.setLevel(previous_level)

    invalid = ~np.isfinite(data)
    sentinel = np.abs(data) > 1e20
    if invalid.any() or sentinel.any():
        data = data.copy()
        data[invalid | sentinel] = np.nan
    return cast(NDArrayFloat, np.asarray(data, dtype=np.float32))


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
                if page is None:
                    return None
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


def geotiff_bytes_from_response(response: TileResponse) -> bytes:
    """Return unwrapped TIFF bytes from a tile response (multipart-aware)."""

    return unwrap_multipart(bytes(response.data), response)


def read_image_bytes(raw_bytes: bytes, *, expand_palette: bool = False) -> NDArrayAny:
    """
    Read a JPEG/PNG payload from bytes.

    Palette-mode PNGs stay as index arrays by default (``expand_palette=False``).
    Pass ``expand_palette=True`` to expand to RGB — opt-in so indices are never
    silently treated as reflectance/brightness.
    """

    if _PILImage is not None:  # pragma: no cover - depends on optional library
        with BytesIO(raw_bytes) as bio:
            with _PILImage.open(bio) as img:
                if expand_palette and img.mode in {"P", "PA"}:
                    img = img.convert("RGB")
                return cast(NDArrayAny, np.asarray(img))
    if _imageio is not None:  # pragma: no cover
        with BytesIO(raw_bytes) as bio:
            data = np.asarray(_imageio.imread(bio))
            if expand_palette and data.ndim == 2:
                # imageio without Pillow cannot expand a palette; keep indices.
                pass
            return cast(NDArrayAny, data)

    msg = (
        "PNG/JPEG decoding requires Pillow or imageio. "
        "Install one of these packages or provide a custom tile_decoder."
    )
    raise ConfigurationError(msg)


def make_image_reader(*, expand_palette: bool = False) -> BytesReader:
    """Build an imagery ``BytesReader`` with optional palette→RGB expansion."""

    def _reader(raw_bytes: bytes) -> NDArrayAny:
        return read_image_bytes(raw_bytes, expand_palette=expand_palette)

    return _reader


def make_tile_decoder(
    reader: BytesReader,
    *,
    band_policy: BandPolicy,
    unwrapper: ResponseUnwrapper | None = None,
    cast_float32: bool = True,
) -> TileDecoder:
    """Build a :class:`TileDecoder` from shared pipeline pieces."""

    def _decoder(response: TileResponse, request: TileRequest) -> NDArrayAny:
        del request
        return decode_tile_bytes(
            bytes(response.data),
            reader=reader,
            band_policy=band_policy,
            unwrapper=unwrapper,
            response=response,
            cast_float32=cast_float32,
        )

    return _decoder


def register_tile_decoder(
    fmt: Format,
    decoder: TileDecoder,
    *,
    band_policy: BandPolicy | None = None,
) -> None:
    """Register a decoder for ``fmt``."""

    del band_policy  # reserved for future per-format policy metadata
    _DECODER_REGISTRY[fmt] = decoder


def decoder_for_format(fmt: Format | str | None) -> TileDecoder | None:
    """Return the registered decoder for ``fmt``, if any."""

    if isinstance(fmt, Format):
        return _DECODER_REGISTRY.get(fmt)
    if isinstance(fmt, str):
        try:
            fmt_enum = Format(fmt)
        except ValueError:
            return None
        return _DECODER_REGISTRY.get(fmt_enum)
    return None


def build_format_decoder(
    fmt: Format,
    *,
    band_policy: BandPolicy | None = None,
    expand_palette: bool = False,
) -> TileDecoder | None:
    """
    Build a decoder for ``fmt`` with explicit band / palette policy.

    Used by ``create_array`` so callers can override defaults without mutating
    the process-wide registry.
    """

    policy = band_policy or DEFAULT_BAND_POLICIES.get(fmt, "preserve")
    if fmt == Format.GEOTIFF:
        return make_tile_decoder(
            read_geotiff_bytes,
            band_policy=policy,
            unwrapper=unwrap_multipart,
            cast_float32=True,
        )
    if fmt in {Format.PNG, Format.JPEG}:
        if _PILImage is None and _imageio is None:  # pragma: no cover
            return None
        return make_tile_decoder(
            make_image_reader(expand_palette=expand_palette),
            band_policy=policy,
            cast_float32=False,
        )
    return None


def band_count_from_array(array: NDArrayAny) -> int:
    """Return the number of bands represented by a decoded tile array."""

    if array.ndim == 3:
        if bands_first_layout(array):
            return int(array.shape[0])
        return int(array.shape[2])
    return 1


def default_band_count_for_format(
    fmt: Format | None, *, band_policy: BandPolicy | None = None
) -> int | None:
    """
    Return a fixed band count when known, or ``None`` when callers must probe.

    With the default ``preserve`` GeoTIFF policy, band count is probed from a
    sample tile. Explicit ``first_band`` returns ``1`` without probing.
    """

    if fmt is None:
        return None
    policy = band_policy or DEFAULT_BAND_POLICIES.get(fmt)
    if policy == "first_band":
        return 1
    if policy == "preserve":
        return None
    return None


def read_geotiff_path(path: str) -> NDArrayFloat:
    """Read a GeoTIFF from disk and apply the default band policy."""

    data = _read_geotiff_file(path)
    reduced = apply_band_policy(data, DEFAULT_BAND_POLICIES[Format.GEOTIFF])
    return cast(NDArrayFloat, np.asarray(ensure_band_last(reduced), dtype=np.float32))


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


def _register_default_decoders() -> None:
    register_tile_decoder(
        Format.GEOTIFF,
        make_tile_decoder(
            read_geotiff_bytes,
            band_policy=DEFAULT_BAND_POLICIES[Format.GEOTIFF],
            unwrapper=unwrap_multipart,
            cast_float32=True,
        ),
    )
    if _PILImage is not None or _imageio is not None:  # pragma: no cover
        image_decoder = make_tile_decoder(
            make_image_reader(expand_palette=False),
            band_policy=DEFAULT_BAND_POLICIES[Format.PNG],
            cast_float32=False,
        )
        register_tile_decoder(Format.PNG, image_decoder)
        register_tile_decoder(Format.JPEG, image_decoder)


_register_default_decoders()
