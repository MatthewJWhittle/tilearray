"""WCS (Web Coverage Service) XML parsing and tile request functionality."""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import httpx

from ..error_messages import (
    config_params_for_errors,
    describe_coverage_parse_message,
    format_http_failure,
    looks_like_html,
    unsupported_format_message,
)
from ..errors import NetworkError, ParseError, ValidationError
from ..mosaic_plan import MosaicPlan
from ..types import (
    CRS,
    BoundingBox,
    CoverageDescription,
    Format,
    ServiceCapabilities,
    ServiceTypeEnum,
    SpatialExtent,
    TemporalExtent,
    TileRequest,
    WCSResponse,
    canonicalize_crs_input,
)
from .base import BaseService, TileGeometry, register_service
from .http import (
    compose_http_headers,
    compose_http_params,
    http_get,
    http_get_capabilities_text,
    resolve_retries,
    resolve_timeout,
)
from .requests import tile_option_passthrough

logger = logging.getLogger(__name__)


def label_is_northing(label: str, crs: CRS | None = None) -> bool | None:
    """Classify a subset/envelope axis label as northing/lat when possible.

    Returns ``True`` / ``False`` when the name is unambiguous, else ``None``
    so callers can fall back to CRS axis order.
    """

    key = label.strip().lower()
    if key in {"lat", "latitude", "n", "north", "northing", "y"}:
        return True
    if key in {"lon", "long", "longitude", "e", "east", "easting", "x"}:
        return False
    if crs is not None:
        try:
            axes = list(CRS.parse(crs).to_pyproj().axis_info)
        except Exception:  # pragma: no cover - defensive
            axes = []
        for axis in axes:
            name = (axis.name or "").lower()
            abbrev = (axis.abbrev or "").lower()
            if key in {name, abbrev} or key == abbrev.replace(" ", ""):
                direction = (axis.direction or "").lower()
                if direction in {"north", "south"}:
                    return True
                if direction in {"east", "west"}:
                    return False
    return None


def normalize_crs_reference(value: str) -> str:
    """Normalize OGC URI / URN or EPSG shorthand to ``EPSG:<code>``.

    Thin wrapper around :func:`tilearray.types.canonicalize_crs_input` kept for
    WCS caps soft-skip paths and existing imports.
    """

    return canonicalize_crs_input(value)


def _coerce_crs_enum(value: str | None) -> CRS | None:
    if not value:
        return None
    normalized = normalize_crs_reference(value)
    try:
        return CRS.parse(normalized)
    except ValidationError:
        logger.debug("Skipping unsupported CRS reference '%s'", value)
        return None


