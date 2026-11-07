"""Inspection result models and validated config builders."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast

from ..error_messages import redact_url
from ..errors import ConfigurationError, ValidationError
from ..matrix_resolution import (
    MatrixLevelSummary,
    ResolutionPolicy,
    choose_matrix_for_resolution,
)
from ..service.config import (
    ServiceConfig,
    WCSConfig,
    WMSConfig,
    WMTSConfig,
    XYZConfig,
)
from ..types import ServiceTypeEnum
from ._resolve import (
    _coerce_tile_matrix,
    _format_resource_lines,
    _merge_auth_kwargs,
    _optional_config_kwargs,
    _redact_mapping,
    _require_known,
    _resolve_crs,
    _resolve_format,
    _unique_identifier,
)


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
    levels: tuple[MatrixLevelSummary, ...] = ()
    """Matrix ids with ScaleDenominator / native resolution when advertised."""


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
                if matrix_set.levels:
                    matrices = ", ".join(
                        (
                            f"{level.identifier}"
                            + (
                                f"@{level.resolution:.6g}"
                                if level.resolution is not None
                                else ""
                            )
                        )
                        for level in matrix_set.levels
                    )
                elif matrix_set.matrices:
                    matrices = ", ".join(matrix_set.matrices)
                else:
                    matrices = "(none listed)"
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
        value. Never silently picks among multiple layers, coverages, matrix
        sets, CRS values, or formats.

        For WMTS, pass either ``tile_matrix=`` or ``target_resolution=`` (CRS
        units per pixel in the matrix CRS, with optional ``resolution_policy``).
        ``target_resolution`` uses :func:`~tilearray.choose_matrix_for_resolution`
        on the selected TileMatrixSet's levels — no ScaleDenominator or URL
        template required from the caller. REST templates from GetCapabilities
        are attached when present.
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
            "    compute_mosaic,",
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
                "da = create_array(config, bbox=(...), bbox_crs=CRS.EPSG_4326)",
                "arr = compute_mosaic(da)",
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
        styles_choice = choices.pop("styles", None)
        layer_parts = [piece.strip() for piece in layers.split(",") if piece.strip()]
        if styles_choice is not None:
            style_parts = [piece.strip() for piece in str(styles_choice).split(",")]
            # WMS GetMap STYLES is positional with LAYERS — arity must match
            # when more than one layer is named (blank slots mean server default).
            if len(layer_parts) > 1 and len(style_parts) != len(layer_parts):
                raise ValidationError(
                    f"WMS styles={styles_choice!r} has {len(style_parts)} "
                    f"entr{'y' if len(style_parts) == 1 else 'ies'} but "
                    f"layers= lists {len(layer_parts)} layers. Pass one style "
                    "slot per layer (comma-separated; empty slot = server "
                    "default), matching GetMap STYLES/LAYERS arity."
                )
            # Validate each non-empty style against the corresponding layer
            # (or the sole layer when a single style is given).
            by_id = {item.identifier: item for item in self.resources}
            for index, layer_id in enumerate(layer_parts):
                if len(style_parts) == 1 and len(layer_parts) == 1:
                    style_name = style_parts[0]
                elif len(style_parts) == len(layer_parts):
                    style_name = style_parts[index]
                else:
                    style_name = style_parts[0] if style_parts else ""
                if not style_name:
                    continue
                layer_res = by_id.get(layer_id)
                advertised = (
                    set(layer_res.styles)
                    if layer_res is not None and layer_res.styles
                    else set()
                )
                if advertised:
                    _require_known(
                        style_name,
                        advertised,
                        field_name="styles",
                        kind="style",
                    )
        kwargs = _merge_auth_kwargs(self, choices)
        if output_format is not None:
            kwargs["output_format"] = output_format
        if crs is not None:
            kwargs["crs"] = crs
        if styles_choice is not None:
            kwargs["styles"] = styles_choice
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
        target_resolution = choices.pop("target_resolution", None)
        resolution_policy = choices.pop("resolution_policy", "nearest")
        matrix_summary = next(
            (item for item in self.matrix_sets if item.identifier == tile_matrix_set),
            None,
        )
        matrix_ids: tuple[str, ...] = (
            matrix_summary.matrices if matrix_summary is not None else ()
        )
        matrix_crs = matrix_summary.crs if matrix_summary is not None else None

        if tile_matrix is not None and target_resolution is not None:
            raise ValidationError(
                "Pass either tile_matrix= or target_resolution=, not both. "
                "target_resolution picks a TileMatrix from the matrix set levels; "
                "tile_matrix= sets the id explicitly."
            )
        if tile_matrix is None and target_resolution is None:
            if len(matrix_ids) == 1:
                tile_matrix = matrix_ids[0]
            else:
                known = list(matrix_ids) if matrix_ids else "(unknown)"
                raise ValidationError(
                    "WMTS tile_matrix is required when multiple matrices are "
                    f"advertised for {tile_matrix_set!r}: {known}. Pass "
                    "tile_matrix= or target_resolution= (CRS units/pixel); "
                    "tilearray will not guess a zoom level."
                )
        elif target_resolution is not None:
            if matrix_summary is None or not matrix_summary.levels:
                raise ValidationError(
                    f"No matrix levels with resolution metadata for "
                    f"tile_matrix_set={tile_matrix_set!r}. Pass tile_matrix= "
                    "explicitly, or inspect a host that advertises "
                    "ScaleDenominator / native resolution."
                )
            if resolution_policy not in {"coarser", "nearest", "finer"}:
                raise ValidationError(
                    "resolution_policy must be 'coarser', 'nearest', or 'finer'."
                )
            choice = choose_matrix_for_resolution(
                matrix_summary.levels,
                float(target_resolution),
                policy=cast(ResolutionPolicy, resolution_policy),
                crs=matrix_crs,
            )
            tile_matrix = choice.identifier

        tile_matrix_int = _coerce_tile_matrix(tile_matrix, matrix_ids)

        output_format = _resolve_format(
            choices.pop("output_format", None),
            advertised=resource.formats or self.formats,
            default_when_unique=True,
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
