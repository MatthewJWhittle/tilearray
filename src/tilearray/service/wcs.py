"""WCS (Web Coverage Service) XML parsing and tile request functionality."""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Any

import requests

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
)
from .base import BaseService, TileGeometry, register_service

logger = logging.getLogger(__name__)

_OGC_EPSG_URI_RE = re.compile(r"/EPSG/(?:0/)?(\d+)/?$", re.I)


def normalize_crs_reference(value: str) -> str:
    """Normalize OGC URI or EPSG shorthand to ``EPSG:<code>``."""

    text = value.strip()
    upper = text.upper()
    if upper.startswith("EPSG:"):
        code = text.split(":", 1)[1].split("/")[0]
        return f"EPSG:{code}"
    match = _OGC_EPSG_URI_RE.search(text)
    if match:
        return f"EPSG:{match.group(1)}"
    return text


def _coerce_crs_enum(value: str | None) -> CRS | None:
    if not value:
        return None
    normalized = normalize_crs_reference(value)
    try:
        return CRS(normalized)
    except ValueError:
        try:
            return CRS.from_epsg(normalized)
        except ValueError:
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
            raise ValueError(f"Invalid XML content: {exc}") from exc

    def parse_describe_coverage(self, xml_content: str) -> CoverageDescription:
        try:
            root = ET.fromstring(xml_content)

            coverage_elem = root.find(".//wcs:CoverageDescription", self.namespaces)
            if coverage_elem is None and root.tag.lower().endswith(
                "coveragedescription"
            ):
                coverage_elem = root
            if coverage_elem is None:
                raise ValueError("No coverage description found in XML")

            identifier = self._get_text(coverage_elem, ".//gml:identifier")
            if not identifier:
                identifier = self._get_text(coverage_elem, ".//wcs:CoverageId")
            if not identifier:
                raise ValueError("Coverage identifier not found")

            title = self._get_text(coverage_elem, ".//gml:name")
            abstract = self._get_text(coverage_elem, ".//gml:description")
            keywords = self._get_keywords(coverage_elem)
            supported_crs = self._parse_coverage_crs(coverage_elem)
            supported_formats = self._parse_coverage_formats(coverage_elem)
            spatial_extent = self._parse_spatial_extent(coverage_elem)
            temporal_extent = self._parse_temporal_extent(coverage_elem)
            native_crs, axis_labels = self._parse_envelope_metadata(coverage_elem)
            native_format = self._parse_native_format(coverage_elem)

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
            )
        except ET.ParseError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid XML content: {exc}") from exc

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
                text = crs_elem.text.strip()
                try:
                    crs_list.append(CRS(text))
                except ValueError:
                    logger.debug("Skipping unsupported CRS '%s'", text)
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
                coverages.append(
                    CoverageDescription(
                        identifier=identifier,
                        title=self._get_text(coverage_elem, ".//wcs:Title"),
                        abstract=self._get_text(coverage_elem, ".//wcs:Abstract"),
                        keywords=self._get_keywords(coverage_elem),
                        native_crs=None,
                        native_format=None,
                    )
                )
        return coverages

    def _parse_coverage_crs(self, coverage_elem: ET.Element) -> list[CRS]:
        crs_list: list[CRS] = []
        for crs_elem in coverage_elem.findall(".//wcs:SupportedCRS", self.namespaces):
            if crs_elem.text:
                text = crs_elem.text.strip()
                try:
                    crs_list.append(CRS(text))
                except ValueError:
                    logger.debug("Skipping unsupported CRS '%s'", text)
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

        bbox = BoundingBox(
            min_x=lower_coords[0],
            min_y=lower_coords[1],
            max_x=upper_coords[0],
            max_y=upper_coords[1],
            crs=bbox_crs,
        )
        return SpatialExtent(bbox=bbox, dimensions=None)

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
        session: requests.Session | None = None,
        coverage_id: str | None = None,
        output_format: Format | None = None,
        crs: CRS | None = None,
        **config: Any,
    ) -> None:
        super().__init__(base_url, version=version, **config)
        self.session = session or requests.Session()
        self.version = version
        self.coverage_id = coverage_id or config.get("layer_id")
        self.parser = WCSParser(self.base_url)
        self.output_format = self._coerce_format(
            output_format or config.get("format") or Format.GEOTIFF
        )
        self.subsetting_crs = self._coerce_crs(
            crs or config.get("crs") or CRS.EPSG_4326
        )
        self._coverage_metadata: dict[str, CoverageDescription] = {}

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
    def get_capabilities(self, **params: Any) -> ServiceCapabilities:
        response = self.session.get(
            self.base_url,
            params={
                "service": "WCS",
                "version": self.version,
                "request": "GetCapabilities",
                **params,
            },
        )
        response.raise_for_status()
        return self.parser.parse_get_capabilities(response.text)

    def describe_coverage(
        self, coverage_id: str | None = None, **params: Any
    ) -> CoverageDescription:
        coverage = coverage_id or self._require_coverage_id()
        response = self.session.get(
            self.base_url,
            params={
                "service": "WCS",
                "version": self.version,
                "request": "DescribeCoverage",
                "coverageId": coverage,
                **params,
            },
        )
        response.raise_for_status()
        description = self.parser.parse_describe_coverage(response.text)
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
        subset_crs, subset_bbox, subset_parts = self._resolve_subset(
            bbox, crs or self.subsetting_crs, metadata
        )

        request_params = {
            "service": "WCS",
            "version": self.version,
            "request": "GetCoverage",
            "coverageId": coverage,
            "subset": subset_parts,
            "format": fmt.value,
            "width": str(width),
            "height": str(height),
            "subsettingCRS": subset_crs.value,
            **params,
        }

        try:
            response = self.session.get(self.base_url, params=request_params)
            response.raise_for_status()
            return WCSResponse(
                success=True,
                data=response.content,
                error_message=None,
                status_code=response.status_code,
            )
        except requests.RequestException as exc:
            logger.debug("WCS GetCoverage failed: %s", exc, exc_info=True)
            status_code = exc.response.status_code if exc.response is not None else None
            return WCSResponse(
                success=False,
                data=None,
                error_message=str(exc),
                status_code=status_code,
            )

    # ------------------------------------------------------------------
    # BaseService overrides
    # ------------------------------------------------------------------
    def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
        coverage = options.get("coverage_id") or self.coverage_id
        if not coverage:
            raise ValueError("WCS coverage_id must be provided")

        metadata = self._metadata_or_stub(coverage)
        fmt = self._coerce_format(
            options.get("output_format") or self.output_format or metadata.native_format
        )
        requested_crs = self._coerce_crs(options.get("crs") or tile.crs)
        subset_crs, subset_bbox, subset_parts = self._resolve_subset(
            tile.bbox, requested_crs, metadata
        )
        subset_tile = TileGeometry(
            bbox=subset_bbox,
            width=tile.width,
            height=tile.height,
            crs=subset_crs,
            tile_x=tile.tile_x,
            tile_y=tile.tile_y,
            zoom=tile.zoom,
        )

        params: dict[str, Any] = {
            "service": "WCS",
            "version": self.version,
            "request": "GetCoverage",
            "coverageId": coverage,
            "subset": subset_parts,
            "format": fmt.value,
            "width": str(tile.width),
            "height": str(tile.height),
            "subsettingCRS": subset_crs.value,
        }

        passthrough = {
            key: value
            for key, value in options.items()
            if key not in {"output_format", "crs"}
        }
        return self.compose_tile_request(
            subset_tile,
            url=self.base_url,
            params=params,
            output_format=fmt,
            crs=subset_crs,
            **passthrough,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_coverage_id(self) -> str:
        if not self.coverage_id:
            raise ValueError("WCS coverage_id is required but was not provided")
        return self.coverage_id

    def _metadata_or_stub(self, coverage_id: str) -> CoverageDescription:
        cached = self.coverage_metadata(coverage_id)
        if cached is not None:
            return cached
        return CoverageDescription(
            identifier=coverage_id,
            native_crs=None,
            native_format=None,
        )

    def _coerce_format(self, fmt: Any) -> Format:
        if isinstance(fmt, Format):
            return fmt
        if isinstance(fmt, str):
            try:
                return Format(fmt)
            except ValueError as exc:
                raise ValueError(f"Unsupported WCS format: {fmt}") from exc
        raise ValueError(f"Invalid WCS format value: {fmt!r}")

    def _coerce_crs(self, crs: Any) -> CRS:
        if isinstance(crs, CRS):
            return crs
        if isinstance(crs, str):
            return (
                CRS.from_epsg(crs)
                if crs.upper().startswith("EPSG:")
                else CRS.from_integer(int(crs))
            )
        if isinstance(crs, int):
            return CRS.from_integer(crs)
        raise ValueError(f"Invalid CRS value: {crs!r}")

    def _resolve_subset(
        self,
        bbox: BoundingBox,
        requested_crs: CRS,
        metadata: CoverageDescription,
    ) -> tuple[CRS, BoundingBox, list[str]]:
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

        return subset_crs, subset_bbox, self._format_subset(subset_bbox, axis_labels)

    def _default_subset_axes(self, crs: CRS) -> tuple[str, str]:
        if crs == CRS.EPSG_27700:
            return ("E", "N")
        if crs == CRS.EPSG_3857:
            return ("x", "y")
        return ("Long", "Lat")

    def _format_subset(
        self, bbox: BoundingBox, axis_labels: tuple[str, str]
    ) -> list[str]:
        axis_x, axis_y = axis_labels
        return [
            f"{axis_x}({bbox.min_x},{bbox.max_x})",
            f"{axis_y}({bbox.min_y},{bbox.max_y})",
        ]
