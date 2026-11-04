"""
Generic type definitions and models for tile-based geospatial data processing.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from functools import lru_cache
from typing import Any, ClassVar

from pydantic import BaseModel, Field, GetCoreSchemaHandler, model_validator
from pydantic_core import CoreSchema, core_schema
from pyproj import CRS as PyprojCRS
from pyproj import Transformer
from pyproj.exceptions import CRSError

from .errors import ValidationError

# OGC HTTP URIs and URNs that spell EPSG codes pyproj may not accept verbatim.
_OGC_EPSG_URI_RE = re.compile(r"/EPSG/(?:0/)?(\d+)/?$", re.I)
_URN_EPSG_RE = re.compile(
    r"(?:URN:OGC:DEF:CRS:)?EPSG(?::+(?:\d+\.?\d*:)?)?:*?(\d+)",
    re.I,
)


def canonicalize_crs_input(value: str) -> str:
    """Rewrite common OGC URN/URI CRS spellings to forms pyproj accepts.

    Handles ``EPSG:NNNN``, ``urn:ogc:def:crs:EPSG::NNNN``, OpenGIS EPSG HTTP
    URIs, and CRS84 aliases (``OGC:CRS84``). Other strings are returned stripped
    unchanged so :meth:`CRS.parse` can still try pyproj / raise clearly.
    """

    text = value.strip()
    if not text:
        return text
    upper = text.upper()
    if upper.startswith("EPSG:"):
        code = text.split(":", 1)[1].split("/")[0].strip()
        return f"EPSG:{code}" if code else text
    uri_match = _OGC_EPSG_URI_RE.search(text)
    if uri_match:
        return f"EPSG:{uri_match.group(1)}"
    urn_match = _URN_EPSG_RE.search(upper.replace(" ", ""))
    if urn_match:
        return f"EPSG:{urn_match.group(1)}"
    if "CRS84" in upper or upper.endswith(":84") or "CRS:84" in upper:
        return "OGC:CRS84"
    return text


@lru_cache(maxsize=256)
def _pyproj_crs(authority: str) -> PyprojCRS:
    return PyprojCRS.from_user_input(authority)


def _authority_string(pj: PyprojCRS) -> str:
    """Prefer ``EPSG:NNNN`` (or other authority) over a raw WKT dump."""

    auth = pj.to_authority()
    if auth is not None:
        return f"{auth[0]}:{auth[1]}"
    # Fall back to a compact PROJ string when no authority is registered.
    try:
        return pj.to_string()
    except CRSError:
        return pj.to_wkt()


def _suggest_crs_fix(raw: str) -> str | None:
    """Best-effort typo hint for common CRS authority mistakes."""

    stripped = raw.strip()
    if not stripped:
        return None
    if ":" in stripped:
        prefix, rest = stripped.split(":", 1)
        if prefix.upper() == "EPGS" and rest.strip():
            return f"EPSG:{rest.strip()}"
    upper = stripped.upper()
    if upper.startswith("EPGS"):
        return "EPSG:" + stripped[4:].lstrip(":").strip()
    return None


def _invalid_crs_message(raw: Any, *, cause: BaseException | None = None) -> str:
    text = str(raw).strip() if raw is not None else ""
    suggestion = _suggest_crs_fix(text) if text else None
    detail = str(cause).strip() if cause is not None else ""
    parts = [f"Invalid CRS {raw!r}."]
    if suggestion is not None:
        parts.append(f"Did you mean {suggestion!r}?")
    parts.append(
        "Use an EPSG authority code (for example 'EPSG:4326' or 4326), "
        "another pyproj-recognised CRS string, or a CRS / pyproj.CRS instance."
    )
    if detail and suggestion is None:
        parts.append(f"pyproj: {detail}")
    return " ".join(parts)


class CRS(str):
    """Validated coordinate reference system as an authority string.

    Presets such as :attr:`EPSG_4326` remain available for common codes.
    Arbitrary projected or geographic CRS values accepted by pyproj
    (``EPSG:25833``, integers, ``pyproj.CRS``, …) are normalised to an
    authority string like ``EPSG:25833``. Invalid identifiers raise
    :class:`~tilearray.errors.ValidationError` early.
    """

    EPSG_4326: ClassVar[CRS]
    EPSG_3857: ClassVar[CRS]
    EPSG_32633: ClassVar[CRS]
    EPSG_27700: ClassVar[CRS]

    def __new__(cls, value: Any) -> CRS:
        if isinstance(value, cls):
            return value
        return cls.parse(value)

    @classmethod
    def parse(cls, value: Any) -> CRS:
        """Normalise a CRS input to a validated authority string."""

        if isinstance(value, cls):
            return value
        if isinstance(value, PyprojCRS):
            return cls._from_pyproj(value)
        if isinstance(value, bool):
            # bool is a subclass of int — reject before int handling.
            raise ValidationError(_invalid_crs_message(value))
        if isinstance(value, int):
            try:
                return cls._from_pyproj(PyprojCRS.from_epsg(value))
            except CRSError as exc:
                raise ValidationError(_invalid_crs_message(value, cause=exc)) from exc
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                raise ValidationError(_invalid_crs_message(value))
            candidate = canonicalize_crs_input(stripped)
            try:
                return cls._from_pyproj(PyprojCRS.from_user_input(candidate))
            except CRSError as exc:
                raise ValidationError(
                    _invalid_crs_message(stripped, cause=exc)
                ) from exc
        raise ValidationError(_invalid_crs_message(value))

    @classmethod
    def _from_pyproj(cls, pj: PyprojCRS) -> CRS:
        authority = _authority_string(pj)
        # Re-parse via authority so cached pyproj lookups share one key.
        _pyproj_crs(authority)
        return str.__new__(cls, authority)

    @classmethod
    def from_string(cls, crs: str) -> CRS:
        """Create CRS from an authority string such as ``EPSG:4326``."""

        if not isinstance(crs, str):
            raise ValidationError(_invalid_crs_message(crs))
        stripped = crs.strip()
        if ":" not in stripped:
            raise ValidationError(
                f"Invalid CRS format: {crs}. Expected an authority string "
                "such as 'EPSG:4326', an integer EPSG code, or a CRS instance."
            )
        return cls.parse(stripped)

    @classmethod
    def from_integer(cls, crs: int) -> CRS:
        """Create CRS from an integer EPSG code."""

        if isinstance(crs, bool) or not isinstance(crs, int):
            raise ValidationError(_invalid_crs_message(crs))
        return cls.parse(crs)

    @classmethod
    def from_epsg(cls, crs: str | int) -> CRS:
        """
        Create CRS from an EPSG code.

        Args:
            crs: EPSG code as string (``EPSG:4326``) or integer (``4326``)

        Returns:
            Validated CRS authority string
        """

        if isinstance(crs, str):
            return cls.from_string(crs) if ":" in crs.strip() else cls.parse(crs)
        return cls.from_integer(crs)

    @property
    def value(self) -> str:
        """Authority string (compat with the former enum ``.value``)."""

        return str(self)

    def to_pyproj(self) -> PyprojCRS:
        """Return the cached :class:`pyproj.CRS` for this authority string."""

        return _pyproj_crs(str(self))

    @property
    def is_geographic(self) -> bool:
        """True when this CRS is geographic (angular axes)."""

        return self.to_pyproj().is_geographic

    @property
    def is_projected(self) -> bool:
        """True when this CRS is projected (typically linear axes)."""

        return self.to_pyproj().is_projected

    @property
    def north_axis_first(self) -> bool:
        """True when the first CRS axis is north/south (WMS 1.3 axis order)."""

        axes = self.to_pyproj().axis_info
        if not axes:
            return False
        return axes[0].direction.lower() in {"north", "south"}

    def default_subset_axes(self) -> tuple[str, str]:
        """Default WCS 2 subset axis labels when DescribeCoverage is silent."""

        if self == CRS.EPSG_27700:
            return ("E", "N")
        if self == CRS.EPSG_3857:
            return ("x", "y")
        if self.is_geographic:
            return ("Long", "Lat")
        return ("E", "N")

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: Any, _handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        def validate(value: Any) -> CRS:
            return cls.parse(value)

        return core_schema.no_info_plain_validator_function(
            validate,
            serialization=core_schema.plain_serializer_function_ser_schema(
                str, info_arg=False, return_schema=core_schema.str_schema()
            ),
        )


CRS.EPSG_4326 = CRS.parse("EPSG:4326")
CRS.EPSG_3857 = CRS.parse("EPSG:3857")
CRS.EPSG_32633 = CRS.parse("EPSG:32633")
CRS.EPSG_27700 = CRS.parse("EPSG:27700")


class Format(str, Enum):
    """Supported output formats."""

    GEOTIFF = "image/tiff"
    PNG = "image/png"
    JPEG = "image/jpeg"


BBoxTuple = tuple[float, float, float, float]


class BoundingBox(BaseModel):
    """Bounding box representation."""

    min_x: float = Field(..., description="Minimum X coordinate")
    min_y: float = Field(..., description="Minimum Y coordinate")
    max_x: float = Field(..., description="Maximum X coordinate")
    max_y: float = Field(..., description="Maximum Y coordinate")
    crs: CRS = Field(default=CRS.EPSG_4326, description="Coordinate Reference System")

    @model_validator(mode="after")
    def validate_coordinates(self) -> BoundingBox:
        """Validate that min coordinates are less than max coordinates."""
        if self.min_x >= self.max_x:
            raise ValueError("min_x must be less than max_x")
        if self.min_y >= self.max_y:
            raise ValueError("min_y must be less than max_y")
        return self

    @classmethod
    def from_tuple(
        cls,
        bbox: BBoxTuple,
        crs: CRS | str | int = CRS.EPSG_4326,
    ) -> BoundingBox:
        """Create BoundingBox from tuple."""

        return cls(
            min_x=bbox[0],
            min_y=bbox[1],
            max_x=bbox[2],
            max_y=bbox[3],
            crs=CRS.parse(crs),
        )

    def intersects(self, other: BoundingBox) -> bool:
        """Check if this bounding box intersects with another."""
        return (
            self.min_x < other.max_x
            and self.max_x > other.min_x
            and self.min_y < other.max_y
            and self.max_y > other.min_y
        )

    def to_crs(self, crs: CRS) -> BoundingBox:
        """Transform the bounding box to a new CRS."""
        target = CRS.parse(crs)
        transformer = Transformer.from_crs(
            self.crs.to_pyproj(), target.to_pyproj(), always_xy=True
        )
        xmin, ymin = transformer.transform(self.min_x, self.min_y)
        xmax, ymax = transformer.transform(self.max_x, self.max_y)
        return BoundingBox(min_x=xmin, min_y=ymin, max_x=xmax, max_y=ymax, crs=target)


class SpatialExtent(BaseModel):
    """Spatial extent information."""

    bbox: BoundingBox
    dimensions: dict[str, Any] | None = Field(
        None, description="Optional dimension details"
    )


class TemporalExtent(BaseModel):
    """Temporal extent information."""

    start_time: datetime | None = None
    end_time: datetime | None = None

    @model_validator(mode="after")
    def validate_temporal_order(self) -> TemporalExtent:
        """Validate that end_time is after start_time."""
        if self.end_time and self.start_time and self.end_time < self.start_time:
            raise ValueError("end_time must be after start_time")
        return self


class CoverageDescription(BaseModel):
    """Coverage description from WCS GetCapabilities."""

    identifier: str = Field(..., description="Coverage identifier")
    title: str | None = None
    abstract: str | None = None
    keywords: list[str] = Field(default_factory=list)
    supported_crs: list[CRS] = Field(default_factory=list)
    supported_formats: list[Format] = Field(default_factory=list)
    spatial_extent: SpatialExtent | None = None
    temporal_extent: TemporalExtent | None = None
    native_crs: CRS | None = Field(
        None, description="Native CRS from DescribeCoverage envelope srsName"
    )
    axis_labels: dict[str, tuple[str, str]] = Field(
        default_factory=dict,
        description="Subsetting axis labels keyed by normalized CRS (e.g. EPSG:3857)",
    )
    native_format: Format | None = Field(
        None, description="Preferred output format from DescribeCoverage nativeFormat"
    )


class ServiceCapabilities(BaseModel):
    """WCS Service Capabilities."""

    service_title: str
    service_abstract: str | None = None
    service_keywords: list[str] = Field(default_factory=list)
    service_provider: str | None = None
    service_contact: str | None = None
    service_url: str
    version: str = Field(default="2.0.1")
    supported_versions: list[str] = Field(default_factory=lambda: ["2.0.1", "2.0.0"])
    supported_operations: list[str] = Field(
        default_factory=lambda: ["GetCapabilities", "DescribeCoverage", "GetCoverage"]
    )
    supported_formats: list[Format] = Field(default_factory=list)
    supported_crs: list[CRS] = Field(default_factory=list)
    coverages: list[CoverageDescription] = Field(default_factory=list)


class TileRequest(BaseModel):
    """Generic tile request parameters."""

    url: str
    params: dict[str, Any]
    headers: dict[str, str] | None = None
    timeout: int = 30
    retries: int = 3
    output_format: Format | None = None
    crs: CRS | None = None
    bbox: BoundingBox | None = None
    width: int | None = None
    height: int | None = None


class TileResponse(BaseModel):
    """Response from tile request."""

    data: bytes
    content_type: str
    status_code: int
    headers: dict[str, str]
    url: str
    success: bool
    error_message: str | None = None


class WCSResponse(BaseModel):
    """Represents a WCS response, either successful data or an error."""

    success: bool = Field(
        ..., description="True if the request was successful, False otherwise"
    )
    data: Any | None = Field(
        None, description="The response data (e.g., image bytes, NetCDF data)"
    )
    error_message: str | None = Field(
        None, description="Error message if the request failed"
    )
    status_code: int | None = Field(
        None, description="HTTP status code of the response"
    )


class ServiceTypeEnum(str, Enum):
    """Service types."""

    WCS = "WCS"
    WMS = "WMS"
    WMTS = "WMTS"
    XYZ = "XYZ"
