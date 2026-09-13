"""WMTS (Web Map Tile Service) parsing and tile request functionality."""

from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import requests

from ..types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest
from .base import BaseService, TileGeometry, register_service
from .xyz import _tile_bounds, _tile_range_for_bbox

logger = logging.getLogger(__name__)


@dataclass
class WMTSTileMatrix:
    """One zoom level within a WMTS TileMatrixSet."""

    identifier: str
    scale_denominator: float
    top_left_x: float
    top_left_y: float
    matrix_width: int
    matrix_height: int
    tile_width: int
    tile_height: int


@dataclass
class WMTSTileMatrixSet:
    """Tile matrix set metadata used for tile planning."""

    identifier: str
    supported_crs: CRS
    matrices: dict[str, WMTSTileMatrix] = field(default_factory=dict)


@dataclass
class WMTSLayerInfo:
    """Layer metadata extracted from GetCapabilities."""

    identifier: str
    resource_url_template: str | None = None
    tile_matrix_set_links: list[str] = field(default_factory=list)


@dataclass
class WMTSCapabilities:
    """Parsed WMTS GetCapabilities payload."""

    layers: dict[str, WMTSLayerInfo] = field(default_factory=dict)
    tile_matrix_sets: dict[str, WMTSTileMatrixSet] = field(default_factory=dict)


class WMTSParser:
    """Parser for WMTS GetCapabilities XML documents."""

    def __init__(self) -> None:
        self.namespaces = {
            "wmts": "http://www.opengis.net/wmts/1.0",
            "ows": "http://www.opengis.net/ows/1.1",
        }

    def parse_get_capabilities(self, xml_content: str) -> WMTSCapabilities:
        try:
            root = ET.fromstring(xml_content)
        except ET.ParseError as exc:  # pragma: no cover - defensive
            raise ValueError(f"Invalid XML content: {exc}") from exc

        capabilities = WMTSCapabilities()
        for tms_elem in root.findall(".//wmts:TileMatrixSet", self.namespaces):
            tms = self._parse_tile_matrix_set(tms_elem)
            if tms is not None:
                capabilities.tile_matrix_sets[tms.identifier] = tms

        for layer_elem in root.findall(".//wmts:Layer", self.namespaces):
            layer = self._parse_layer(layer_elem)
            if layer is not None:
                capabilities.layers[layer.identifier] = layer

        return capabilities

    def _parse_tile_matrix_set(self, element: ET.Element) -> WMTSTileMatrixSet | None:
        identifier = self._text(element, "ows:Identifier")
        if not identifier:
            return None

        crs_text = self._text(element, "ows:SupportedCRS")
        supported_crs = CRS.EPSG_3857
        if crs_text:
            normalized = crs_text.strip()
            if normalized.upper().endswith("4326") or "CRS:84" in normalized.upper():
                supported_crs = CRS.EPSG_4326
            elif "3857" in normalized:
                supported_crs = CRS.EPSG_3857

        tms = WMTSTileMatrixSet(identifier=identifier, supported_crs=supported_crs)
        for matrix_elem in element.findall("wmts:TileMatrix", self.namespaces):
            matrix = self._parse_tile_matrix(matrix_elem)
            if matrix is not None:
                tms.matrices[matrix.identifier] = matrix
        return tms

    def _parse_tile_matrix(self, element: ET.Element) -> WMTSTileMatrix | None:
        identifier = self._text(element, "ows:Identifier")
        if not identifier:
            return None

        scale_text = self._text(element, "wmts:ScaleDenominator")
        top_left_text = self._text(element, "wmts:TopLeftCorner")
        if not scale_text or not top_left_text:
            return None

        try:
            scale_denominator = float(scale_text)
            top_left_parts = [float(part) for part in top_left_text.split()]
            matrix_width = int(self._text(element, "wmts:MatrixWidth") or "0")
            matrix_height = int(self._text(element, "wmts:MatrixHeight") or "0")
            tile_width = int(self._text(element, "wmts:TileWidth") or "256")
            tile_height = int(self._text(element, "wmts:TileHeight") or "256")
        except ValueError:
            return None

        if len(top_left_parts) < 2 or matrix_width <= 0 or matrix_height <= 0:
            return None

        return WMTSTileMatrix(
            identifier=identifier,
            scale_denominator=scale_denominator,
            top_left_x=top_left_parts[0],
            top_left_y=top_left_parts[1],
            matrix_width=matrix_width,
            matrix_height=matrix_height,
            tile_width=tile_width,
            tile_height=tile_height,
        )

    def _parse_layer(self, element: ET.Element) -> WMTSLayerInfo | None:
        identifier = self._text(element, "ows:Identifier")
        if not identifier:
            return None

        resource_template: str | None = None
        for resource_elem in element.findall("wmts:ResourceURL", self.namespaces):
            if resource_elem.get("resourceType") == "tile":
                resource_template = resource_elem.get("template")
                if resource_template:
                    break

        tms_links = [
            link.text.strip()
            for link in element.findall(
                "wmts:TileMatrixSetLink/wmts:TileMatrixSet", self.namespaces
            )
            if link.text
        ]

        return WMTSLayerInfo(
            identifier=identifier,
            resource_url_template=resource_template,
            tile_matrix_set_links=tms_links,
        )

    def _text(self, element: ET.Element, xpath: str) -> str | None:
        child = element.find(xpath, self.namespaces)
        if child is not None and child.text:
            return child.text.strip()
        return None


