"""WCS (Web Coverage Service) XML parsing and tile request functionality."""

from __future__ import annotations

import logging
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


class WCSParser:
    """Parser for WCS XML responses."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.namespaces = {
            "wcs": "http://www.opengis.net/wcs/2.0",
            "ows": "http://www.opengis.net/ows/1.1",
            "gml": "http://www.opengis.net/gml/3.2",
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

            return CoverageDescription(
                identifier=identifier,
                title=title,
                abstract=abstract,
                keywords=keywords,
                supported_crs=supported_crs,
                supported_formats=supported_formats,
                spatial_extent=spatial_extent,
                temporal_extent=temporal_extent,
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

    def _parse_spatial_extent(self, coverage_elem: ET.Element) -> SpatialExtent | None:
        bbox_elem = coverage_elem.find(".//gml:Envelope", self.namespaces)
        if bbox_elem is None:
            return None

        lower_corner = bbox_elem.find(".//gml:lowerCorner", self.namespaces)
        upper_corner = bbox_elem.find(".//gml:upperCorner", self.namespaces)
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

        bbox = BoundingBox(
            min_x=lower_coords[0],
            min_y=lower_coords[1],
            max_x=upper_coords[0],
            max_y=upper_coords[1],
            crs=self._parse_native_crs(coverage_elem),
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
        native_crs_elem = coverage_elem.find(".//wcs:NativeCRS", self.namespaces)
        if native_crs_elem is not None and native_crs_elem.text:
            try:
                return CRS(native_crs_elem.text.strip())
            except ValueError:
                logger.debug("Unsupported native CRS '%s'", native_crs_elem.text)
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

    @classmethod
    def from_url(cls, url: str, **config: Any) -> WCSService:
        return cls(url, **config)

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
        return self.parser.parse_describe_coverage(response.text)

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
        fmt = self._coerce_format(output_format or self.output_format)
        subset_crs = self._coerce_crs(crs or self.subsetting_crs)
        subset_parts = self._format_subset(bbox, subset_crs)

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

        fmt = self._coerce_format(options.get("output_format") or self.output_format)
        crs = self._coerce_crs(options.get("crs") or tile.crs)
        subset_parts = self._format_subset(tile.bbox, crs)

        params: dict[str, Any] = {
            "service": "WCS",
            "version": self.version,
            "request": "GetCoverage",
            "coverageId": coverage,
            "subset": subset_parts,
            "format": fmt.value,
            "width": str(tile.width),
            "height": str(tile.height),
            "subsettingCRS": crs.value,
        }

        passthrough = {
            key: value
            for key, value in options.items()
            if key not in {"output_format", "crs"}
        }
        return self.compose_tile_request(
            tile,
            url=self.base_url,
            params=params,
            output_format=fmt,
            crs=crs,
            **passthrough,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _require_coverage_id(self) -> str:
        if not self.coverage_id:
            raise ValueError("WCS coverage_id is required but was not provided")
        return self.coverage_id

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

    def _subset_axes(self, crs: CRS) -> tuple[str, str]:
        if crs == CRS.EPSG_27700:
            return ("E", "N")
        if crs == CRS.EPSG_3857:
            return ("X", "Y")
        return ("Long", "Lat")

    def _format_subset(self, bbox: BoundingBox, crs: CRS) -> list[str]:
        axis_x, axis_y = self._subset_axes(crs)
        return [
            f"{axis_x}({bbox.min_x},{bbox.max_x})",
            f"{axis_y}({bbox.min_y},{bbox.max_y})",
        ]
