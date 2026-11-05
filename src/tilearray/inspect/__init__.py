"""Inspect unknown OGC endpoints and build validated service configs (#96).

Discover → choose → ``create_array``. Capability summaries are structured and
human-readable; ``build_config`` validates identifiers against what the host
advertised and never silently guesses a layer, coverage, grid, CRS, or format.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from ..error_messages import looks_like_html, redact_url
from ..errors import ConfigurationError, ParseError
from ..matrix_resolution import MatrixLevelSummary
from ..service.wcs import WCSParser
from ..service.wms import WMSParser
from ..service.wmts import WMTSParser
from ..types import ServiceTypeEnum
from ._models import EndpointInspection, MatrixSetSummary, ResourceSummary
from ._parse import (
    _coerce_service_type,
    _fetch_capabilities,
    _from_wcs_capabilities,
    _from_wms_capabilities,
    _from_wmts_capabilities,
    _inspect_xyz,
)
from ._resolve import (
    _coerce_crs_value,
    _coerce_tile_matrix,
    _normalize_crs_token,
    _redact_mapping,
    _resolve_crs,
    _resolve_format,
)

__all__ = [
    "EndpointInspection",
    "MatrixLevelSummary",
    "MatrixSetSummary",
    "ResourceSummary",
    "inspect_capabilities",
    "inspect_endpoint",
]


def inspect_endpoint(
    url: str,
    *,
    service_type: ServiceTypeEnum | str | None = None,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
    timeout: float | None = None,
    client: httpx.Client | None = None,
) -> EndpointInspection:
    """Fetch and summarise an OGC endpoint (or validate an XYZ template).

    Parameters
    ----------
    url:
        Service base URL, or an XYZ template containing ``{z}``, ``{x}``, ``{y}``.
    service_type:
        Optional override when the URL does not encode the protocol.
    headers, params:
        Same auth route as tile fetches / plan-time caps GETs.
    timeout:
        Optional HTTP timeout in seconds (defaults to the shared caps timeout).
    client:
        Optional shared ``httpx.Client`` (tests / callers that own a session).
    """

    resolved_type = _coerce_service_type(service_type, url)
    header_map = dict(headers or {})
    param_map = dict(params or {})
    config_bag: dict[str, Any] = {}
    if header_map:
        config_bag["headers"] = header_map
    if param_map:
        config_bag["params"] = param_map
    if timeout is not None:
        config_bag["fetch_timeout"] = timeout

    if resolved_type is ServiceTypeEnum.XYZ:
        return _inspect_xyz(url, headers=header_map, params=param_map)

    body = _fetch_capabilities(
        url,
        service_type=resolved_type,
        config=config_bag,
        client=client,
    )
    return inspect_capabilities(
        body,
        service_type=resolved_type,
        base_url=url,
        headers=header_map,
        params=param_map,
    )


def inspect_capabilities(
    body: str | bytes,
    *,
    service_type: ServiceTypeEnum | str,
    base_url: str,
    headers: Mapping[str, str] | None = None,
    params: Mapping[str, Any] | None = None,
) -> EndpointInspection:
    """Parse a GetCapabilities body you already have (tests / saved XML).

    Everyday callers should use :func:`inspect_endpoint` with a URL instead.
    Rejects HTML login pages and malformed XML with :exc:`~tilearray.ParseError`.
    """

    resolved_type = (
        service_type
        if isinstance(service_type, ServiceTypeEnum)
        else ServiceTypeEnum(str(service_type).upper())
    )
    text = body.decode("utf-8", errors="replace") if isinstance(body, bytes) else body
    if looks_like_html(text):
        raise ParseError(
            f"{resolved_type.value} GetCapabilities response looks like HTML "
            f"(login/portal page), not OGC XML, for {redact_url(base_url)}. "
            "Check base_url and credentials via params=/headers=."
        )

    header_map = dict(headers or {})
    param_map = dict(params or {})

    if resolved_type is ServiceTypeEnum.WCS:
        return _from_wcs_capabilities(
            WCSParser(base_url).parse_get_capabilities(text),
            base_url=base_url,
            headers=header_map,
            params=param_map,
        )
    if resolved_type is ServiceTypeEnum.WMTS:
        return _from_wmts_capabilities(
            WMTSParser().parse_get_capabilities(text),
            base_url=base_url,
            headers=header_map,
            params=param_map,
        )
    if resolved_type is ServiceTypeEnum.WMS:
        return _from_wms_capabilities(
            WMSParser().parse_get_capabilities(text),
            base_url=base_url,
            headers=header_map,
            params=param_map,
        )
    if resolved_type is ServiceTypeEnum.XYZ:
        return _inspect_xyz(base_url, headers=header_map, params=param_map)
    raise ConfigurationError(
        f"inspect_capabilities does not support service_type={resolved_type!r}"
    )
