"""WMTS (Web Map Tile Service) parsing and tile request functionality."""

from __future__ import annotations

import logging
import math
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote

import httpx

from ..error_messages import format_http_failure, unsupported_format_message
from ..errors import NetworkError, ParseError, ValidationError
from ..types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest
from .base import BaseService, TileGeometry, register_service
from .http import (
    compose_http_headers,
    compose_http_params,
    http_get,
    resolve_timeout,
)
from .xyz import _tile_bounds, _tile_range_for_bbox

logger = logging.getLogger(__name__)

# OGC 07-057r7 standardised rendering pixel size (0.28 mm → metres).
_OGC_PIXEL_SIZE_METRES = 0.00028

# TileMatrixSet name fragments that may use XYZ-style planning without caps.
_SLIPPY_TMS_HINTS = (
    "googlemapscompatible",
    "webmercatorquad",
    "webmercator",
    "epsg3857",
    "3857",
    "epsg4326",
    "4326",
    "crs84",
    "googlecrs84",
)


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
class WMTSTileMatrixLimits:
    """Optional per-matrix tile index limits from a layer TileMatrixSetLink."""

    tile_matrix: str
    min_tile_row: int
    max_tile_row: int
    min_tile_col: int
    max_tile_col: int


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
    # tile_matrix_set → matrix id → limits
    matrix_limits: dict[str, dict[str, WMTSTileMatrixLimits]] = field(
        default_factory=dict
    )


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
            raise ParseError(f"Invalid XML content: {exc}", cause=exc) from exc

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
        if not crs_text:
            raise ValidationError(
                f"WMTS TileMatrixSet {identifier!r} is missing SupportedCRS; "
                "cannot plan a native tile grid without the matrix CRS."
            )
        supported_crs = _parse_supported_crs(crs_text.strip(), tms_id=identifier)

        tms = WMTSTileMatrixSet(identifier=identifier, supported_crs=supported_crs)
        for matrix_elem in element.findall("wmts:TileMatrix", self.namespaces):
            matrix = self._parse_tile_matrix(matrix_elem, supported_crs)
            if matrix is not None:
                tms.matrices[matrix.identifier] = matrix
        return tms

    def _parse_tile_matrix(
        self, element: ET.Element, supported_crs: CRS
    ) -> WMTSTileMatrix | None:
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

        top_left_x, top_left_y = _top_left_xy(
            top_left_parts[0], top_left_parts[1], supported_crs
        )

        return WMTSTileMatrix(
            identifier=identifier,
            scale_denominator=scale_denominator,
            top_left_x=top_left_x,
            top_left_y=top_left_y,
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

        tms_links: list[str] = []
        matrix_limits: dict[str, dict[str, WMTSTileMatrixLimits]] = {}
        for link_elem in element.findall("wmts:TileMatrixSetLink", self.namespaces):
            tms_elem = link_elem.find("wmts:TileMatrixSet", self.namespaces)
            if tms_elem is None or not tms_elem.text:
                continue
            tms_id = tms_elem.text.strip()
            tms_links.append(tms_id)
            limits_for_tms = _parse_matrix_limits(link_elem, self.namespaces)
            if limits_for_tms:
                matrix_limits[tms_id] = limits_for_tms

        return WMTSLayerInfo(
            identifier=identifier,
            resource_url_template=resource_template,
            tile_matrix_set_links=tms_links,
            matrix_limits=matrix_limits,
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
    inferred_mosaic_bbox: BoundingBox | None = None

    def __init__(
        self,
        base_url: str,
        *,
        layer: str,
        tile_matrix_set: str,
        tile_matrix: int | str,
        style: str = "",
        output_format: Format | None = Format.PNG,
        crs: CRS | None = CRS.EPSG_4326,
        url_template: str | None = None,
        version: str = "1.0.0",
        client: httpx.Client | None = None,
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
            raise ValidationError("WMTS layer must be provided")
        if not tile_matrix_set:
            raise ValidationError("WMTS tile_matrix_set must be provided")
        resolved_matrix = _coerce_tile_matrix_id(tile_matrix)
        if resolved_matrix == "":
            raise ValidationError("tile_matrix must be a non-empty identifier")

        self.layer = layer
        self.tile_matrix_set = tile_matrix_set
        self.tile_matrix = resolved_matrix
        self.style = style
        self.version = version
        self.output_format = self._coerce_format(
            output_format or config.get("format") or Format.PNG
        )
        self.native_crs = self._coerce_crs(crs or config.get("crs") or CRS.EPSG_4326)
        self.url_template = url_template or config.get("url_template")
        self._client = client
        self.parser = WMTSParser()
        self._capabilities: WMTSCapabilities | None = None

    @classmethod
    def from_url(cls, url: str, **config: Any) -> WMTSService:
        if config.get("layer") is None:
            raise ValidationError("WMTS services require a layer")
        if config.get("tile_matrix_set") is None:
            raise ValidationError("WMTS services require a tile_matrix_set")
        if config.get("tile_matrix") is None:
            raise ValidationError("WMTS services require a tile_matrix")
        return cls(url, **config)

    def get_capabilities(self, **params: Any) -> WMTSCapabilities:
        try:
            response = http_get(
                self.base_url,
                params=compose_http_params(
                    {
                        "service": "WMTS",
                        "version": self.version,
                        "request": "GetCapabilities",
                    },
                    self.config,
                    params or None,
                ),
                headers=compose_http_headers(self.config),
                timeout=resolve_timeout(self.config),
                client=self._client,
            )
        except httpx.HTTPError as exc:
            raise NetworkError(
                format_http_failure(
                    exc,
                    request_name="GetCapabilities",
                    url=self.base_url,
                    protocol="WMTS",
                    layer=self.layer,
                ),
                cause=exc,
            ) from exc
        self._capabilities = self.parser.parse_get_capabilities(response.text)
        return self._capabilities

    def apply_capabilities(self, capabilities: WMTSCapabilities | None = None) -> None:
        """Fill missing REST template / matrix metadata from GetCapabilities."""

        caps = capabilities or self._capabilities
        if caps is None:
            caps = self.get_capabilities()
        else:
            self._capabilities = caps

        layer_info = caps.layers.get(self.layer)
        if layer_info and self.url_template is None:
            self.url_template = layer_info.resource_url_template

    def plan_tiles(
        self,
        bbox: BoundingBox,
        chunk_size: tuple[int, int],
        **options: object,
    ) -> Iterable[TileGeometry]:
        self.inferred_mosaic_bbox = None
        tile_matrix_id = _coerce_tile_matrix_id(
            options.get("tile_matrix", self.tile_matrix)
        )
        tms_id = str(options.get("tile_matrix_set") or self.tile_matrix_set)

        matrix_override = None
        if isinstance(options, dict):
            maybe_override = options.get("matrix")
            if isinstance(maybe_override, WMTSTileMatrix):
                matrix_override = maybe_override

        query_crs = options.get("crs", self.native_crs)
        if not isinstance(query_crs, CRS):
            query_crs = self.native_crs

        # Native / non-slippy grids need TileMatrixSet metadata. Load
        # GetCapabilities once so create_array(config, bbox, crs) works without a
        # separate apply_capabilities() step (same habit as WCS DescribeCoverage).
        if (
            matrix_override is None
            and self._capabilities is None
            and not _allows_slippy_fallback(tms_id, query_crs)
        ):
            self.apply_capabilities()

        caps = self._capabilities
        has_caps = caps is not None or matrix_override is not None

        if has_caps:
            tms, matrix = self._require_matrix(
                tms_id, tile_matrix_id, matrix_override=matrix_override
            )
            matrix_crs = (
                tms.supported_crs
                if tms is not None
                else self._matrix_override_crs(options)
            )
            limits = self._layer_limits(tms_id, matrix.identifier)
            return self._plan_matrix_tiles(
                bbox,
                matrix,
                matrix_crs,
                limits=limits,
            )

        # No capabilities: keep XYZ-style planning for common Web Mercator / CRS84
        # slippy hosts. Non-Mercator grids need TileMatrixSet metadata.
        if _allows_slippy_fallback(tms_id, query_crs):
            tile_width = chunk_size[0]
            tile_height = chunk_size[1]
            try:
                zoom = int(tile_matrix_id)
            except ValueError as exc:
                raise ValidationError(
                    f"WMTS tile_matrix {tile_matrix_id!r} is not numeric and no "
                    "GetCapabilities TileMatrix metadata is loaded. Call "
                    "get_capabilities()/apply_capabilities() or pass matrix= with "
                    "native TileMatrix fields."
                ) from exc
            return self._plan_web_mercator_tiles(
                bbox,
                zoom,
                tile_width,
                tile_height,
                query_crs
                if query_crs in {CRS.EPSG_4326, CRS.EPSG_3857}
                else CRS.EPSG_4326,
            )

        raise ValidationError(
            f"WMTS TileMatrixSet {tms_id!r} needs native matrix metadata "
            f"(SupportedCRS / ScaleDenominator / TopLeftCorner). Load "
            "GetCapabilities via get_capabilities()/apply_capabilities(), or pass "
            "matrix= with those fields. tilearray will not guess a Web Mercator grid."
        )

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
        # Prefer the identifier resolved during planning (e.g. Kartverket "08")
        # over the caller's numeric tile_matrix=8 from config tile_kwargs.
        if tile.tile_matrix_id is not None:
            matrix_level = _coerce_tile_matrix_id(tile.tile_matrix_id)
        else:
            matrix_level = _coerce_tile_matrix_id(
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
                tile_matrix=matrix_level,
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
            "TileMatrix": matrix_level,
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

    def _require_matrix(
        self,
        tile_matrix_set: str,
        tile_matrix: str,
        *,
        matrix_override: WMTSTileMatrix | None,
    ) -> tuple[WMTSTileMatrixSet | None, WMTSTileMatrix]:
        if matrix_override is not None:
            return None, matrix_override

        caps = self._capabilities
        if caps is None:
            raise ValidationError(
                "WMTS capabilities are required to resolve a TileMatrixSet; "
                "call get_capabilities() or apply_capabilities() first."
            )

        tms = caps.tile_matrix_sets.get(tile_matrix_set)
        if tms is None:
            known = ", ".join(sorted(caps.tile_matrix_sets)) or "(none)"
            raise ValidationError(
                f"WMTS TileMatrixSet {tile_matrix_set!r} was not found in "
                f"GetCapabilities. Available: {known}."
            )

        matrix = _lookup_matrix(tms, tile_matrix)
        if matrix is None:
            known = ", ".join(sorted(tms.matrices)) or "(none)"
            raise ValidationError(
                f"WMTS TileMatrix {tile_matrix!r} was not found in TileMatrixSet "
                f"{tile_matrix_set!r}. Available: {known}."
            )
        return tms, matrix

    def _matrix_override_crs(self, options: object) -> CRS:
        if isinstance(options, dict):
            override_crs = options.get("matrix_crs")
            if isinstance(override_crs, CRS):
                return override_crs
            if override_crs is not None:
                return CRS.parse(override_crs)
        return self.native_crs

    def _layer_limits(
        self, tile_matrix_set: str, tile_matrix: str
    ) -> WMTSTileMatrixLimits | None:
        caps = self._capabilities
        if caps is None:
            return None
        layer = caps.layers.get(self.layer)
        if layer is None:
            return None
        by_matrix = layer.matrix_limits.get(tile_matrix_set)
        if not by_matrix:
            return None
        return by_matrix.get(tile_matrix)

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
                    tile_matrix_id=str(zoom),
                )

    def _plan_matrix_tiles(
        self,
        bbox: BoundingBox,
        matrix: WMTSTileMatrix,
        matrix_crs: CRS,
        *,
        limits: WMTSTileMatrixLimits | None = None,
    ) -> Iterable[TileGeometry]:
        try:
            working_bbox = bbox if bbox.crs == matrix_crs else bbox.to_crs(matrix_crs)
        except Exception as exc:
            raise ValidationError(
                f"Cannot transform request bbox from {bbox.crs.value} to WMTS "
                f"matrix CRS {matrix_crs.value}: {exc}. Pass a bbox already in "
                f"{matrix_crs.value}, or choose a TileMatrixSet whose SupportedCRS "
                "is compatible."
            ) from exc

        pixel_size = _pixel_size_in_crs_units(matrix.scale_denominator, matrix_crs)
        span_x = abs(pixel_size * matrix.tile_width)
        span_y = abs(pixel_size * matrix.tile_height)
        if span_x <= 0 or span_y <= 0:
            raise ValidationError(
                f"WMTS TileMatrix {matrix.identifier!r} produced a non-positive "
                "tile span from ScaleDenominator; check the capabilities document."
            )

        min_col = _matrix_index(
            working_bbox.min_x,
            matrix.top_left_x,
            span_x,
            matrix.matrix_width,
            reverse=False,
        )
        max_col = _matrix_index(
            working_bbox.max_x,
            matrix.top_left_x,
            span_x,
            matrix.matrix_width,
            reverse=False,
        )
        # TileRow 0 starts at TopLeftCorner; rows increase away from the top
        # (decreasing Y in always_xy / north-up coordinates).
        min_row = _matrix_index(
            working_bbox.max_y,
            matrix.top_left_y,
            span_y,
            matrix.matrix_height,
            reverse=True,
        )
        max_row = _matrix_index(
            working_bbox.min_y,
            matrix.top_left_y,
            span_y,
            matrix.matrix_height,
            reverse=True,
        )

        if limits is not None:
            min_col = max(min_col, limits.min_tile_col)
            max_col = min(max_col, limits.max_tile_col)
            min_row = max(min_row, limits.min_tile_row)
            max_row = min(max_row, limits.max_tile_row)

        if min_col > max_col or min_row > max_row:
            raise ValidationError(
                f"Request bbox does not intersect WMTS TileMatrix "
                f"{matrix.identifier!r} in {matrix_crs.value}"
                + (
                    " within the layer TileMatrixSetLimits"
                    if limits is not None
                    else ""
                )
                + "."
            )

        rows = max_row - min_row + 1
        cols = max_col - min_col + 1
        self.inferred_grid_shape = (rows, cols)

        mosaic_min_x = matrix.top_left_x + min_col * span_x
        mosaic_max_x = matrix.top_left_x + (max_col + 1) * span_x
        mosaic_max_y = matrix.top_left_y - min_row * span_y
        mosaic_min_y = matrix.top_left_y - (max_row + 1) * span_y
        self.inferred_mosaic_bbox = BoundingBox(
            min_x=mosaic_min_x,
            min_y=mosaic_min_y,
            max_x=mosaic_max_x,
            max_y=mosaic_max_y,
            crs=matrix_crs,
        )

        zoom: int | None = (
            int(matrix.identifier) if matrix.identifier.isdigit() else None
        )

        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                max_y = matrix.top_left_y - row * span_y
                min_y = max_y - span_y
                min_x = matrix.top_left_x + col * span_x
                max_x = min_x + span_x

                yield TileGeometry(
                    bbox=BoundingBox(
                        min_x=min_x,
                        min_y=min_y,
                        max_x=max_x,
                        max_y=max_y,
                        crs=matrix_crs,
                    ),
                    width=matrix.tile_width,
                    height=matrix.tile_height,
                    crs=matrix_crs,
                    tile_x=col,
                    tile_y=row,
                    zoom=zoom,
                    tile_matrix_id=matrix.identifier,
                )

    def _coerce_format(self, fmt: Any) -> Format:
        if isinstance(fmt, Format):
            return fmt
        if isinstance(fmt, str):
            try:
                return Format(fmt)
            except ValueError as exc:
                raise ValidationError(unsupported_format_message("WMTS", fmt)) from exc
        raise ValidationError(f"Invalid WMTS format value: {fmt!r}")

    def _coerce_crs(self, crs: Any) -> CRS:
        return CRS.parse(crs)


def _parse_supported_crs(crs_text: str, *, tms_id: str) -> CRS:
    try:
        return CRS.parse(crs_text)
    except ValidationError as exc:
        upper = crs_text.upper()
        # Common URN spellings pyproj may not accept verbatim.
        if "CRS84" in upper or upper.endswith(":84"):
            return CRS.parse("OGC:CRS84")
        if upper.endswith("4326") or ":4326" in upper:
            return CRS.EPSG_4326
        if "3857" in upper:
            return CRS.EPSG_3857
        raise ValidationError(
            f"WMTS TileMatrixSet {tms_id!r} has unsupported SupportedCRS "
            f"{crs_text!r}. Provide a pyproj-recognised CRS (for example "
            "EPSG:25833 or OGC:CRS84)."
        ) from exc


def _top_left_xy(first: float, second: float, crs: CRS) -> tuple[float, float]:
    """Map TopLeftCorner axis order to always_xy ``(x, y)``."""

    if crs.north_axis_first:
        # EPSG:4326-style (lat, lon) → (lon, lat)
        return second, first
    return first, second


def _parse_matrix_limits(
    link_elem: ET.Element, namespaces: dict[str, str]
) -> dict[str, WMTSTileMatrixLimits]:
    limits: dict[str, WMTSTileMatrixLimits] = {}
    for limit_elem in link_elem.findall(
        "wmts:TileMatrixSetLimits/wmts:TileMatrixLimits", namespaces
    ):
        matrix_id = _child_text(limit_elem, "wmts:TileMatrix", namespaces)
        if not matrix_id:
            continue
        try:
            min_row = int(_child_text(limit_elem, "wmts:MinTileRow", namespaces) or "")
            max_row = int(_child_text(limit_elem, "wmts:MaxTileRow", namespaces) or "")
            min_col = int(_child_text(limit_elem, "wmts:MinTileCol", namespaces) or "")
            max_col = int(_child_text(limit_elem, "wmts:MaxTileCol", namespaces) or "")
        except ValueError:
            continue
        limits[matrix_id] = WMTSTileMatrixLimits(
            tile_matrix=matrix_id,
            min_tile_row=min_row,
            max_tile_row=max_row,
            min_tile_col=min_col,
            max_tile_col=max_col,
        )
    return limits


def _child_text(
    element: ET.Element, xpath: str, namespaces: dict[str, str]
) -> str | None:
    child = element.find(xpath, namespaces)
    if child is not None and child.text:
        return child.text.strip()
    return None


def _lookup_matrix(tms: WMTSTileMatrixSet, tile_matrix: str) -> WMTSTileMatrix | None:
    if tile_matrix in tms.matrices:
        return tms.matrices[tile_matrix]
    # Common identifier prefixes used by some hosts.
    for prefix in (tms.identifier, "EPSG4326", "GoogleMapsCompatible", "EPSG:3857"):
        candidate = f"{prefix}:{tile_matrix}"
        if candidate in tms.matrices:
            return tms.matrices[candidate]
    # Allow matching a trailing ":N" / numeric suffix when the caller passes an int,
    # and zero-padded ids such as Kartverket "08" when the caller passes 8 / "8".
    if tile_matrix.isdigit():
        want = int(tile_matrix)
        for key, matrix in tms.matrices.items():
            if key == tile_matrix or key.endswith(f":{tile_matrix}"):
                return matrix
            if key.isdigit() and int(key) == want:
                return matrix
    return None


def _pixel_size_in_crs_units(scale_denominator: float, crs: CRS) -> float:
    """Convert OGC ScaleDenominator to CRS coordinate units per pixel."""

    meters_per_unit = _meters_per_crs_unit(crs)
    return scale_denominator * _OGC_PIXEL_SIZE_METRES / meters_per_unit


def _meters_per_crs_unit(crs: CRS) -> float:
    pj = crs.to_pyproj()
    if pj.is_geographic:
        ellipsoid = pj.ellipsoid
        if ellipsoid is None or ellipsoid.semi_major_metre is None:
            raise ValidationError(
                f"Cannot derive metres-per-degree for geographic CRS {crs.value}; "
                "ellipsoid semi-major axis is unavailable."
            )
        return float(ellipsoid.semi_major_metre) * math.pi / 180.0

    axes = pj.axis_info
    if not axes:
        raise ValidationError(
            f"Cannot derive metres-per-unit for CRS {crs.value}: no axis metadata."
        )
    factor = axes[0].unit_conversion_factor
    if factor is None or factor <= 0:
        raise ValidationError(
            f"Cannot derive metres-per-unit for CRS {crs.value}: "
            f"unsupported axis unit {axes[0].unit_name!r}."
        )
    return float(factor)


def _allows_slippy_fallback(tile_matrix_set: str, crs: CRS) -> bool:
    """True when a common slippy TMS name can be planned without capabilities."""

    if crs not in {CRS.EPSG_4326, CRS.EPSG_3857} and not crs.is_geographic:
        return False
    lowered = tile_matrix_set.lower().replace(":", "").replace("_", "")
    return any(hint in lowered for hint in _SLIPPY_TMS_HINTS)


def _coerce_tile_matrix_id(value: object) -> str:
    if isinstance(value, bool):
        raise ValidationError(f"Invalid WMTS tile_matrix value: {value!r}")
    if isinstance(value, int):
        if value < 0:
            raise ValidationError("tile_matrix must be non-negative")
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValidationError("tile_matrix must be a non-empty identifier")
        return stripped
    raise ValidationError(f"Invalid WMTS tile_matrix value: {value!r}")


def _require_index(value: int | None, override: object) -> int:
    resolved = override if override is not None else value
    if resolved is None:
        raise ValidationError("WMTS tile indices are required")
    if isinstance(resolved, bool) or not isinstance(resolved, (int, str)):
        raise ValidationError(f"Invalid WMTS tile index: {resolved!r}")
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
    raise ValidationError(f"Unsupported format: {fmt}")


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