@register_service(ServiceTypeEnum.WMTS)
class WMTSService(BaseService):
    """Client for WMTS GetTile endpoints (REST template or KVP)."""

    inferred_grid_shape: tuple[int, int] | None = None

    def __init__(
        self,
        base_url: str,
        *,
        layer: str,
        tile_matrix_set: str,
        tile_matrix: int,
        style: str = "",
        output_format: Format | None = Format.PNG,
        crs: CRS | None = CRS.EPSG_4326,
        url_template: str | None = None,
        version: str = "1.0.0",
        session: requests.Session | None = None,
        **config: Any,
    ) -> None:
        super().__init__(
            base_url,
            layer=layer,
            tile_matrix_set=tile_matrix_set,
            tile_matrix=tile_matrix,
            **config,
        )
        if not layer:
            raise ValueError("WMTS layer must be provided")
        if not tile_matrix_set:
            raise ValueError("WMTS tile_matrix_set must be provided")
        if tile_matrix < 0:
            raise ValueError("tile_matrix must be non-negative")

        self.layer = layer
        self.tile_matrix_set = tile_matrix_set
        self.tile_matrix = tile_matrix
        self.style = style
        self.version = version
        self.output_format = self._coerce_format(
            output_format or config.get("format") or Format.PNG
        )
        self.native_crs = self._coerce_crs(crs or config.get("crs") or CRS.EPSG_4326)
        self.url_template = url_template or config.get("url_template")
        self.session = session or requests.Session()
        self.parser = WMTSParser()
        self._capabilities: WMTSCapabilities | None = None

    @classmethod
    def from_url(cls, url: str, **config: Any) -> WMTSService:
        if config.get("layer") is None:
            raise ValueError("WMTS services require a layer")
        if config.get("tile_matrix_set") is None:
            raise ValueError("WMTS services require a tile_matrix_set")
        if config.get("tile_matrix") is None:
            raise ValueError("WMTS services require a tile_matrix")
        return cls(url, **config)

    def get_capabilities(self, **params: Any) -> WMTSCapabilities:
        response = self.session.get(
            self.base_url,
            params={
                "service": "WMTS",
                "version": self.version,
                "request": "GetCapabilities",
                **params,
            },
        )
        response.raise_for_status()
        self._capabilities = self.parser.parse_get_capabilities(response.text)
        return self._capabilities

    def apply_capabilities(self, capabilities: WMTSCapabilities | None = None) -> None:
        """Fill missing REST template / matrix metadata from GetCapabilities."""

        caps = capabilities or self._capabilities
        if caps is None:
            caps = self.get_capabilities()

        layer_info = caps.layers.get(self.layer)
        if layer_info and self.url_template is None:
            self.url_template = layer_info.resource_url_template

    def plan_tiles(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: object,
    ) -> Iterable[TileGeometry]:
        tile_matrix_value = options.get("tile_matrix", self.tile_matrix)
        tile_matrix = (
            int(tile_matrix_value)
            if isinstance(tile_matrix_value, (int, str))
            else self.tile_matrix
        )

        tms_id = str(options.get("tile_matrix_set") or self.tile_matrix_set)
        matrix = self._resolve_matrix(tms_id, tile_matrix, options)

        tile_width = matrix.tile_width if matrix else chunk_size[0]
        tile_height = matrix.tile_height if matrix else chunk_size[1]
        target_crs = options.get("crs", self.native_crs)
        if not isinstance(target_crs, CRS):
            target_crs = self.native_crs

        use_web_mercator = matrix is None or self._is_web_mercator_matrix(
            matrix, target_crs
        )
        if use_web_mercator and target_crs in {CRS.EPSG_4326, CRS.EPSG_3857}:
            return self._plan_web_mercator_tiles(
                bbox,
                tile_matrix,
                tile_width,
                tile_height,
                target_crs,
            )

        if matrix is not None:
            return self._plan_matrix_tiles(bbox, matrix, target_crs)

        return super().plan_tiles(bbox, chunk_size, **options)

    def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
        layer = str(options.get("layer") or self.layer)
        tms = str(options.get("tile_matrix_set") or self.tile_matrix_set)
        style = str(
            options.get("style") if options.get("style") is not None else self.style
        )
        fmt = self._coerce_format(options.get("output_format") or self.output_format)
        crs = self._coerce_crs(options.get("crs") or tile.crs or self.native_crs)

        tile_col = _require_index(tile.tile_x, options.get("tile_col"))
        tile_row = _require_index(tile.tile_y, options.get("tile_row"))
        matrix_level = int(
            options.get(
                "tile_matrix",
                tile.zoom if tile.zoom is not None else self.tile_matrix,
            )
        )

        template = options.get("url_template") or self.url_template
        if template:
            url = _format_rest_template(
                template,
                layer=layer,
                style=style,
                tile_matrix_set=tms,
                tile_matrix=str(matrix_level),
                tile_row=str(tile_row),
                tile_col=str(tile_col),
                fmt=fmt,
            )
            passthrough = {
                key: value
                for key, value in options.items()
                if key
                not in {
                    "output_format",
                    "crs",
                    "layer",
                    "style",
                    "tile_matrix_set",
                    "tile_matrix",
                    "url_template",
                }
            }
            return self.compose_tile_request(
                tile,
                url=url,
                output_format=fmt,
                crs=crs,
                **passthrough,
            )

        params: dict[str, Any] = {
            "service": "WMTS",
            "request": "GetTile",
            "version": self.version,
            "layer": layer,
            "style": style,
            "format": fmt.value,
            "TileMatrixSet": tms,
            "TileMatrix": str(matrix_level),
            "TileRow": str(tile_row),
            "TileCol": str(tile_col),
        }

        passthrough = {
            key: value
            for key, value in options.items()
            if key
            not in {
                "output_format",
                "crs",
                "layer",
                "style",
                "tile_matrix_set",
                "tile_matrix",
                "url_template",
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

    def _resolve_matrix(
        self,
        tile_matrix_set: str,
        tile_matrix: int,
        options: object,
    ) -> WMTSTileMatrix | None:
        if isinstance(options, dict):
            matrix_override = options.get("matrix")
            if isinstance(matrix_override, WMTSTileMatrix):
                return matrix_override

        caps = self._capabilities
        if caps is None:
            return None

        tms = caps.tile_matrix_sets.get(tile_matrix_set)
        if tms is None:
            return None

        matrix_key = str(tile_matrix)
        if matrix_key in tms.matrices:
            return tms.matrices[matrix_key]
        return tms.matrices.get(f"EPSG4326:{tile_matrix}") or tms.matrices.get(
            f"GoogleMapsCompatible:{tile_matrix}"
        )

    def _is_web_mercator_matrix(self, matrix: WMTSTileMatrix, target_crs: CRS) -> bool:
        return target_crs in {CRS.EPSG_4326, CRS.EPSG_3857} and (
            matrix.top_left_x <= -180.0 or matrix.top_left_x <= -20037508.0
        )

    def _plan_web_mercator_tiles(
        self,
        bbox: BoundingBox,
        zoom: int,
        tile_width: int,
        tile_height: int,
        target_crs: CRS,
    ) -> Iterable[TileGeometry]:
        working_bbox = bbox if bbox.crs == CRS.EPSG_4326 else bbox.to_crs(CRS.EPSG_4326)
        x_min, y_min, x_max, y_max = _tile_range_for_bbox(working_bbox, zoom)

        rows = y_max - y_min + 1
        cols = x_max - x_min + 1
        self.inferred_grid_shape = (rows, cols)

        for tile_y in range(y_min, y_max + 1):
            for tile_x in range(x_min, x_max + 1):
                tile_bbox = _tile_bounds(tile_x, tile_y, zoom, target_crs)
                yield TileGeometry(
                    bbox=tile_bbox,
                    width=tile_width,
                    height=tile_height,
                    crs=target_crs,
                    tile_x=tile_x,
                    tile_y=tile_y,
                    zoom=zoom,
                )

    def _plan_matrix_tiles(
        self,
        bbox: BoundingBox,
        matrix: WMTSTileMatrix,
        target_crs: CRS,
    ) -> Iterable[TileGeometry]:
        pixel_size = matrix.scale_denominator * 0.00028  # OGC standard pixel size (mm)

        span_x = abs(pixel_size * matrix.tile_width)
        span_y = abs(pixel_size * matrix.tile_height)

        min_col = _matrix_index(
            bbox.min_x, matrix.top_left_x, span_x, matrix.matrix_width, reverse=False
        )
        max_col = _matrix_index(
            bbox.max_x, matrix.top_left_x, span_x, matrix.matrix_width, reverse=False
        )
        min_row = _matrix_index(
            bbox.max_y, matrix.top_left_y, span_y, matrix.matrix_height, reverse=True
        )
        max_row = _matrix_index(
            bbox.min_y, matrix.top_left_y, span_y, matrix.matrix_height, reverse=True
        )

        rows = max_row - min_row + 1
        cols = max_col - min_col + 1
        self.inferred_grid_shape = (rows, cols)

        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                if matrix.top_left_y >= 0:
                    max_y = matrix.top_left_y - row * span_y
                    min_y = max_y - span_y
                else:
                    min_y = matrix.top_left_y + row * span_y
                    max_y = min_y + span_y

                min_x = matrix.top_left_x + col * span_x
                max_x = min_x + span_x

                yield TileGeometry(
                    bbox=BoundingBox(
                        min_x=min_x,
                        min_y=min_y,
                        max_x=max_x,
                        max_y=max_y,
                        crs=target_crs,
                    ),
                    width=matrix.tile_width,
                    height=matrix.tile_height,
                    crs=target_crs,
                    tile_x=col,
                    tile_y=row,
                    zoom=int(matrix.identifier)
                    if matrix.identifier.isdigit()
                    else self.tile_matrix,
                )

    def _coerce_format(self, fmt: Any) -> Format:
        if isinstance(fmt, Format):
            return fmt
        if isinstance(fmt, str):
            try:
                return Format(fmt)
            except ValueError as exc:
                raise ValueError(f"Unsupported WMTS format: {fmt}") from exc
        raise ValueError(f"Invalid WMTS format value: {fmt!r}")

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


def _require_index(value: int | None, override: int | None) -> int:
    resolved = override if override is not None else value
    if resolved is None:
        raise ValueError("WMTS tile indices are required")
    return int(resolved)


def _format_rest_template(
    template: str,
    *,
    layer: str,
    style: str,
    tile_matrix_set: str,
    tile_matrix: str,
    tile_row: str,
    tile_col: str,
    fmt: Format,
) -> str:
    extension = _format_extension(fmt)
    replacements = {
        "{Layer}": quote(layer, safe=""),
        "{Style}": quote(style, safe=""),
        "{TileMatrixSet}": quote(tile_matrix_set, safe=""),
        "{TileMatrix}": tile_matrix,
        "{TileRow}": tile_row,
        "{TileCol}": tile_col,
        "{Format}": quote(fmt.value, safe=""),
    }
    url = template
    for token, value in replacements.items():
        url = url.replace(token, value)
    if "{FormatExtension}" in url:
        url = url.replace("{FormatExtension}", extension)
    return url


def _format_extension(fmt: Format) -> str:
    if fmt == Format.PNG:
        return "png"
    if fmt == Format.JPEG:
        return "jpg"
    if fmt == Format.GEOTIFF:
        return "tif"
    raise ValueError(f"Unsupported format: {fmt}")


def _matrix_index(
    coordinate: float,
    origin: float,
    span: float,
    limit: int,
    *,
    reverse: bool,
) -> int:
    if span <= 0:
        return 0
    if reverse:
        raw = math.floor((origin - coordinate) / span)
    else:
        raw = math.floor((coordinate - origin) / span)
    return max(0, min(int(raw), limit - 1))
