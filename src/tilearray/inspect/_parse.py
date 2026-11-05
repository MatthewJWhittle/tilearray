"""Capabilities fetch and protocol adapters for inspection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

import httpx

from ..error_messages import format_http_failure, redact_url
from ..errors import NetworkError, ValidationError
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
        "Choose layer + tile_matrix_set + tile_matrix explicitly unless only one "
        "option exists.",
        "Returned mosaics are labelled with the grid CRS you request — inspection "
        "does not warp tiles into another CRS.",
        "Non-Web-Mercator / native-matrix planning depth is still limited on some "
        "hosts; see the WMTS roadmap notes if georef looks wrong.",
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
