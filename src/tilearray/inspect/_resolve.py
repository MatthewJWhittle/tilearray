"""CRS / format / auth / redaction helpers for endpoint inspection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from ..error_messages import redact_url
from ..errors import ValidationError
from ..service.config import ServiceConfig, WMTSConfig
from ..types import CRS, Format, canonicalize_crs_input

if TYPE_CHECKING:
    from ._models import EndpointInspection, ResourceSummary

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
    """Normalise advertised CRS tokens for equality checks in ``build_config``."""

    canonical = canonicalize_crs_input(value)
    # Keep CRS84 comparisons stable against host spellings that displayed as CRS:84.
    if canonical == "OGC:CRS84":
        return "CRS:84"
    return canonical.upper() if canonical.upper().startswith("EPSG:") else canonical


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
