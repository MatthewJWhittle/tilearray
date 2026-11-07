"""Capabilities fetch and protocol adapters for inspection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from ..error_messages import (
    format_http_failure,
    looks_like_html,
    looks_like_ogc_exception_report,
    redact_url,
    undetectable_service_type_message,
)
from ..errors import ConfigurationError, NetworkError, ValidationError
from ..matrix_resolution import MatrixLevelSummary
from ..service.base import detect_service_type
from ..service.http import (
    compose_http_headers,
    compose_http_params,
    http_get,
    resolve_timeout,
)
from ..service.wms import WMSCapabilities
from ..service.wmts import (
    WMTSCapabilities,
    WMTSTileMatrixSet,
    pixel_size_from_scale_denominator,
)
from ..types import CRS, Format, ServiceCapabilities, ServiceTypeEnum
from ._models import EndpointInspection, MatrixSetSummary, ResourceSummary
from ._resolve import _XYZ_PLACEHOLDERS, _formats_from_template

# Opaque OGC URLs (no wcs/wms/wmts path token) — probe GetCapabilities in this order.
_OGC_PROBE_ORDER: tuple[ServiceTypeEnum, ...] = (
    ServiceTypeEnum.WMTS,
    ServiceTypeEnum.WMS,
    ServiceTypeEnum.WCS,
)


def _matrix_set_summary(tms: WMTSTileMatrixSet) -> MatrixSetSummary:
    crs_value = (
        tms.supported_crs.value
        if isinstance(tms.supported_crs, CRS)
        else str(tms.supported_crs)
    )
    levels: list[MatrixLevelSummary] = []
    for matrix in tms.matrices.values():
        resolution: float | None
        try:
            resolution = pixel_size_from_scale_denominator(
                matrix.scale_denominator, tms.supported_crs
            )
        except ValidationError:
            resolution = None
        levels.append(
            MatrixLevelSummary(
                identifier=matrix.identifier,
                scale_denominator=matrix.scale_denominator,
                resolution=resolution,
            )
        )
    return MatrixSetSummary(
        identifier=tms.identifier,
        crs=crs_value,
        matrices=tuple(tms.matrices.keys()),
        levels=tuple(levels),
    )


def _coerce_service_type(
    service_type: ServiceTypeEnum | str | None, url: str
) -> ServiceTypeEnum:
    if service_type is None:
        return detect_service_type(url)
    if isinstance(service_type, ServiceTypeEnum):
        return service_type
    try:
        return ServiceTypeEnum(str(service_type).upper())
    except ValueError as exc:
        raise ValidationError(
            f"Unknown service_type={service_type!r}; expected one of "
            f"{[item.value for item in ServiceTypeEnum]}."
        ) from exc


def _sniff_capabilities_service_type(body: str) -> ServiceTypeEnum | None:
    """Infer OGC protocol from a GetCapabilities XML body (no network)."""

    if not body or looks_like_html(body) or looks_like_ogc_exception_report(body):
        return None
    sample = body[:8000].lower()
    # WMTS: TileMatrixSet / GetTile are distinctive.
    if "tilematrixset" in sample or ("wmts" in sample and "gettile" in sample):
        return ServiceTypeEnum.WMTS
    if "coveragesummary" in sample or "describecoverage" in sample:
        return ServiceTypeEnum.WCS
    if "wcs:capabilities" in sample or "xmlns:wcs=" in sample:
        return ServiceTypeEnum.WCS
    if "getmap" in sample or "<wms:" in sample or "ogc:wms" in sample:
        return ServiceTypeEnum.WMS
    if "<capability" in sample and "request" in sample:
        return ServiceTypeEnum.WMS
    return None


def _probe_service_type_via_capabilities(
    url: str,
    *,
    config: Mapping[str, Any],
    client: httpx.Client | None,
) -> tuple[ServiceTypeEnum, str]:
    """Probe GetCapabilities when the URL path does not encode the protocol.

    Tries WMTS → WMS → WCS with ``service=`` / ``request=GetCapabilities`` and
    sniffs the XML. Returns ``(service_type, body)`` so the caller can reuse the
    successful response. Used by :func:`inspect_endpoint` for hosts like
    Kartverket (``/v1/service`` with no ``wmts`` token). Callers may still pass
    ``service_type=`` to skip the probe.
    """

    failures: list[str] = []
    for candidate in _OGC_PROBE_ORDER:
        try:
            body = _fetch_capabilities(
                url,
                service_type=candidate,
                config=config,
                client=client,
            )
        except NetworkError as exc:
            failures.append(f"{candidate.value}: {exc}")
            continue
        sniffed = _sniff_capabilities_service_type(body)
        if sniffed is not None:
            return sniffed, body
        failures.append(f"{candidate.value}: response was not recognisable OGC XML")

    detail = "; ".join(failures) if failures else "no probe attempts succeeded"
    raise ConfigurationError(
        undetectable_service_type_message(url)
        + f"\n  GetCapabilities probe also failed ({detail})."
        + " Pass service_type='wmts' (or wms/wcs) to skip probing."
    )


def _fetch_capabilities(
    url: str,
    *,
    service_type: ServiceTypeEnum,
    config: Mapping[str, Any],
    client: httpx.Client | None,
) -> str:
    version = {
        ServiceTypeEnum.WCS: "2.0.1",
        ServiceTypeEnum.WMS: "1.3.0",
        ServiceTypeEnum.WMTS: "1.0.0",
    }[service_type]
    try:
        response = http_get(
            url,
            params=compose_http_params(
                {
                    "service": service_type.value,
                    "version": version,
                    "request": "GetCapabilities",
                },
                config,
            ),
            headers=compose_http_headers(config),
            timeout=resolve_timeout(config),
            client=client,
        )
    except httpx.HTTPError as exc:
        raise NetworkError(
            format_http_failure(
                exc,
                request_name="GetCapabilities",
                url=url,
                protocol=service_type.value,
            ),
            cause=exc,
        ) from exc
    return response.text


def _inspect_xyz(
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any],
) -> EndpointInspection:
    missing = [token for token in _XYZ_PLACEHOLDERS if token not in url.lower()]
    # Templates are case-sensitive in practice; check exact common forms.
    lower = url.lower()
    has_placeholders = all(token in lower for token in _XYZ_PLACEHOLDERS)
    notes = [
        "XYZ / slippy-map templates have no OGC GetCapabilities document.",
        "Pass zoom= to build_config; confirm {z}/{x}/{y} order against the host "
        "(some hosts use {z}/{y}/{x}).",
    ]
    warnings: list[str] = []
    if not has_placeholders:
        warnings.append(
            "URL does not contain {z}, {x}, and {y} placeholders — "
            f"missing {missing or 'expected tokens'}."
        )
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValidationError(
            f"XYZ template must be an absolute URL: {redact_url(url)}"
        )
    return EndpointInspection(
        service_type=ServiceTypeEnum.XYZ,
        base_url=url,
        title=None,
        notes=tuple(notes),
        warnings=tuple(warnings),
        headers=headers,
        params=params,
    )


def _from_wcs_capabilities(
    caps: ServiceCapabilities,
    *,
    base_url: str,
    headers: dict[str, str],
    params: dict[str, Any],
) -> EndpointInspection:
    resources = tuple(
        ResourceSummary(
            identifier=coverage.identifier,
            title=coverage.title,
            formats=tuple(
                fmt.value if isinstance(fmt, Format) else str(fmt)
                for fmt in coverage.supported_formats
            ),
            crs=tuple(
                item.value if isinstance(item, CRS) else str(item)
                for item in coverage.supported_crs
            ),
        )
        for coverage in caps.coverages
    )
    formats = tuple(
        fmt.value if isinstance(fmt, Format) else str(fmt)
        for fmt in caps.supported_formats
    )
    crs = tuple(
        item.value if isinstance(item, CRS) else str(item)
        for item in caps.supported_crs
    )
    notes = [
        "Inspection lists CoverageIds from GetCapabilities only; "
        "create_array still runs DescribeCoverage when building the service.",
        "tilearray does not reproject pixels into your bbox CRS — pick a "
        "supported request CRS (or the coverage native CRS when known).",
    ]
    warnings: list[str] = []
    if not resources:
        warnings.append(
            "No CoverageSummary entries found — check the URL or auth; "
            "you cannot build a WCSConfig without a CoverageId."
        )
    return EndpointInspection(
        service_type=ServiceTypeEnum.WCS,
        base_url=base_url,
        title=caps.service_title,
        version=caps.version,
        resources=resources,
        formats=formats,
        crs=crs,
        notes=tuple(notes),
        warnings=tuple(warnings),
        headers=headers,
        params=params,
    )


def _from_wmts_capabilities(
    caps: WMTSCapabilities,
    *,
    base_url: str,
    headers: dict[str, str],
    params: dict[str, Any],
) -> EndpointInspection:
    resources = tuple(
        ResourceSummary(
            identifier=layer.identifier,
            tile_matrix_sets=tuple(layer.tile_matrix_set_links),
            resource_url_template=layer.resource_url_template,
            formats=_formats_from_template(layer.resource_url_template),
        )
        for layer in caps.layers.values()
    )
    matrix_sets = tuple(
        _matrix_set_summary(tms) for tms in caps.tile_matrix_sets.values()
    )
    notes = [
        "Choose layer + tile_matrix_set unless only one option exists. Pick a "
        "TileMatrix with tile_matrix= or target_resolution= (CRS units/pixel).",
        "Returned mosaics stay on the matrix SupportedCRS — inspection does not "
        "warp tiles into another CRS.",
        "REST GetTile templates from GetCapabilities are attached by build_config "
        "when present; you do not need to paste url_template= on the happy path.",
    ]
    warnings: list[str] = []
    if not resources:
        warnings.append("No WMTS layers found in GetCapabilities.")
    return EndpointInspection(
        service_type=ServiceTypeEnum.WMTS,
        base_url=base_url,
        resources=resources,
        matrix_sets=matrix_sets,
        formats=tuple(
            sorted({fmt for resource in resources for fmt in resource.formats})
        ),
        crs=tuple(sorted({item.crs for item in matrix_sets if item.crs})),
        notes=tuple(notes),
        warnings=tuple(warnings),
        headers=headers,
        params=params,
    )


def _from_wms_capabilities(
    caps: WMSCapabilities,
    *,
    base_url: str,
    headers: dict[str, str],
    params: dict[str, Any],
) -> EndpointInspection:
    resources = tuple(
        ResourceSummary(
            identifier=layer.name,
            title=layer.title,
            crs=tuple(layer.crs),
            styles=tuple(layer.styles),
            formats=tuple(caps.formats),
        )
        for layer in caps.layers
    )
    notes = [
        "WMS is a thin GetMap adapter — confirm layer names (often year-suffixed) "
        "before hard-coding.",
        "Inspection does not imply client-side reprojection; pass a CRS the host "
        "advertises for GetMap.",
    ]
    warnings: list[str] = []
    if not resources:
        warnings.append("No named WMS layers found in GetCapabilities.")
    return EndpointInspection(
        service_type=ServiceTypeEnum.WMS,
        base_url=base_url,
        title=caps.title,
        version=caps.version,
        resources=resources,
        formats=tuple(caps.formats),
        crs=tuple(sorted({crs for resource in resources for crs in resource.crs})),
        notes=tuple(notes),
        warnings=tuple(warnings),
        headers=headers,
        params=params,
    )
