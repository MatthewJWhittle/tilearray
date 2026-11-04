"""Inspect unknown OGC endpoints and build validated service configs (#96).

Discover → choose → ``create_array``. Capability summaries are structured and
human-readable; ``build_config`` validates identifiers against what the host
advertised and never silently guesses a layer, coverage, grid, CRS, or format.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from .error_messages import (
    format_http_failure,
    looks_like_html,
    redact_url,
)
from .errors import (
    ConfigurationError,
    NetworkError,
    ParseError,
    ValidationError,
)
from .service.base import detect_service_type
from .service.config import (
    ServiceConfig,
    WCSConfig,
    WMSConfig,
    WMTSConfig,
    XYZConfig,
)
from .service.http import (
    compose_http_headers,
    compose_http_params,
    http_get,
    resolve_timeout,
)
from .service.wcs import WCSParser
from .service.wms import WMSCapabilities, WMSParser
from .service.wmts import WMTSCapabilities, WMTSParser
from .types import CRS, Format, ServiceCapabilities, ServiceTypeEnum

_SECRET_HEADER_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "x-auth-token",
    }
)

_XYZ_PLACEHOLDERS = ("{z}", "{x}", "{y}")


@dataclass(frozen=True)
class ResourceSummary:
    """One layer or coverage advertised by a service."""

    identifier: str
    title: str | None = None
    formats: tuple[str, ...] = ()
    crs: tuple[str, ...] = ()
    tile_matrix_sets: tuple[str, ...] = ()
    styles: tuple[str, ...] = ()
    resource_url_template: str | None = None


@dataclass(frozen=True)
class MatrixSetSummary:
    """WMTS TileMatrixSet summary for inspection."""

    identifier: str
    crs: str | None = None
    matrices: tuple[str, ...] = ()


@dataclass
class EndpointInspection:
    """Structured capability summary plus a validated config builder."""

    service_type: ServiceTypeEnum
    base_url: str
    title: str | None = None
    version: str | None = None
    resources: tuple[ResourceSummary, ...] = ()
    formats: tuple[str, ...] = ()
    crs: tuple[str, ...] = ()
    matrix_sets: tuple[MatrixSetSummary, ...] = ()
    notes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    params: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def layers(self) -> tuple[ResourceSummary, ...]:
        """Alias for :attr:`resources` (WMS / WMTS wording)."""

        return self.resources

    @property
    def coverages(self) -> tuple[ResourceSummary, ...]:
        """Alias for :attr:`resources` (WCS wording)."""

        return self.resources

    def summary(self) -> str:
        """Human-readable capability summary with secrets redacted."""

        safe_url = redact_url(self.base_url)
        lines = [
            f"{self.service_type.value} endpoint: {safe_url}",
        ]
        if self.title:
            lines.append(f"Title: {self.title}")
        if self.version:
            lines.append(f"Version: {self.version}")

        if self.service_type is ServiceTypeEnum.XYZ:
            lines.append("Discovery: XYZ URL templates have no GetCapabilities.")
        elif self.service_type is ServiceTypeEnum.WCS:
            lines.append(f"Coverages ({len(self.resources)}):")
            lines.extend(_format_resource_lines(self.resources, label="coverage"))
        else:
            lines.append(f"Layers ({len(self.resources)}):")
            lines.extend(_format_resource_lines(self.resources, label="layer"))

        if self.formats:
            lines.append(f"Formats: {', '.join(self.formats)}")
        if self.crs:
            lines.append(f"CRS: {', '.join(self.crs)}")
        if self.matrix_sets:
            lines.append(f"Tile matrix sets ({len(self.matrix_sets)}):")
            for matrix_set in self.matrix_sets:
                crs_bit = f", CRS {matrix_set.crs}" if matrix_set.crs else ""
                matrices = (
                    ", ".join(matrix_set.matrices)
                    if matrix_set.matrices
                    else "(none listed)"
                )
                lines.append(
                    f"  - {matrix_set.identifier}{crs_bit}; matrices: {matrices}"
                )

        if self.notes:
            lines.append("Notes:")
            lines.extend(f"  - {note}" for note in self.notes)
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)

        if self.headers or self.params:
            lines.append(
                "Auth: headers=/params= were supplied for capability requests "
                f"(redacted for display: {_redact_mapping(self.headers)}; "
                f"params={_redact_mapping(self.params)})."
            )
        return "\n".join(lines)

    def __str__(self) -> str:
        return self.summary()

    def build_config(self, **choices: Any) -> ServiceConfig:
        """Validate *choices* against this inspection and return a service config.

        Auto-fills a field only when the inspection lists exactly one unambiguous
        value. Never silently picks among multiple layers, coverages, matrices,
        CRS values, or formats.
        """

        if self.service_type is ServiceTypeEnum.WCS:
            return self._build_wcs_config(**choices)
        if self.service_type is ServiceTypeEnum.WMS:
            return self._build_wms_config(**choices)
        if self.service_type is ServiceTypeEnum.WMTS:
            return self._build_wmts_config(**choices)
        if self.service_type is ServiceTypeEnum.XYZ:
            return self._build_xyz_config(**choices)
        raise ConfigurationError(
            f"Unsupported service type for build_config: {self.service_type!r}"
        )

    def example_snippet(self, **choices: Any) -> str:
        """Return a copy-paste snippet for ``create_array`` with secrets redacted."""

        config = self.build_config(**choices)
        safe_url = redact_url(self.base_url)
        protocol = self.service_type.value
        lines = [
            "from tilearray import (",
            "    compute_with_policy,",
            "    create_array,",
            f"    {type(config).__name__},",
            ")",
            "from tilearray.types import CRS",
            "",
        ]
        if isinstance(config, WCSConfig):
            lines.extend(
                [
                    "config = WCSConfig.from_url(",
                    f"    {safe_url!r},",
                    f"    {config.coverage_id!r},",
                    *_optional_config_kwargs(config),
                    ")",
                ]
            )
        elif isinstance(config, WMSConfig):
            lines.extend(
                [
                    "config = WMSConfig.from_url(",
                    f"    {safe_url!r},",
                    f"    {config.layers!r},",
                    *_optional_config_kwargs(config),
                    ")",
                ]
            )
        elif isinstance(config, WMTSConfig):
            lines.extend(
                [
                    "config = WMTSConfig.from_url(",
                    f"    {safe_url!r},",
                    f"    layer={config.layer!r},",
                    f"    tile_matrix_set={config.tile_matrix_set!r},",
                    f"    tile_matrix={config.tile_matrix!r},",
                    *_optional_config_kwargs(config),
                    ")",
                ]
            )
        else:
            assert isinstance(config, XYZConfig)
            lines.extend(
                [
                    "config = XYZConfig.from_url(",
                    f"    {safe_url!r},",
                    f"    zoom={config.zoom},",
                    *_optional_config_kwargs(config),
                    ")",
                ]
            )
        lines.extend(
            [
                "",
                f"# {protocol}: set bbox/crs to the AOI you need, then:",
                "da = create_array(config, bbox=(...), crs=CRS.EPSG_4326)",
                "arr = compute_with_policy(da)",
            ]
        )
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Config builders
    # ------------------------------------------------------------------
    def _build_wcs_config(self, **choices: Any) -> WCSConfig:
        coverage_id = choices.pop("coverage_id", None)
        if coverage_id is None:
            coverage_id = _unique_identifier(self.resources, field_name="coverage_id")
        coverage_id = str(coverage_id)
        _require_known(
            coverage_id,
            {item.identifier for item in self.resources},
            field_name="coverage_id",
            kind="coverage",
        )
        output_format = _resolve_format(
            choices.pop("output_format", None),
            advertised=self.formats,
            default_when_unique=True,
        )
        crs = _resolve_crs(
            choices.pop("crs", None),
            advertised=self.crs,
            default_when_unique=True,
        )
        kwargs = _merge_auth_kwargs(self, choices)
        if output_format is not None:
            kwargs["output_format"] = output_format
        if crs is not None:
            kwargs["crs"] = crs
        return WCSConfig.from_url(self.base_url, coverage_id, **kwargs)

    def _build_wms_config(self, **choices: Any) -> WMSConfig:
        layers = choices.pop("layers", None)
        if layers is None:
            layers = _unique_identifier(self.resources, field_name="layers")
        layers = str(layers)
        for part in (piece.strip() for piece in layers.split(",")):
            if not part:
                continue
            _require_known(
                part,
                {item.identifier for item in self.resources},
                field_name="layers",
                kind="layer",
            )
        resource = next(
            (
                item
                for item in self.resources
                if item.identifier == layers.split(",")[0].strip()
            ),
            None,
        )
        advertised_crs = resource.crs if resource and resource.crs else self.crs
        advertised_formats = (
            resource.formats if resource and resource.formats else self.formats
        )
        output_format = _resolve_format(
            choices.pop("output_format", None),
            advertised=advertised_formats,
            default_when_unique=True,
        )
        crs = _resolve_crs(
            choices.pop("crs", None),
            advertised=advertised_crs,
            default_when_unique=True,
        )
        kwargs = _merge_auth_kwargs(self, choices)
        if output_format is not None:
            kwargs["output_format"] = output_format
        if crs is not None:
            kwargs["crs"] = crs
        return WMSConfig.from_url(self.base_url, layers, **kwargs)

    def _build_wmts_config(self, **choices: Any) -> WMTSConfig:
        layer = choices.pop("layer", None)
        if layer is None:
            layer = _unique_identifier(self.resources, field_name="layer")
        layer = str(layer)
        _require_known(
            layer,
            {item.identifier for item in self.resources},
            field_name="layer",
            kind="layer",
        )
        resource = next(item for item in self.resources if item.identifier == layer)

        tile_matrix_set = choices.pop("tile_matrix_set", None)
        known_sets = set(resource.tile_matrix_sets) or {
            item.identifier for item in self.matrix_sets
        }
        if tile_matrix_set is None:
            if len(known_sets) == 1:
                tile_matrix_set = next(iter(known_sets))
            elif not known_sets:
                raise ValidationError(
                    "WMTS tile_matrix_set is required, but GetCapabilities listed "
                    "none for this layer. Pass tile_matrix_set= explicitly."
                )
            else:
                raise ValidationError(
                    "WMTS tile_matrix_set is required when multiple tile matrix "
                    f"sets are advertised: {sorted(known_sets)}. Pass "
                    "tile_matrix_set= explicitly; tilearray will not guess."
                )
        tile_matrix_set = str(tile_matrix_set)
        if known_sets:
            _require_known(
                tile_matrix_set,
                known_sets,
                field_name="tile_matrix_set",
                kind="tile matrix set",
            )

        tile_matrix = choices.pop("tile_matrix", None)
        matrix_ids: tuple[str, ...] = ()
        for matrix_set in self.matrix_sets:
            if matrix_set.identifier == tile_matrix_set:
                matrix_ids = matrix_set.matrices
                break
        if tile_matrix is None:
            if len(matrix_ids) == 1:
                tile_matrix = matrix_ids[0]
            else:
                known = list(matrix_ids) if matrix_ids else "(unknown)"
                raise ValidationError(
                    "WMTS tile_matrix is required when multiple matrices are "
                    f"advertised for {tile_matrix_set!r}: {known}. "
                    "Pass tile_matrix= explicitly; tilearray will not guess."
                )
        tile_matrix_int = _coerce_tile_matrix(tile_matrix, matrix_ids)

        output_format = _resolve_format(
            choices.pop("output_format", None),
            advertised=resource.formats or self.formats,
            default_when_unique=True,
        )
        matrix_crs = next(
            (
                item.crs
                for item in self.matrix_sets
                if item.identifier == tile_matrix_set
            ),
            None,
        )
        crs = _resolve_crs(
            choices.pop("crs", None),
            advertised=tuple(
                value
                for value in (matrix_crs, *resource.crs, *self.crs)
                if value is not None
            ),
            default_when_unique=True,
        )
        kwargs = _merge_auth_kwargs(self, choices)
        if resource.resource_url_template and "url_template" not in kwargs:
            kwargs["url_template"] = resource.resource_url_template
        if output_format is not None:
            kwargs["output_format"] = output_format
        if crs is not None:
            kwargs["crs"] = crs
        return WMTSConfig.from_url(
            self.base_url,
            layer=layer,
            tile_matrix_set=tile_matrix_set,
            tile_matrix=tile_matrix_int,
            **kwargs,
        )

    def _build_xyz_config(self, **choices: Any) -> XYZConfig:
        zoom = choices.pop("zoom", None)
        if zoom is None:
            raise ValidationError(
                "XYZ configs require zoom= (no GetCapabilities to discover levels)."
            )
        kwargs = _merge_auth_kwargs(self, choices)
        return XYZConfig.from_url(self.base_url, zoom=int(zoom), **kwargs)


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
    """Parse a GetCapabilities body (or reject HTML / malformed XML) offline."""

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


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------
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
        MatrixSetSummary(
            identifier=tms.identifier,
            crs=tms.supported_crs.value
            if isinstance(tms.supported_crs, CRS)
            else str(tms.supported_crs),
            matrices=tuple(tms.matrices.keys()),
        )
        for tms in caps.tile_matrix_sets.values()
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


def _formats_from_template(template: str | None) -> tuple[str, ...]:
    if not template:
        return ()
    lower = template.lower()
    if lower.endswith(".png") or "image/png" in lower:
        return ("image/png",)
    if lower.endswith((".jpg", ".jpeg")) or "image/jpeg" in lower:
        return ("image/jpeg",)
    if lower.endswith((".tif", ".tiff")) or "image/tiff" in lower:
        return ("image/tiff",)
    return ()


def _format_resource_lines(
    resources: Sequence[ResourceSummary], *, label: str
) -> list[str]:
    if not resources:
        return [f"  (none — cannot build a config without a {label} id)"]
    lines: list[str] = []
    for item in resources:
        title_bit = f" — {item.title}" if item.title else ""
        detail_parts: list[str] = []
        if item.tile_matrix_sets:
            detail_parts.append("TMS: " + ", ".join(item.tile_matrix_sets))
        if item.crs:
            detail_parts.append("CRS: " + ", ".join(item.crs))
        if item.formats:
            detail_parts.append("formats: " + ", ".join(item.formats))
        if detail_parts:
            joined = "; ".join(detail_parts)
            detail = f" ({joined})"
        else:
            detail = ""
        lines.append(f"  - {item.identifier}{title_bit}{detail}")
    return lines


def _unique_identifier(resources: Sequence[ResourceSummary], *, field_name: str) -> str:
    if len(resources) == 1:
        return resources[0].identifier
    if not resources:
        raise ValidationError(
            f"{field_name} is required, but GetCapabilities listed no identifiers."
        )
    ids = [item.identifier for item in resources]
    raise ValidationError(
        f"{field_name} is required when multiple options exist: {ids}. "
        "Pass it explicitly; tilearray will not guess."
    )


def _require_known(value: str, known: set[str], *, field_name: str, kind: str) -> None:
    if value in known:
        return
    preview = sorted(known)
    shown = preview[:12]
    more = f" (+{len(preview) - len(shown)} more)" if len(preview) > len(shown) else ""
    raise ValidationError(
        f"Unknown {kind} {value!r} for {field_name}. "
        f"Known from GetCapabilities: {shown}{more}. "
        "tilearray does not rename or guess identifiers."
    )


def _resolve_format(
    value: Any,
    *,
    advertised: Sequence[str],
    default_when_unique: bool,
) -> Format | None:
    if value is None:
        library_hits = [fmt for fmt in Format if fmt.value in advertised]
        if default_when_unique and len(library_hits) == 1:
            return library_hits[0]
        if default_when_unique and len(advertised) == 1:
            try:
                return Format(advertised[0])
            except ValueError:
                return None
        return None
    if isinstance(value, Format):
        fmt = value
    else:
        try:
            fmt = Format(str(value))
        except ValueError as exc:
            raise ValidationError(
                f"Unsupported output_format={value!r}. "
                f"Library formats: {[item.value for item in Format]}."
            ) from exc
    if advertised and fmt.value not in advertised:
        raise ValidationError(
            f"output_format={fmt.value!r} is not in the advertised formats "
            f"{list(advertised)}. Pass a listed MIME type; tilearray will not "
            "substitute another format."
        )
    return fmt


_CRS_PRESETS: tuple[CRS, ...] = (
    CRS.EPSG_4326,
    CRS.EPSG_3857,
    CRS.EPSG_32633,
    CRS.EPSG_27700,
)


def _resolve_crs(
    value: Any,
    *,
    advertised: Sequence[str],
    default_when_unique: bool,
) -> CRS | None:
    if value is None:
        # Prefer named presets when they match advertised ids, then fall back
        # to any single parseable advertised CRS (CRS accepts any pyproj CRS).
        advertised_upper = {text.upper() for text in advertised}
        library_hits = [
            item
            for item in _CRS_PRESETS
            if item.value in advertised or item.value.upper() in advertised_upper
        ]
        if not library_hits:
            library_hits = [
                item
                for item in _CRS_PRESETS
                if any(item.value.split(":")[-1] in text for text in advertised)
            ]
        unique = list(dict.fromkeys(library_hits))
        if default_when_unique and len(unique) == 1:
            return unique[0]
        if default_when_unique and not unique and advertised:
            parsed: list[CRS] = []
            for text in advertised:
                try:
                    parsed.append(CRS.parse(text))
                except ValidationError:
                    continue
            unique_parsed = list(dict.fromkeys(parsed))
            if len(unique_parsed) == 1:
                return unique_parsed[0]
        return None
    crs = _coerce_crs_value(value)
    if advertised:
        normalized_advertised = {_normalize_crs_token(item) for item in advertised}
        if (
            _normalize_crs_token(crs.value) not in normalized_advertised
            and crs.value not in advertised
        ):
            raise ValidationError(
                f"crs={crs.value!r} is not in the advertised CRS list "
                f"{list(advertised)}. Pass a listed CRS; tilearray will not "
                "silently reproject."
            )
    return crs


def _coerce_crs_value(value: Any) -> CRS:
    try:
        return CRS.parse(value)
    except ValidationError as exc:
        raise ValidationError(
            f"Unsupported crs={value!r}. Expected a pyproj-valid CRS "
            "(authority string such as 'EPSG:4326', integer EPSG code, "
            f"or CRS instance). Presets: {[item.value for item in _CRS_PRESETS]}."
        ) from exc


def _normalize_crs_token(value: str) -> str:
    upper = value.upper().replace("URN:OGC:DEF:CRS:", "")
    if "EPSG" in upper:
        digits = "".join(ch for ch in upper.rsplit("EPSG", 1)[-1] if ch.isdigit())
        if digits:
            return f"EPSG:{digits}"
    if upper.endswith("CRS84") or "CRS:84" in upper:
        return "CRS:84"
    return upper


def _coerce_tile_matrix(value: Any, matrix_ids: Sequence[str]) -> int:
    text = str(value)
    if (
        matrix_ids
        and text not in matrix_ids
        and text not in {str(int(item)) for item in matrix_ids if item.isdigit()}
    ):
        # Allow numeric equivalents when caps use "05" vs 5 sparingly: exact match only.
        raise ValidationError(
            f"Unknown tile_matrix={text!r}. Known matrices: {list(matrix_ids)}. "
            "tilearray will not guess a zoom level."
        )
    try:
        return int(text)
    except ValueError as exc:
        raise ValidationError(
            f"tile_matrix={text!r} is not an integer. Current WMTSConfig requires "
            "an integer tile_matrix id; non-numeric matrix identifiers need a "
            "follow-up beyond this inspector."
        ) from exc


def _merge_auth_kwargs(
    inspection: EndpointInspection, choices: dict[str, Any]
) -> dict[str, Any]:
    kwargs = dict(choices)
    if inspection.headers and "headers" not in kwargs:
        kwargs["headers"] = dict(inspection.headers)
    if inspection.params and "params" not in kwargs:
        kwargs["params"] = dict(inspection.params)
    return kwargs


def _optional_config_kwargs(config: ServiceConfig) -> list[str]:
    lines: list[str] = []
    if config.output_format is not None:
        lines.append(f"    output_format={config.output_format!r},")
    if config.crs is not None:
        lines.append(f"    crs={config.crs!r},")
    if isinstance(config, WMTSConfig) and config.url_template:
        lines.append(f"    url_template={redact_url(config.url_template)!r},")
    if config.headers:
        lines.append(f"    headers={_redact_mapping(config.headers)},")
    if config.params:
        lines.append(f"    params={_redact_mapping(config.params)},")
    return lines


def _redact_mapping(mapping: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in mapping.items():
        key_l = str(key).lower()
        if key_l in _SECRET_HEADER_KEYS or key_l in {
            "key",
            "api_key",
            "apikey",
            "token",
            "access_token",
            "password",
            "secret",
            "client_secret",
            "authorization",
        }:
            redacted[key] = "***"
        elif isinstance(value, str) and ("http://" in value or "https://" in value):
            redacted[key] = redact_url(value)
        else:
            redacted[key] = value
    return redacted