class WCSParser:
    """Parser for WCS XML responses."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.namespaces = {
            "wcs": "http://www.opengis.net/wcs/2.0",
            "ows": "http://www.opengis.net/ows/1.1",
            "gml": "http://www.opengis.net/gml/3.2",
            "crs": "http://www.opengis.net/wcs/crs/1.0",
            "xsi": "http://www.w3.org/2001/XMLSchema-instance",
        }

    def parse_get_capabilities(self, xml_content: str) -> ServiceCapabilities:
        try:
            root = ET.fromstring(xml_content)

            service_title = self._get_text(
                root, ".//ows:ServiceIdentification/ows:Title"
            )
            service_abstract = self._get_text(
                root, ".//ows:ServiceIdentification/ows:Abstract"
            )
            service_keywords = self._get_keywords(root)
            service_provider = self._get_text(
                root, ".//ows:ServiceProvider/ows:ProviderName"
            )
            service_contact = self._get_text(
                root,
                ".//ows:ServiceProvider/ows:ServiceContact/ows:ContactInfo/ows:ContactPersonPrimary/ows:ContactPerson",
            )

            operations: list[str] = []
            for op in root.findall(".//ows:Operation", self.namespaces):
                op_name = op.get("name")
                if op_name:
                    operations.append(op_name)

            supported_formats = self._parse_supported_formats(root)
            supported_crs = self._parse_supported_crs(root)
            coverages = self._parse_coverages(root)

            return ServiceCapabilities(
                service_title=service_title or "WCS Service",
                service_abstract=service_abstract,
                service_keywords=service_keywords,
                service_provider=service_provider,
                service_contact=service_contact,
                service_url=self.base_url,
                supported_operations=operations,
                supported_formats=supported_formats,
                supported_crs=supported_crs,
                coverages=coverages,
            )
        except ET.ParseError as exc:  # pragma: no cover - defensive
            raise ParseError(f"Invalid XML content: {exc}", cause=exc) from exc

    def parse_describe_coverage(self, xml_content: str) -> CoverageDescription:
        if looks_like_html(xml_content):
            raise ParseError(
                "DescribeCoverage response looks like HTML (login/portal page), "
                "not WCS XML. Check base_url and credentials via params=/headers=; "
                "this is unlikely a coverage_id typo."
            )
        try:
            root = ET.fromstring(xml_content)

            coverage_elem = root.find(".//wcs:CoverageDescription", self.namespaces)
            if coverage_elem is None and root.tag.lower().endswith(
                "coveragedescription"
            ):
                coverage_elem = root
            if coverage_elem is None:
                raise ParseError(
                    "No coverage description found in XML "
                    "(empty response or OGC ExceptionReport). "
                    "Hint: call get_capabilities() and check CoverageId values."
                )

            identifier = self._get_text(coverage_elem, ".//gml:identifier")
            if not identifier:
                identifier = self._get_text(coverage_elem, ".//wcs:CoverageId")
            if not identifier:
                raise ParseError(
                    "Coverage identifier not found in DescribeCoverage XML. "
                    "Hint: call get_capabilities() and check CoverageId values."
                )

            title = self._get_text(coverage_elem, ".//gml:name")
            abstract = self._get_text(coverage_elem, ".//gml:description")
            keywords = self._get_keywords(coverage_elem)
            supported_crs = self._parse_coverage_crs(coverage_elem)
            supported_formats = self._parse_coverage_formats(coverage_elem)
            spatial_extent = self._parse_spatial_extent(coverage_elem)
            temporal_extent = self._parse_temporal_extent(coverage_elem)
            native_crs, axis_labels = self._parse_envelope_metadata(coverage_elem)
            native_format = self._parse_native_format(coverage_elem)
            native_resolution = self._parse_native_resolution(coverage_elem)

            if native_format and native_format not in supported_formats:
                supported_formats.append(native_format)
            if native_crs and native_crs not in supported_crs:
                supported_crs.append(native_crs)

            return CoverageDescription(
                identifier=identifier,
                title=title,
                abstract=abstract,
                keywords=keywords,
                supported_crs=supported_crs,
                supported_formats=supported_formats,
                spatial_extent=spatial_extent,
                temporal_extent=temporal_extent,
                native_crs=native_crs,
                axis_labels=axis_labels,
                native_format=native_format,
                native_resolution=native_resolution,
            )
        except ET.ParseError as exc:  # pragma: no cover - defensive
            raise ParseError(f"Invalid XML content: {exc}", cause=exc) from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _get_text(self, element: ET.Element, xpath: str) -> str | None:
        elem = element.find(xpath, self.namespaces)
        return elem.text.strip() if elem is not None and elem.text else None

    def _get_keywords(self, element: ET.Element) -> list[str]:
        keywords: list[str] = []
        for kw_elem in element.findall(".//ows:Keywords/ows:Keyword", self.namespaces):
            if kw_elem.text:
                keywords.append(kw_elem.text.strip())
        return keywords

    def _parse_supported_formats(self, root: ET.Element) -> list[Format]:
        formats: list[Format] = []
        for format_elem in root.findall(".//wcs:SupportedFormat", self.namespaces):
            if format_elem.text:
                text = format_elem.text.strip()
                try:
                    formats.append(Format(text))
                except ValueError:
                    logger.debug("Skipping unsupported WCS format '%s'", text)
        for format_elem in root.findall(".//wcs:formatSupported", self.namespaces):
            if format_elem.text:
                text = format_elem.text.strip()
                try:
                    fmt = Format(text)
                except ValueError:
                    logger.debug("Skipping unsupported WCS format '%s'", text)
                else:
                    if fmt not in formats:
                        formats.append(fmt)
        return formats

    def _parse_supported_crs(self, root: ET.Element) -> list[CRS]:
        crs_list: list[CRS] = []
        for crs_elem in root.findall(".//wcs:SupportedCRS", self.namespaces):
            if crs_elem.text:
                coerced = _coerce_crs_enum(crs_elem.text.strip())
                if coerced is not None and coerced not in crs_list:
                    crs_list.append(coerced)
        for crs_elem in root.findall(".//crs:crsSupported", self.namespaces):
            if crs_elem.text:
                coerced = _coerce_crs_enum(crs_elem.text.strip())
                if coerced is not None and coerced not in crs_list:
                    crs_list.append(coerced)
        return crs_list

    def _parse_coverages(self, root: ET.Element) -> list[CoverageDescription]:
        coverages: list[CoverageDescription] = []
        for coverage_elem in root.findall(
            ".//wcs:Contents/wcs:CoverageSummary", self.namespaces
        ):
            identifier = self._get_text(coverage_elem, ".//wcs:Identifier")
            if not identifier:
                identifier = self._get_text(coverage_elem, ".//wcs:CoverageId")
            if identifier:
                title = self._get_text(coverage_elem, ".//wcs:Title")
                if not title:
                    title = self._get_text(coverage_elem, ".//ows:Title")
                abstract = self._get_text(coverage_elem, ".//wcs:Abstract")
                if not abstract:
                    abstract = self._get_text(coverage_elem, ".//ows:Abstract")
                coverages.append(
                    CoverageDescription(
                        identifier=identifier,
                        title=title,
                        abstract=abstract,
                        keywords=self._get_keywords(coverage_elem),
                        native_crs=None,
                        native_format=None,
                        native_resolution=None,
                    )
                )
        return coverages

    def _parse_coverage_crs(self, coverage_elem: ET.Element) -> list[CRS]:
        crs_list: list[CRS] = []
        for crs_elem in coverage_elem.findall(".//wcs:SupportedCRS", self.namespaces):
            if crs_elem.text:
                coerced = _coerce_crs_enum(crs_elem.text.strip())
                if coerced is not None and coerced not in crs_list:
                    crs_list.append(coerced)
        return crs_list

    def _parse_coverage_formats(self, coverage_elem: ET.Element) -> list[Format]:
        formats: list[Format] = []
        for format_elem in coverage_elem.findall(
            ".//wcs:SupportedFormat", self.namespaces
        ):
            if format_elem.text:
                text = format_elem.text.strip()
                try:
                    formats.append(Format(text))
                except ValueError:
                    logger.debug("Skipping unsupported format '%s'", text)
        return formats

    def _find_envelope(self, coverage_elem: ET.Element) -> ET.Element | None:
        envelope = coverage_elem.find(".//gml:boundedBy/gml:Envelope", self.namespaces)
        if envelope is None:
            envelope = coverage_elem.find(".//gml:Envelope", self.namespaces)
        return envelope

    def _parse_envelope_metadata(
        self, coverage_elem: ET.Element
    ) -> tuple[CRS | None, dict[str, tuple[str, str]]]:
        envelope = self._find_envelope(coverage_elem)
        if envelope is None:
            return None, {}

        srs_name = envelope.get("srsName")
        axis_labels_raw = envelope.get("axisLabels")
        if not srs_name:
            return None, {}

        normalized_crs = normalize_crs_reference(srs_name)
        native_crs = _coerce_crs_enum(normalized_crs)
        axis_labels: dict[str, tuple[str, str]] = {}
        if axis_labels_raw:
            parts = axis_labels_raw.split()
            if len(parts) >= 2:
                axis_labels[normalized_crs] = (parts[0], parts[1])
        return native_crs, axis_labels

    def _parse_native_format(self, coverage_elem: ET.Element) -> Format | None:
        native_format_elem = coverage_elem.find(
            ".//wcs:ServiceParameters/wcs:nativeFormat", self.namespaces
        )
        if native_format_elem is None or not native_format_elem.text:
            return None
        text = native_format_elem.text.strip()
        try:
            return Format(text)
        except ValueError:
            logger.debug("Skipping unsupported native format '%s'", text)
            return None

    def _parse_native_resolution(
        self, coverage_elem: ET.Element
    ) -> tuple[float, float] | None:
        """Ground sample distance from RectifiedGrid ``offsetVector`` magnitudes."""

        vectors = coverage_elem.findall(
            ".//gml:domainSet//gml:offsetVector", self.namespaces
        )
        if len(vectors) < 2:
            vectors = coverage_elem.findall(".//gml:offsetVector", self.namespaces)
        if len(vectors) < 2:
            return None

        parsed: list[list[float]] = []
        for elem in vectors[:2]:
            text = (elem.text or "").strip()
            if not text:
                return None
            try:
                parsed.append([float(part) for part in text.split()])
            except ValueError:
                return None
        if len(parsed[0]) < 2 or len(parsed[1]) < 2:
            return None

        # offsetVector components are typically (dx, 0) and (0, dy); take
        # Euclidean magnitude so rotated grids still yield positive GSD.
        res_a = (parsed[0][0] ** 2 + parsed[0][1] ** 2) ** 0.5
        res_b = (parsed[1][0] ** 2 + parsed[1][1] ** 2) ** 0.5
        if res_a <= 0 or res_b <= 0:
            return None
        return (res_a, res_b)

    def _parse_spatial_extent(self, coverage_elem: ET.Element) -> SpatialExtent | None:
        envelope = self._find_envelope(coverage_elem)
        if envelope is None:
            return None

        lower_corner = envelope.find(".//gml:lowerCorner", self.namespaces)
        upper_corner = envelope.find(".//gml:upperCorner", self.namespaces)
        if not (lower_corner is not None and upper_corner is not None):
            return None

        lower_text = (lower_corner.text or "").strip()
        upper_text = (upper_corner.text or "").strip()
        if not lower_text or not upper_text:
            return None

        try:
            lower_coords = [float(x) for x in lower_text.split()]
            upper_coords = [float(x) for x in upper_text.split()]
        except (AttributeError, ValueError):
            return None

        if len(lower_coords) < 2 or len(upper_coords) < 2:
            return None

        srs_name = envelope.get("srsName")
        bbox_crs = _coerce_crs_enum(normalize_crs_reference(srs_name or ""))
        if bbox_crs is None:
            bbox_crs = self._parse_native_crs(coverage_elem)

        axis_labels_raw = envelope.get("axisLabels")
        axis_parts = axis_labels_raw.split() if axis_labels_raw else []
        min_x, min_y, max_x, max_y = self._envelope_corners_to_xy(
            lower_coords,
            upper_coords,
            axis_parts=axis_parts,
            crs=bbox_crs,
        )
        bbox = BoundingBox(
            min_x=min_x,
            min_y=min_y,
            max_x=max_x,
            max_y=max_y,
            crs=bbox_crs,
        )
        return SpatialExtent(bbox=bbox, dimensions=None)

    @staticmethod
    def _envelope_corners_to_xy(
        lower_coords: list[float],
        upper_coords: list[float],
        *,
        axis_parts: list[str],
        crs: CRS | None,
    ) -> tuple[float, float, float, float]:
        """Map envelope corners to always_xy mins/maxes using ``axisLabels``."""

        if len(axis_parts) < 2:
            return (
                lower_coords[0],
                lower_coords[1],
                upper_coords[0],
                upper_coords[1],
            )

        values = [0.0, 0.0, 0.0, 0.0]  # min_x, min_y, max_x, max_y
        north_first = CRS.parse(crs).north_axis_first if crs is not None else False
        for index, label in enumerate(axis_parts[:2]):
            northing = label_is_northing(label, crs)
            if northing is None:
                northing = (index == 0) if north_first else (index == 1)
            lo = lower_coords[index]
            hi = upper_coords[index]
            if northing:
                values[1] = min(lo, hi)
                values[3] = max(lo, hi)
            else:
                values[0] = min(lo, hi)
                values[2] = max(lo, hi)
        return values[0], values[1], values[2], values[3]

    def _parse_temporal_extent(
        self, coverage_elem: ET.Element
    ) -> TemporalExtent | None:
        time_elem = coverage_elem.find(".//gml:TimePeriod", self.namespaces)
        if time_elem is None:
            return None

        begin_elem = time_elem.find(".//gml:beginPosition", self.namespaces)
        end_elem = time_elem.find(".//gml:endPosition", self.namespaces)

        start_time = self._parse_datetime(
            begin_elem.text if begin_elem is not None else None
        )
        end_time = self._parse_datetime(end_elem.text if end_elem is not None else None)

        if start_time or end_time:
            return TemporalExtent(start_time=start_time, end_time=end_time)
        return None

    def _parse_datetime(self, value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            logger.debug("Failed to parse datetime '%s'", value)
            return None

    def _parse_native_crs(self, coverage_elem: ET.Element) -> CRS:
        native_crs, _ = self._parse_envelope_metadata(coverage_elem)
        if native_crs is not None:
            return native_crs

        native_crs_elem = coverage_elem.find(".//wcs:NativeCRS", self.namespaces)
        if native_crs_elem is not None and native_crs_elem.text:
            coerced = _coerce_crs_enum(native_crs_elem.text.strip())
            if coerced is not None:
                return coerced
        return CRS.EPSG_4326


@register_service(ServiceTypeEnum.WCS)
class WCSService(BaseService):
    """Client for interacting with WCS endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        version: str = "2.0.1",
        client: httpx.Client | None = None,
        coverage_id: str | None = None,
        output_format: Format | None = None,
        crs: CRS | None = None,
        **config: Any,
    ) -> None:
        super().__init__(base_url, version=version, **config)
        self._client = client
        self.version = version
        self.coverage_id = coverage_id or config.get("layer_id")
        self.parser = WCSParser(self.base_url)
        self._coverage_metadata: dict[str, CoverageDescription] = {}
        self.output_format = self._coerce_format(
            output_format or config.get("format") or Format.GEOTIFF
        )
        self.subsetting_crs = CRS.parse(crs or config.get("crs") or CRS.EPSG_4326)

    @classmethod
    def from_url(cls, url: str, **config: Any) -> WCSService:
        return cls(url, **config)

    def set_coverage_metadata(self, description: CoverageDescription) -> None:
        """Cache DescribeCoverage metadata for GetCoverage request building."""

        self._coverage_metadata[description.identifier] = description

    def coverage_metadata(
        self, coverage_id: str | None = None
    ) -> CoverageDescription | None:
        coverage = coverage_id or self.coverage_id
        if not coverage:
            return None
        return self._coverage_metadata.get(coverage)

    def ensure_coverage_metadata(
        self, coverage_id: str | None = None, **params: Any
    ) -> CoverageDescription:
        coverage = coverage_id or self._require_coverage_id()
        cached = self._coverage_metadata.get(coverage)
        if cached is not None:
            return cached
        description = self.describe_coverage(coverage, **params)
        self.set_coverage_metadata(description)
        return description

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def _service_get(
        self,
        params: dict[str, Any],
        *,
        extra_params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """GET against ``base_url`` with shared UA/timeout and config credentials."""

        return http_get(
            self.base_url,
            params=compose_http_params(params, self.config, extra_params),
            headers=compose_http_headers(self.config),
            timeout=resolve_timeout(self.config),
            client=self._client,
            retries=resolve_retries(self.config),
        )

    def get_capabilities(
        self, *, refresh: bool = False, **params: Any
    ) -> ServiceCapabilities:
        request_params = compose_http_params(
            {
                "service": "WCS",
                "version": self.version,
                "request": "GetCapabilities",
            },
            self.config,
            params or None,
        )
        headers = compose_http_headers(self.config)
        try:
            body = http_get_capabilities_text(
                self.base_url,
                params=request_params,
                headers=headers,
                timeout=resolve_timeout(self.config),
                client=self._client,
                retries=resolve_retries(self.config),
                refresh=refresh,
            )
        except httpx.HTTPError as exc:
            raise NetworkError(
                format_http_failure(
                    exc,
                    request_name="GetCapabilities",
                    url=self.base_url,
                    protocol="WCS",
                    params=config_params_for_errors(self.config),
                ),
                cause=exc,
            ) from exc
        return self.parser.parse_get_capabilities(body)

    def describe_coverage(
        self,
        coverage_id: str | None = None,
        *,
        refresh: bool = False,
        **params: Any,
    ) -> CoverageDescription:
        coverage = coverage_id or self._require_coverage_id()
        request_params = compose_http_params(
            {
                "service": "WCS",
                "version": self.version,
                "request": "DescribeCoverage",
                "coverageId": coverage,
            },
            self.config,
            params or None,
        )
        headers = compose_http_headers(self.config)
        try:
            body = http_get_capabilities_text(
                self.base_url,
                params=request_params,
                headers=headers,
                timeout=resolve_timeout(self.config),
                client=self._client,
                retries=resolve_retries(self.config),
                refresh=refresh,
            )
        except httpx.HTTPError as exc:
            raise NetworkError(
                format_http_failure(
                    exc,
                    request_name="DescribeCoverage",
                    url=self.base_url,
                    protocol="WCS",
                    coverage_id=coverage,
                    params=config_params_for_errors(self.config),
                ),
                cause=exc,
            ) from exc
        try:
            description = self.parser.parse_describe_coverage(body)
        except ParseError as exc:
            raise ParseError(
                describe_coverage_parse_message(
                    coverage,
                    self.base_url,
                    body,
                    content_type=None,
                    parse_error=exc,
                ),
                cause=exc.cause or exc,
            ) from exc
        self.set_coverage_metadata(description)
        return description

    def get_coverage(
        self,
        coverage_id: str | None,
        bbox: BoundingBox,
        width: int,
        height: int,
        *,
        output_format: Format | None = None,
        crs: CRS | None = None,
        **params: Any,
    ) -> WCSResponse:
        coverage = coverage_id or self._require_coverage_id()
        metadata = self.ensure_coverage_metadata(coverage)
        fmt = self._coerce_format(
            output_format
            or self.output_format
            or metadata.native_format
            or Format.GEOTIFF
        )
        subset_crs, subset_bbox, subset_parts, axis_labels = self._resolve_subset(
            bbox, crs or self.subsetting_crs, metadata
        )
        clipped = self._intersect_coverage_extent(subset_bbox, subset_crs, metadata)
        if clipped is None:
            raise ValidationError(self._outside_coverage_message(coverage))
        req_width, req_height = width, height
        size_bbox = subset_bbox
        if (
            clipped.min_x != subset_bbox.min_x
            or clipped.min_y != subset_bbox.min_y
            or clipped.max_x != subset_bbox.max_x
            or clipped.max_y != subset_bbox.max_y
        ):
            res_x = (subset_bbox.max_x - subset_bbox.min_x) / width
            res_y = (subset_bbox.max_y - subset_bbox.min_y) / height
            req_width = max(1, int(round((clipped.max_x - clipped.min_x) / res_x)))
            req_height = max(1, int(round((clipped.max_y - clipped.min_y) / res_y)))
            subset_parts = self._format_subset(clipped, axis_labels)
            size_bbox = clipped
        output_raw = params.pop("output_crs", None)

        request_params: dict[str, Any] = {
            "service": "WCS",
            "version": self.version,
            "request": "GetCoverage",
            "coverageId": coverage,
            "subset": subset_parts,
            "format": fmt.value,
        }
        request_params.update(
            self._getcoverage_size_params(
                axis_labels=axis_labels,
                width=req_width,
                height=req_height,
                bbox=size_bbox,
                metadata=metadata,
                subset_crs=subset_crs,
            )
        )
        if metadata.native_crs is None or subset_crs != metadata.native_crs:
            request_params["subsettingCRS"] = subset_crs.value
        if output_raw is not None:
            request_params["outputCRS"] = CRS.parse(output_raw).value

        try:
            response = self._service_get(request_params, extra_params=params or None)
            return WCSResponse(
                success=True,
                data=response.content,
                error_message=None,
                status_code=response.status_code,
            )
        except httpx.HTTPError as exc:
            logger.debug("WCS GetCoverage failed: %s", exc, exc_info=True)
            status_code = (
                exc.response.status_code
                if isinstance(exc, httpx.HTTPStatusError)
                else None
            )
            error_message = str(exc)
            if "scaleSize" in request_params:
                error_message = (
                    f"{error_message}; scaled GetCoverage (scaleSize) failed — "
                    "server may not support WCS Scaling"
                )
            return WCSResponse(
                success=False,
                data=None,
                error_message=error_message,
                status_code=status_code,
            )

    # ------------------------------------------------------------------
    # BaseService overrides
    # ------------------------------------------------------------------
    def generate_tile_requests(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: object,
    ) -> MosaicPlan:
        """Plan tiles; fail loud when the whole AOI misses the coverage extent."""

        coverage = options.get("coverage_id") or self.coverage_id
        if coverage:
            self.ensure_coverage_metadata(str(coverage))
        plan = super().generate_tile_requests(bbox, chunk_size, **options)
        tiles = list(plan.tiles)
        if tiles and all(tile.coverage_empty for tile in tiles):
            raise ValidationError(
                self._outside_coverage_message(str(coverage) if coverage else None)
            )
        return plan

    def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
        coverage = options.get("coverage_id") or self.coverage_id
        if not coverage:
            raise ValidationError("WCS coverage_id must be provided")

        metadata = self._metadata_or_stub(coverage)
        fmt = self._coerce_format(
            options.get("output_format") or self.output_format or metadata.native_format
        )
        requested_crs = CRS.parse(options.get("crs") or tile.crs)
        subset_crs, subset_bbox, subset_parts, axis_labels = self._resolve_subset(
            tile.bbox, requested_crs, metadata
        )
        output_raw = options.get("output_crs")
        result_crs = CRS.parse(output_raw) if output_raw is not None else subset_crs
        clipped = self._intersect_coverage_extent(subset_bbox, subset_crs, metadata)

        # Canvas stays the planned tile; HTTP subset may be clipped or skipped.
        canvas_tile = TileGeometry(
            bbox=subset_bbox,
            width=tile.width,
            height=tile.height,
            crs=subset_crs,
            tile_x=tile.tile_x,
            tile_y=tile.tile_y,
            zoom=tile.zoom,
        )
        passthrough = tile_option_passthrough(
            options,
            "output_crs",
            "supported_crs",
            "coverage_id",
        )

        if clipped is None:
            # Wholly outside the envelope — no GetCoverage subset that the
            # server would reject; mosaic loader fills coverage nodata.
            request = self.compose_tile_request(
                canvas_tile,
                url=self.base_url,
                protocol_params={
                    "service": "WCS",
                    "version": self.version,
                    "request": "GetCoverage",
                    "coverageId": coverage,
                    "format": fmt.value,
                },
                output_format=fmt,
                crs=result_crs,
                **passthrough,
            )
            return request.model_copy(update={"coverage_empty": True})

        req_width = tile.width
        req_height = tile.height
        size_bbox = subset_bbox
        if (
            clipped.min_x != subset_bbox.min_x
            or clipped.min_y != subset_bbox.min_y
            or clipped.max_x != subset_bbox.max_x
            or clipped.max_y != subset_bbox.max_y
        ):
            res_x = (subset_bbox.max_x - subset_bbox.min_x) / tile.width
            res_y = (subset_bbox.max_y - subset_bbox.min_y) / tile.height
            req_width = max(1, int(round((clipped.max_x - clipped.min_x) / res_x)))
            req_height = max(1, int(round((clipped.max_y - clipped.min_y) / res_y)))
            subset_parts = self._format_subset(clipped, axis_labels)
            size_bbox = clipped

        protocol_params: dict[str, Any] = {
            "service": "WCS",
            "version": self.version,
            "request": "GetCoverage",
            "coverageId": coverage,
            "subset": subset_parts,
            "format": fmt.value,
        }
        protocol_params.update(
            self._getcoverage_size_params(
                axis_labels=axis_labels,
                width=req_width,
                height=req_height,
                bbox=size_bbox,
                metadata=metadata,
                subset_crs=subset_crs,
            )
        )
        if metadata.native_crs is None or subset_crs != metadata.native_crs:
            protocol_params["subsettingCRS"] = subset_crs.value
        if output_raw is not None:
            protocol_params["outputCRS"] = result_crs.value

        request = self.compose_tile_request(
            canvas_tile,
            url=self.base_url,
            protocol_params=protocol_params,
            output_format=fmt,
            crs=result_crs,
            **passthrough,
        )
        # Fail fast on scaled GetCoverage: servers that hang or 500 on
        # scaleSize must not burn every retry (#177).
        if "scaleSize" in protocol_params:
            return request.model_copy(update={"retries": 0})
        return request

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_coverage_id(self) -> str:
        if not self.coverage_id:
            raise ValidationError("WCS coverage_id is required but was not provided")
        return self.coverage_id

    def _metadata_or_stub(self, coverage_id: str) -> CoverageDescription:
        cached = self.coverage_metadata(coverage_id)
        if cached is not None:
            return cached
        return CoverageDescription(
            identifier=coverage_id,
            native_crs=None,
            native_format=None,
            native_resolution=None,
        )

    def _coerce_format(self, fmt: Any) -> Format:
        try:
            return Format.coerce(fmt)
        except ValueError as exc:
            if isinstance(fmt, str):
                meta = self.coverage_metadata()
                raise ValidationError(
                    unsupported_format_message(
                        "WCS",
                        fmt,
                        server_formats=None if meta is None else meta.supported_formats,
                        native_format=None if meta is None else meta.native_format,
                    )
                ) from exc
            raise ValidationError(f"Invalid WCS format value: {fmt!r}") from exc

    def _resolve_subset(
        self,
        bbox: BoundingBox,
        requested_crs: CRS,
        metadata: CoverageDescription,
    ) -> tuple[CRS, BoundingBox, list[str], tuple[str, str]]:
        subset_crs = requested_crs
        subset_bbox = bbox if bbox.crs == requested_crs else bbox.to_crs(requested_crs)
        axis_labels = metadata.axis_labels.get(requested_crs.value)

        if axis_labels is None and metadata.native_crs is not None:
            native_key = metadata.native_crs.value
            native_axes = metadata.axis_labels.get(native_key)
            if native_axes is not None:
                subset_crs = metadata.native_crs
                subset_bbox = subset_bbox.to_crs(subset_crs)
                axis_labels = native_axes

        if axis_labels is None:
            axis_labels = self._default_subset_axes(subset_crs)

        return (
            subset_crs,
            subset_bbox,
            self._format_subset(subset_bbox, axis_labels),
            axis_labels,
        )

    def _intersect_coverage_extent(
        self,
        subset_bbox: BoundingBox,
        subset_crs: CRS,
        metadata: CoverageDescription,
    ) -> BoundingBox | None:
        """Return ``subset ∩ coverage`` in ``subset_crs``, or ``None`` if disjoint.

        When DescribeCoverage did not advertise a spatial extent, return the
        subset unchanged (no client-side clip).
        """

        extent = metadata.spatial_extent
        if extent is None:
            return subset_bbox
        coverage_bbox = extent.bbox
        if coverage_bbox.crs != subset_crs:
            coverage_bbox = coverage_bbox.to_crs(subset_crs)
        return subset_bbox.intersection(coverage_bbox)

    def _outside_coverage_message(self, coverage_id: str | None) -> str:
        meta = self.coverage_metadata(coverage_id) if coverage_id else None
        extent = (
            meta.spatial_extent.bbox
            if meta is not None and meta.spatial_extent is not None
            else None
        )
        if extent is None:
            return (
                "Requested bbox does not intersect the coverage spatial extent; "
                "no tiles to fetch."
            )
        return (
            "Requested bbox does not intersect coverage extent "
            f"({extent.min_x}, {extent.min_y}, {extent.max_x}, {extent.max_y}) "
            f"in {extent.crs.value}."
        )

    def _default_subset_axes(self, crs: CRS) -> tuple[str, str]:
        return CRS.parse(crs).default_subset_axes()

    @staticmethod
    def _label_is_northing(label: str, crs: CRS | None = None) -> bool | None:
        """Classify a subset/scale axis label as northing/lat when possible."""

        return label_is_northing(label, crs)

    def _format_subset(
        self, bbox: BoundingBox, axis_labels: tuple[str, str]
    ) -> list[str]:
        # BoundingBox is always_xy; map each label by axis meaning so lat-first
        # caps labels like ("y", "x") get northing under y and easting under x.
        parts: list[str] = []
        for index, label in enumerate(axis_labels):
            northing = label_is_northing(label, bbox.crs)
            if northing is None:
                # Fall back: CRS axis order when labels are opaque.
                north_first = CRS.parse(bbox.crs).north_axis_first
                northing = (index == 0) if north_first else (index == 1)
            if northing:
                parts.append(f"{label}({bbox.min_y},{bbox.max_y})")
            else:
                parts.append(f"{label}({bbox.min_x},{bbox.max_x})")
        return parts

    def _format_scale_size(
        self,
        axis_labels: tuple[str, str],
        width: int,
        height: int,
        *,
        crs: CRS | None = None,
    ) -> str:
        """WCS 2 Scaling ``scaleSize`` using the same labels/meaning as subsets."""

        parts: list[str] = []
        for index, label in enumerate(axis_labels):
            northing = label_is_northing(label, crs)
            if northing is None:
                # Match `_format_subset`: CRS axis order when labels are opaque.
                north_first = (
                    CRS.parse(crs).north_axis_first if crs is not None else False
                )
                northing = (index == 0) if north_first else (index == 1)
            size = height if northing else width
            parts.append(f"{label}({size})")
        return ",".join(parts)

    @staticmethod
    def _requested_resolution(
        bbox: BoundingBox, width: int, height: int
    ) -> tuple[float, float]:
        return (
            (bbox.max_x - bbox.min_x) / width,
            (bbox.max_y - bbox.min_y) / height,
        )

    @staticmethod
    def _resolutions_match(
        requested: tuple[float, float],
        native: tuple[float, float],
        *,
        relative_tol: float = 1e-3,
        absolute_tol: float = 1e-6,
    ) -> bool:
        for req, nat in zip(requested, native):
            if abs(req - nat) > max(absolute_tol, relative_tol * abs(nat)):
                return False
        return True

    def _is_native_output_size(
        self,
        bbox: BoundingBox,
        width: int,
        height: int,
        metadata: CoverageDescription,
    ) -> bool:
        """True when the requested pixel grid matches DescribeCoverage native GSD."""

        native = metadata.native_resolution
        if native is None:
            return False
        return self._resolutions_match(
            self._requested_resolution(bbox, width, height), native
        )

    def _getcoverage_size_params(
        self,
        *,
        axis_labels: tuple[str, str],
        width: int,
        height: int,
        bbox: BoundingBox,
        metadata: CoverageDescription,
        subset_crs: CRS,
    ) -> dict[str, str]:
        """Size keys for WCS 2 GetCoverage.

        At native resolution omit ``scaleSize`` / ``width`` / ``height`` — some
        hosts (EA LiDAR) hang or 500 when Scaling is requested even at native
        size (#177). When the output grid differs from native, send ``scaleSize``
        (MapServer / #154) and keep vendor ``width``/``height`` for ArcGIS.
        When native GSD is unknown, keep sending size params so MapServer still
        gets ``scaleSize``.
        """

        if self._is_native_output_size(bbox, width, height, metadata):
            return {}
        return {
            "width": str(width),
            "height": str(height),
            "scaleSize": self._format_scale_size(
                axis_labels, width, height, crs=subset_crs
            ),
        }
