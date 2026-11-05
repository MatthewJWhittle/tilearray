"""WMS (Web Map Service) tile request functionality."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any

from ..error_messages import unsupported_format_message
from ..errors import ParseError, ValidationError
from ..types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest
from .base import BaseService, TileGeometry, register_service


@dataclass
class WMSLayerInfo:
    """Layer metadata extracted from WMS GetCapabilities."""

    name: str
    title: str | None = None
    crs: list[str] = field(default_factory=list)
    styles: list[str] = field(default_factory=list)


@dataclass
class WMSCapabilities:
    """Parsed WMS GetCapabilities payload (inspection-oriented)."""

    version: str = "1.3.0"
    title: str | None = None
    layers: list[WMSLayerInfo] = field(default_factory=list)
    formats: list[str] = field(default_factory=list)


class WMSParser:
    """Parser for WMS GetCapabilities XML (layers, CRS, GetMap formats)."""

    def __init__(self) -> None:
        self.namespaces = {
            "wms": "http://www.opengis.net/wms",
            "ows": "http://www.opengis.net/ows/1.1",
            "xlink": "http://www.w3.org/1999/xlink",
        }

    def parse_get_capabilities(self, xml_content: str) -> WMSCapabilities:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:
            raise ParseError(f"Invalid XML content: {exc}", cause=exc) from exc

        version = root.get("version") or "1.3.0"
        title = self._first_text(
            root,
            (
                "./wms:Service/wms:Title",
                "./Service/Title",
                ".//{http://www.opengis.net/wms}Service/{http://www.opengis.net/wms}Title",
            ),
        )
        formats = self._parse_getmap_formats(root)
        layers = self._parse_layers(root)
        return WMSCapabilities(
            version=version, title=title, layers=layers, formats=formats
        )

    def _parse_getmap_formats(self, root: ET.Element) -> list[str]:
        formats: list[str] = []
        for xpath in (
            ".//wms:Capability/wms:Request/wms:GetMap/wms:Format",
            ".//Capability/Request/GetMap/Format",
        ):
            for elem in root.findall(xpath, self.namespaces):
                if elem.text and elem.text.strip() and elem.text.strip() not in formats:
                    formats.append(elem.text.strip())
        if formats:
            return formats
        # Namespace-agnostic fallback: Format siblings under a GetMap parent.
        for elem in root.iter():
            if elem.tag.rsplit("}", 1)[-1] != "GetMap":
                continue
            for child in elem:
                if child.tag.rsplit("}", 1)[-1] != "Format":
                    continue
                if (
                    child.text
                    and child.text.strip()
                    and child.text.strip() not in formats
                ):
                    formats.append(child.text.strip())
        return formats

    def _parse_layers(self, root: ET.Element) -> list[WMSLayerInfo]:
        layers: list[WMSLayerInfo] = []
        seen: set[str] = set()
        layer_elems = root.findall(".//wms:Layer", self.namespaces) or root.findall(
            ".//Layer"
        )
        for layer_elem in layer_elems:
            name = self._child_text(layer_elem, ("Name", "wms:Name"))
            if not name or name in seen:
                continue
            seen.add(name)
            title = self._child_text(layer_elem, ("Title", "wms:Title"))
            crs_values = self._layer_crs(layer_elem)
            styles = [
                style_name
                for style_elem in list(layer_elem)
                if style_elem.tag.rsplit("}", 1)[-1] == "Style"
                for style_name in [self._child_text(style_elem, ("Name", "wms:Name"))]
                if style_name
            ]
            layers.append(
                WMSLayerInfo(name=name, title=title, crs=crs_values, styles=styles)
            )
        return layers

    def _layer_crs(self, layer_elem: ET.Element) -> list[str]:
        values: list[str] = []
        for child in layer_elem:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag in {"CRS", "SRS"} and child.text and child.text.strip():
                text = child.text.strip()
                if text not in values:
                    values.append(text)
        return values

    def _child_text(self, element: ET.Element, names: tuple[str, ...]) -> str | None:
        wanted = {name.rsplit(":", 1)[-1] for name in names}
        for child in element:
            if child.tag.rsplit("}", 1)[-1] in wanted and child.text:
                return child.text.strip()
        return None

    def _first_text(self, root: ET.Element, xpaths: tuple[str, ...]) -> str | None:
        for xpath in xpaths:
            elem = root.find(xpath, self.namespaces)
            if elem is not None and elem.text and elem.text.strip():
                return elem.text.strip()
        return None


def format_wms_bbox(bbox: BoundingBox, crs: CRS, version: str) -> str:
    """
    Format a bounding box for WMS GetMap.

    WMS 1.3.0 follows CRS axis order (north/south axis first for geographic
    CRS such as EPSG:4326). Earlier versions always use minx,miny,maxx,maxy.
    """

    target = CRS.parse(crs)
    if version.startswith("1.3") and target.north_axis_first:
        return f"{bbox.min_y},{bbox.min_x},{bbox.max_y},{bbox.max_x}"

    return f"{bbox.min_x},{bbox.min_y},{bbox.max_x},{bbox.max_y}"


@register_service(ServiceTypeEnum.WMS)
class WMSService(BaseService):
    """Client for WMS GetMap endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        layers: str,
        version: str = "1.3.0",
        styles: str = "",
        output_format: Format | None = Format.PNG,
        crs: CRS | None = CRS.EPSG_4326,
        **config: Any,
    ) -> None:
        super().__init__(
            base_url,
            layers=layers,
            version=version,
            styles=styles,
            **config,
        )
        if not layers:
            raise ValidationError("WMS layers must be provided")
        self.layers = layers
        self.version = version
        self.styles = styles
        self.output_format = self._coerce_format(
            output_format or config.get("format") or Format.PNG
        )
        self.native_crs = CRS.parse(crs or config.get("crs") or CRS.EPSG_4326)

    @classmethod
    def from_url(cls, url: str, **config: Any) -> WMSService:
        layers = config.get("layers")
        if not layers:
            raise ValidationError("WMS services require layers")
        return cls(url, **config)

    def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
        layers = str(options.get("layers") or self.layers)
        if not layers:
            raise ValidationError("WMS layers must be provided")

        version = str(options.get("version") or self.version)
        styles = str(
            options.get("styles") if options.get("styles") is not None else self.styles
        )
        fmt = self._coerce_format(options.get("output_format") or self.output_format)
        # GetMap has one CRS for BBOX and rendered pixels. Prefer explicit
        # server-side output_crs when set (never a client-side warp).
        crs = CRS.parse(
            options.get("output_crs")
            or options.get("crs")
            or tile.crs
            or self.native_crs
        )
        # Tile bbox must already be in the GetMap CRS — we never reproject here.
        # A mismatch previously swapped axes for geographic CRS and sent metres
        # as lat/lon. Planning (create_array) reprojects before this call.
        bbox_crs = tile.bbox.crs
        if bbox_crs is not None and CRS.parse(bbox_crs) != crs:
            raise ValidationError(
                f"WMS GetMap crs={crs.value} does not match tile bbox CRS "
                f"{CRS.parse(bbox_crs).value}. Reproject the AOI to the GetMap "
                "CRS before build_tile_request (create_array / plan_array do this "
                "when output_crs= is set), or omit output_crs=."
            )

        params: dict[str, Any] = {
            "service": "WMS",
            "request": "GetMap",
            "version": version,
            "layers": layers,
            "styles": styles,
            "crs" if version.startswith("1.3") else "srs": crs.value,
            "bbox": format_wms_bbox(tile.bbox, crs, version),
            "width": str(tile.width),
            "height": str(tile.height),
            "format": fmt.value,
            "transparent": "true",
        }

        passthrough = {
            key: value
            for key, value in options.items()
            if key
            not in {
                "output_format",
                "crs",
                "output_crs",
                "supported_crs",
                "layers",
                "styles",
                "version",
            }
        }
        return self.compose_tile_request(
            tile,
            url=self.base_url,
            params=params,
            output_format=fmt,
            crs=crs,
            **passthrough,
        )

    def _coerce_format(self, fmt: Any) -> Format:
        try:
            return Format.coerce(fmt)
        except ValueError as exc:
            if isinstance(fmt, str):
                raise ValidationError(unsupported_format_message("WMS", fmt)) from exc
            raise ValidationError(f"Invalid WMS format value: {fmt!r}") from exc

    def _coerce_crs(self, crs: Any) -> CRS:
        return CRS.parse(crs)
