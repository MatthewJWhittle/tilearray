"""Shared tile decode pipeline with pluggable band policy and response unwrapping."""

from __future__ import annotations

import logging
import re
import tempfile
import warnings
from collections.abc import Callable
from email import policy
from email.parser import BytesParser
from io import BytesIO
from typing import Literal, cast

import numpy as np
from geotiff import GeoTiff  # type: ignore[import-untyped]
from geotiff.geotiff import TiffFile  # type: ignore[import-untyped]
from numpy.typing import NDArray

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
BandPolicy = Literal["preserve", "first_band"]
ResponseUnwrapper = Callable[[bytes, TileResponse], bytes]
BytesReader = Callable[[bytes], NDArrayFloat]
TileDecoder = Callable[[TileResponse, TileRequest], NDArrayFloat]

DEFAULT_BAND_POLICIES: dict[Format, BandPolicy] = {
    Format.GEOTIFF: "first_band",
    Format.PNG: "preserve",
    Format.JPEG: "preserve",
}

_DECODER_REGISTRY: dict[Format, TileDecoder] = {}


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


def _extract_tiff_part(data: bytes, content_type: str) -> bytes | None:
    effective_type = _effective_multipart_content_type(content_type, data)
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

    content_type = (response.content_type or "").lower()
    if data[:2] in (b"II", b"MM"):
        return data

    if "multipart/" in content_type or data.startswith(b"--"):
        tiff_part = _extract_tiff_part(data, content_type)
        if tiff_part is not None:
            return tiff_part

    return data


def apply_band_policy(data: NDArrayFloat, policy: BandPolicy) -> NDArrayFloat:
    """Reduce or preserve band dimensions according to the configured policy."""

    if data.ndim <= 2 or policy == "preserve":
        return data

    if data.ndim == 3:
        bands_first = data.shape[0] <= 4 and data.shape[0] < min(
            data.shape[1], data.shape[2]
        )
        if bands_first:
            return cast(NDArrayFloat, data[0])
        return cast(NDArrayFloat, data[..., 0])

    return cast(NDArrayFloat, data.reshape(data.shape[0], -1)[0])


def decode_tile_bytes(
    raw_bytes: bytes,
    *,
    reader: BytesReader,
    band_policy: BandPolicy,
    unwrapper: ResponseUnwrapper | None = None,
    response: TileResponse | None = None,
) -> NDArrayFloat:
    """Run the shared decode pipeline: unwrap → read → band policy → float32."""

    stub_response = response or TileResponse(
        data=raw_bytes,
        content_type="",
        status_code=200,
        headers={},
        url="",
        success=True,
    )
    payload = (unwrapper or identity_unwrapper)(raw_bytes, stub_response)
    data = reader(payload)
    data = apply_band_policy(data, band_policy)
    return cast(NDArrayFloat, np.asarray(data, dtype=np.float32))


def read_geotiff_bytes(raw_bytes: bytes) -> NDArrayFloat:
    """Read a GeoTIFF payload from bytes."""

    with tempfile.NamedTemporaryFile(suffix=".tif") as tmp:
        tmp.write(raw_bytes)
        tmp.flush()
        try:
            return _read_geotiff_file(tmp.name)
        except Exception:  # pragma: no cover - fallback path
            tif = GeoTiff(tmp.name, as_crs=None)
            return cast(NDArrayFloat, np.asarray(tif.read(), dtype=np.float32))


def _read_geotiff_file(path: str) -> NDArrayFloat:
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

    invalid = ~np.isfinite(data)
    sentinel = np.abs(data) > 1e20
    if invalid.any() or sentinel.any():
        data = data.copy()
        data[invalid | sentinel] = np.nan
    return cast(NDArrayFloat, np.asarray(data, dtype=np.float32))


def read_image_bytes(raw_bytes: bytes) -> NDArrayFloat:
    """Read a JPEG/PNG payload from bytes."""

    if _PILImage is not None:  # pragma: no cover - depends on optional library
        with BytesIO(raw_bytes) as bio:
            with _PILImage.open(bio) as img:
                return cast(NDArrayFloat, np.asarray(img))
    if _imageio is not None:  # pragma: no cover
        with BytesIO(raw_bytes) as bio:
            return cast(NDArrayFloat, np.asarray(_imageio.imread(bio)))

    msg = (
        "PNG/JPEG decoding requires Pillow or imageio. "
        "Install one of these packages or provide a custom tile_decoder."
    )
    raise RuntimeError(msg)


def make_tile_decoder(
    reader: BytesReader,
    *,
    band_policy: BandPolicy,
    unwrapper: ResponseUnwrapper | None = None,
) -> TileDecoder:
    """Build a :class:`TileDecoder` from shared pipeline pieces."""

    def _decoder(response: TileResponse, request: TileRequest) -> NDArrayFloat:
        del request
        return decode_tile_bytes(
            bytes(response.data),
            reader=reader,
            band_policy=band_policy,
            unwrapper=unwrapper,
            response=response,
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


def band_count_from_array(array: NDArrayFloat) -> int:
    """Return the number of bands represented by a decoded tile array."""

    if array.ndim == 3:
        bands_first = array.shape[0] <= 4 and array.shape[0] < min(
            array.shape[1], array.shape[2]
        )
        if bands_first:
            return int(array.shape[0])
        return int(array.shape[2])
    return 1


def default_band_count_for_format(fmt: Format | None) -> int | None:
    """
    Return a fixed band count when known, or ``None`` when callers must probe.

    GeoTIFF elevation tiles are always reduced to a single band by policy.
    JPEG/PNG band count depends on the source imagery.
    """

    if fmt is None:
        return None
    policy = DEFAULT_BAND_POLICIES.get(fmt)
    if policy == "first_band":
        return 1
    if policy == "preserve":
        return None
    return None


def read_geotiff_path(path: str) -> NDArrayFloat:
    """Read a GeoTIFF from disk and apply the default elevation band policy."""

    data = _read_geotiff_file(path)
    return apply_band_policy(data, DEFAULT_BAND_POLICIES[Format.GEOTIFF])


def _register_default_decoders() -> None:
    register_tile_decoder(
        Format.GEOTIFF,
        make_tile_decoder(
            read_geotiff_bytes,
            band_policy=DEFAULT_BAND_POLICIES[Format.GEOTIFF],
            unwrapper=unwrap_multipart,
        ),
    )
    if _PILImage is not None or _imageio is not None:  # pragma: no cover
        image_decoder = make_tile_decoder(
            read_image_bytes,
            band_policy=DEFAULT_BAND_POLICIES[Format.PNG],
        )
        register_tile_decoder(Format.PNG, image_decoder)
        register_tile_decoder(Format.JPEG, image_decoder)


_register_default_decoders()
