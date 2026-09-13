"""Shared helpers for composing HTTP tile requests from service configuration."""

from __future__ import annotations

from typing import Any

from ..types import CRS, BoundingBox, Format, TileRequest


def merge_str_mappings(*sources: object) -> dict[str, str]:
    """Merge string-keyed mappings from config/options overlays."""

    merged: dict[str, str] = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update({str(key): str(value) for key, value in source.items()})
    return merged


def merge_params(*sources: object) -> dict[str, Any]:
    """Merge query-parameter mappings from config/options overlays."""

    merged: dict[str, Any] = {}
    for source in sources:
        if isinstance(source, dict):
            merged.update(source)
    return merged


def compose_tile_request(
    *,
    config: dict[str, object],
    options: dict[str, object],
    url: str,
    bbox: BoundingBox,
    width: int,
    height: int,
    crs: CRS,
    params: dict[str, Any] | None = None,
    output_format: Format | None = None,
) -> TileRequest:
    """
    Build a :class:`TileRequest` with headers and params wired from config.

    Service implementations supply URL/bbox/format specifics; this helper
    ensures headers (User-Agent, auth hooks, etc.) and params always land
    on the outgoing request.
    """

    headers = merge_str_mappings(config.get("headers"), options.get("headers"))
    merged_params = merge_params(config.get("params"), params, options.get("params"))

    fmt = output_format
    if fmt is None:
        fmt_option = options.get("output_format")
        if isinstance(fmt_option, Format):
            fmt = fmt_option
        elif isinstance(fmt_option, str):
            fmt = Format(fmt_option)

    return TileRequest(
        url=url,
        params=merged_params,
        headers=headers or None,
        output_format=fmt,
        crs=crs,
        bbox=bbox,
        width=width,
        height=height,
    )
