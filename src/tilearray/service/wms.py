"""WMS (Web Map Service) tile request functionality."""

from __future__ import annotations

from typing import Any

from ..types import CRS, BoundingBox, Format, ServiceTypeEnum, TileRequest
from .base import BaseService, TileGeometry, register_service


def format_wms_bbox(bbox: BoundingBox, crs: CRS, version: str) -> str:
    """
    Format a bounding box for WMS GetMap.

    WMS 1.3.0 follows CRS axis order (latitude-first for EPSG:4326).
    Earlier versions always use minx,miny,maxx,maxy.
    """

    if version.startswith("1.3"):
        if crs in {CRS.EPSG_4326}:
            return f"{bbox.min_y},{bbox.min_x},{bbox.max_y},{bbox.max_x}"
        return f"{bbox.min_x},{bbox.min_y},{bbox.max_x},{bbox.max_y}"

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
            raise ValueError("WMS layers must be provided")
        self.layers = layers
        self.version = version
        self.styles = styles
        self.output_format = self._coerce_format(
            output_format or config.get("format") or Format.PNG
        )
        self.native_crs = self._coerce_crs(crs or config.get("crs") or CRS.EPSG_4326)

    @classmethod
    def from_url(cls, url: str, **config: Any) -> WMSService:
        layers = config.get("layers")
        if not layers:
            raise ValueError("WMS services require layers")
        return cls(url, **config)

    def build_tile_request(self, tile: TileGeometry, **options: Any) -> TileRequest:
        layers = str(options.get("layers") or self.layers)
        if not layers:
            raise ValueError("WMS layers must be provided")

        version = str(options.get("version") or self.version)
        styles = str(
            options.get("styles") if options.get("styles") is not None else self.styles
        )
        fmt = self._coerce_format(options.get("output_format") or self.output_format)
        crs = self._coerce_crs(options.get("crs") or tile.crs or self.native_crs)

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
            if key not in {"output_format", "crs", "layers", "styles", "version"}
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
        if isinstance(fmt, Format):
            return fmt
        if isinstance(fmt, str):
            try:
                return Format(fmt)
            except ValueError as exc:
                raise ValueError(f"Unsupported WMS format: {fmt}") from exc
        raise ValueError(f"Invalid WMS format value: {fmt!r}")

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
