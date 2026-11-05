"""Helpers for actionable stage-gate error messages (#88).

Enrich existing ``TileArrayError`` wraps with what was wrong, what was tried,
and what to check next. No new public exception types.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

import httpx

from .errors import ParseError, TileArrayError
from .types import Format

# Query keys that must never appear in exception text.
_SECRET_QUERY_KEYS = frozenset(
    {
        "key",
        "api_key",
        "apikey",
        "password",
        "passwd",
        "token",
        "access_token",
        "auth",
        "secret",
        "client_secret",
        "authorization",
    }
)

_LIBRARY_FORMAT_VALUES = tuple(sorted(fmt.value for fmt in Format))


def library_format_values() -> tuple[str, ...]:
    """Return sorted MIME values accepted by :class:`~tilearray.types.Format`."""

    return _LIBRARY_FORMAT_VALUES


def redact_url(url: str) -> str:
    """Return ``url`` with known secret query values replaced by ``***``."""

    parts = urlsplit(url)
    if not parts.query:
        return url
    encoded: list[str] = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        safe_key = quote(key, safe="")
        if key.lower() in _SECRET_QUERY_KEYS:
            encoded.append(f"{safe_key}=***")
        else:
            encoded.append(f"{safe_key}={quote(value, safe='')}")
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, "&".join(encoded), parts.fragment)
    )


def looks_like_html(
    body: str | bytes | None, *, content_type: str | None = None
) -> bool:
    """True when a response body/content-type looks like an HTML portal page."""

    if content_type:
        lowered = content_type.lower()
        if "text/html" in lowered or "application/xhtml" in lowered:
            return True
    if body is None:
        return False
    if isinstance(body, bytes):
        byte_sample = body[:256].lstrip().lower()
        return byte_sample.startswith((b"<!doctype html", b"<html"))
    text_sample = body.lstrip()[:256].lower()
    return text_sample.startswith(("<!doctype html", "<html"))


def looks_like_ogc_exception_report(body: str | bytes | None) -> bool:
    """True when a body is an OGC ExceptionReport / ServiceExceptionReport."""

    if body is None:
        return False
    if isinstance(body, bytes):
        sample = body[:4000].decode("utf-8", errors="replace")
    else:
        sample = body[:4000]
    lowered = sample.lower()
    return (
        "exceptionreport" in lowered
        or "serviceexceptionreport" in lowered
        or ("<exception" in lowered and "exceptiontext" in lowered)
    )


def ogc_exception_report_message(
    body: str | bytes,
    *,
    protocol: str,
    url: str,
) -> str:
    """Human-readable ParseError / ServiceError text for an OGC exception body."""

    if isinstance(body, bytes):
        text = body.decode("utf-8", errors="replace")
    else:
        text = body
    detail: str | None = None
    # Prefer ExceptionText / ServiceException body content when present.
    for marker in ("ExceptionText", "ServiceException"):
        start = text.find(f"<{marker}")
        if start < 0:
            start = text.find(f"<{marker.lower()}")
        if start < 0:
            continue
        gt = text.find(">", start)
        end = text.find(f"</{marker}", gt)
        if end < 0:
            end = text.find(f"</{marker.lower()}", gt)
        if gt >= 0 and end > gt:
            detail = text[gt + 1 : end].strip()
            if detail:
                break
    safe = redact_url(url)
    lines = [
        f"{protocol} GetCapabilities returned an OGC ExceptionReport for {safe}, "
        "not a capabilities document.",
    ]
    if detail:
        lines.append(f"  Server message: {detail}")
    else:
        lines.append(
            "  Hint: check layer/coverage identifiers, credentials "
            "(params=/headers=), and base_url."
        )
    return "\n".join(lines)


def format_values_for_message(formats: Iterable[Format | str] | None) -> str | None:
    """Join format values for an error line, or ``None`` when empty."""

    if not formats:
        return None
    values: list[str] = []
    for fmt in formats:
        text = fmt.value if isinstance(fmt, Format) else str(fmt)
        if text and text not in values:
            values.append(text)
    if not values:
        return None
    return ", ".join(values)


def unsupported_format_message(
    protocol: str,
    fmt: Any,
    *,
    server_formats: Sequence[Format | str] | None = None,
    native_format: Format | str | None = None,
) -> str:
    """Build a ValidationError message for an unknown library format string."""

    library = ", ".join(_LIBRARY_FORMAT_VALUES)
    lines = [
        f"Unsupported {protocol} format {fmt!r}.",
        f"  Requested: {fmt!r}",
        f"  Library accepts: {library}",
    ]
    server_bits: list[str] = []
    if server_formats:
        joined = format_values_for_message(server_formats)
        if joined:
            server_bits.append(joined)
    if native_format is not None:
        native = (
            native_format.value
            if isinstance(native_format, Format)
            else str(native_format)
        )
        if native and native not in server_bits:
            server_bits.append(f"native={native}")
    if server_bits:
        lines.append(f"  Server (DescribeCoverage) reported: {'; '.join(server_bits)}")
    lines.append(
        "  Hint: use Format.GEOTIFF / 'image/tiff', Format.PNG / 'image/png', "
        "or Format.JPEG / 'image/jpeg'; omit output_format to use native when available."
    )
    return "\n".join(lines)


def _http_status(exc: BaseException) -> int | None:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    cause = getattr(exc, "cause", None)
    if isinstance(cause, BaseException):
        return _http_status(cause)
    underlying = exc.__cause__
    if isinstance(underlying, BaseException):
        return _http_status(underlying)
    return None


def _httpx_root(exc: BaseException) -> BaseException:
    current: BaseException = exc
    while True:
        cause = getattr(current, "cause", None)
        if isinstance(cause, httpx.HTTPError):
            return cause
        if isinstance(current, httpx.HTTPError):
            return current
        underlying = current.__cause__
        if isinstance(underlying, BaseException) and underlying is not current:
            current = underlying
            continue
        if isinstance(cause, BaseException) and cause is not current:
            current = cause
            continue
        return current


def format_http_failure(
    exc: BaseException,
    *,
    request_name: str,
    url: str,
    protocol: str,
    coverage_id: str | None = None,
    layer: str | None = None,
) -> str:
    """Outer NetworkError text for caps / DescribeCoverage HTTP failures."""

    safe_url = redact_url(url)
    root = _httpx_root(exc)
    status = _http_status(exc)
    identity = ""
    if coverage_id is not None:
        identity = f" coverageId={coverage_id!r}"
    elif layer is not None:
        identity = f" layer={layer!r}"

    lines = [
        f"{protocol} {request_name} failed for {safe_url}{identity}.",
        f"  Tried: GET {request_name} against {safe_url}",
        f"  Cause: {type(root).__name__}: {root}",
    ]
    if status is not None:
        lines.append(f"  Status: HTTP {status}")
        if status in {401, 403}:
            lines.append(
                "  Hint: auth/forbidden — pass credentials via config "
                "params=/headers= (not soft-fail); check base_url is the service endpoint."
            )
        elif status == 404:
            lines.append(
                "  Hint: check base_url path spelling; if the host is reachable, "
                "also verify coverage_id / layer against GetCapabilities."
            )
        else:
            lines.append(
                "  Hint: inspect the host response; wrong coverage_id can surface as "
                "HTTP 4xx on some servers."
            )
    elif isinstance(
        root, (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException)
    ):
        lines.append(
            "  Hint: check base_url host spelling / DNS / VPN; "
            "this is not a coverage_id problem."
        )
    else:
        lines.append(
            "  Hint: check base_url and network reachability before changing coverage_id."
        )
    return "\n".join(lines)


def describe_coverage_parse_message(
    coverage_id: str,
    url: str,
    body: str,
    *,
    content_type: str | None = None,
    parse_error: ParseError | None = None,
) -> str:
    """Message when DescribeCoverage returned a non-coverage payload."""

    safe_url = redact_url(url)
    if looks_like_html(body, content_type=content_type):
        return (
            f"DescribeCoverage for coverage_id={coverage_id!r} at {safe_url} "
            "returned HTML (login/portal page), not WCS XML.\n"
            "  Hint: check base_url and credentials via params=/headers=; "
            "this is unlikely a coverage_id typo."
        )
    detail = str(parse_error) if parse_error is not None else "no coverage description"
    return (
        f"DescribeCoverage for coverage_id={coverage_id!r} at {safe_url} "
        f"did not yield a coverage description ({detail}).\n"
        "  Hint: call service.get_capabilities() and use a CoverageId from the list; "
        "for EA DSP, CoverageId looks like 'UUID__…' and is not the URL path slug "
        "(see docs/troubleshooting)."
    )


def coverage_build_service_network_message(
    coverage_id: str,
    base_url: str,
    exc: BaseException,
) -> str:
    """Outer NetworkError for ``WCSConfig.build_service`` transport/HTTP failures."""

    safe_url = redact_url(base_url)
    root = _httpx_root(exc)
    status = _http_status(exc)
    lines = [
        f"Failed to validate coverage {coverage_id!r} against {safe_url}.",
        f"  Request: WCS DescribeCoverage coverageId={coverage_id!r}",
        f"  Cause: {type(root).__name__}: {root}",
    ]
    if isinstance(
        root, (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException)
    ) or (status is None and "ConnectError" in type(root).__name__):
        lines.append(
            "  Hint: check base_url host spelling / DNS / VPN; "
            "this is not a coverage_id problem."
        )
    elif status is not None:
        lines.append(f"  Status: HTTP {status}")
        if status in {401, 403}:
            lines.append(
                "  Hint: auth/forbidden — use params=/headers=; do not soft-fail 401."
            )
        else:
            lines.append(
                "  Hint: if the host is reachable, verify coverage_id via GetCapabilities; "
                "also check base_url path."
            )
    else:
        # Preserve any already-enriched NetworkError body from describe_coverage.
        inner = str(exc).strip()
        if inner and inner not in lines[0]:
            lines.append(f"  Detail: {inner}")
        lines.append(
            "  Hint: check base_url and network reachability before changing coverage_id."
        )
    return "\n".join(lines)


def coverage_build_service_config_message(
    coverage_id: str,
    base_url: str,
    exc: TileArrayError,
) -> str:
    """Outer ConfigurationError when DescribeCoverage parse/availability fails."""

    safe_url = redact_url(base_url)
    detail = str(exc).strip()
    htmlish = "HTML" in detail or "login/portal" in detail.lower()
    if htmlish:
        return (
            f"Coverage {coverage_id!r} could not be validated at {safe_url}: "
            "response was HTML / not a WCS coverage description.\n"
            f"  Detail: {detail}\n"
            "  Hint: check base_url and auth via params=/headers=; "
            "this is unlikely a coverage_id typo."
        )
    return (
        f"Coverage {coverage_id!r} is not available from {safe_url}.\n"
        f"  Detail: {detail}\n"
        "  Hint: call service.get_capabilities() / check CoverageId values; "
        "for EA DSP, CoverageId ≠ URL path slug (see docs/troubleshooting)."
    )


def tile_fetch_failure_message(
    url: str,
    message: str,
    *,
    content_type: str | None = None,
    body: bytes | str | None = None,
) -> str:
    """NetworkError text for a failed tile GET."""

    safe_url = redact_url(url)
    lines = [f"Tile fetch failed for {safe_url}: {message}"]
    if looks_like_html(body, content_type=content_type) or looks_like_html(message):
        lines.append(
            "  Hint: response looks like HTML (auth/portal page), not tile bytes; "
            "check credentials via params=/headers= and base_url."
        )
    elif "401" in message or "403" in message:
        lines.append(
            "  Hint: auth/forbidden — pass credentials via params=/headers=; "
            "soft-fail does not paper over 401."
        )
    return "\n".join(lines)


def undetectable_service_type_message(url: str) -> str:
    """ConfigurationError text when URL does not imply WCS/WMS/WMTS/XYZ."""

    safe_url = redact_url(url)
    return (
        f"Unable to detect service type from URL: {safe_url}\n"
        "  Hint: pass an explicit WCSConfig / WMSConfig / WMTSConfig / XYZConfig, "
        "or include a service path (wcs/wms/wmts) or XYZ '{z}/{x}/{y}' placeholders. "
        "For inspect_endpoint on opaque OGC URLs, pass service_type='wmts' "
        "(or wms/wcs) — or let inspect probe GetCapabilities."
    )


def missing_decoder_message(fmt: Any) -> str:
    """ConfigurationError text when no built-in tile decoder matches."""

    library = ", ".join(_LIBRARY_FORMAT_VALUES)
    return (
        f"No tile decoder available for format {fmt!r}; please provide tile_decoder.\n"
        f"  Library decodes by default: {library} "
        "(GeoTIFF via tifffile; PNG/JPEG via Pillow or imageio)."
    )
