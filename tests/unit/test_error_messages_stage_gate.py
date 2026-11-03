"""Contract tests for actionable stage-gate error messages (#88).

Matrix (offline):

| Scenario | Expected class | Locked substrings |
|----------|----------------|-------------------|
| Unknown format string | ValidationError | Requested, Library accepts, image/tiff |
| Caps-aware format (cached DescribeCoverage) | ValidationError | Server (DescribeCoverage), image/png |
| DNS / connect on DescribeCoverage | NetworkError | ConnectError, not a coverage_id |
| HTTP 404 on DescribeCoverage | NetworkError | Status: HTTP 404, GetCapabilities |
| HTML body on DescribeCoverage | ParseError / ConfigurationError | HTML, params=/headers |
| Empty / ExceptionReport coverage XML | ParseError | get_capabilities, CoverageId |
| Missing coverage_id | ValidationError | coverage_id |
| Undetectable URL | ConfigurationError | WCSConfig, {z}/{x}/{y} |
| Secret query redaction | (helper) | *** not secret value |
| Soft-fail + 401 | ABORT (unchanged) | see test_soft_fail.py |
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from tilearray.error_messages import redact_url
from tilearray.errors import (
    ConfigurationError,
    NetworkError,
    ParseError,
    ValidationError,
)
from tilearray.service.base import detect_service_type
from tilearray.service.config import WCSConfig
from tilearray.service.wcs import WCSParser, WCSService
from tilearray.types import CoverageDescription, Format

pytestmark = pytest.mark.unit


def test_unsupported_format_lists_library_values() -> None:
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="cov-1",
        output_format=Format.GEOTIFF,
    )
    with pytest.raises(ValidationError, match="Unsupported WCS format") as exc_info:
        service._coerce_format("image/tif")
    message = str(exc_info.value)
    assert "Requested: 'image/tif'" in message
    assert "Library accepts:" in message
    assert "image/tiff" in message
    assert "image/png" in message
    assert "Hint:" in message


def test_unsupported_format_includes_server_formats_when_cached() -> None:
    service = WCSService(
        "http://example.com/wcs",
        coverage_id="cov-1",
        output_format=Format.GEOTIFF,
    )
    service.set_coverage_metadata(
        CoverageDescription(
            identifier="cov-1",
            supported_formats=[Format.PNG, Format.GEOTIFF],
            native_format=Format.GEOTIFF,
        )
    )
    with pytest.raises(ValidationError) as exc_info:
        service._coerce_format("image/tif")
    message = str(exc_info.value)
    assert "Server (DescribeCoverage) reported:" in message
    assert "image/png" in message
    assert "image/tiff" in message


def test_describe_coverage_connect_error_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WCSService("http://example.invalid/wcs", coverage_id="cov-1")

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("[Errno -2] Name or service not known")

    monkeypatch.setattr("tilearray.service.wcs.http_get", boom)

    with pytest.raises(NetworkError) as exc_info:
        service.describe_coverage()
    message = str(exc_info.value)
    assert "DescribeCoverage" in message
    assert "ConnectError" in message
    assert "not a coverage_id problem" in message
    assert isinstance(exc_info.value.cause, httpx.ConnectError)


def test_describe_coverage_http_404_includes_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WCSService("http://example.com/wcs", coverage_id="missing-id")

    def boom(*args: Any, **kwargs: Any) -> Any:
        request = httpx.Request("GET", "http://example.com/wcs")
        response = httpx.Response(404, request=request, text="not found")
        raise httpx.HTTPStatusError("404", request=request, response=response)

    monkeypatch.setattr("tilearray.service.wcs.http_get", boom)

    with pytest.raises(NetworkError) as exc_info:
        service.describe_coverage()
    message = str(exc_info.value)
    assert "Status: HTTP 404" in message
    assert "coverageId='missing-id'" in message
    assert "GetCapabilities" in message or "coverage_id" in message


def test_describe_coverage_html_body_not_blamed_on_coverage_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WCSService("http://example.com/login", coverage_id="cov-1")

    def fake_get(*args: Any, **kwargs: Any) -> httpx.Response:
        return httpx.Response(
            200,
            text="<!DOCTYPE html><html><body>Login</body></html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "http://example.com/login"),
        )

    monkeypatch.setattr("tilearray.service.wcs.http_get", fake_get)

    with pytest.raises(ParseError) as exc_info:
        service.describe_coverage()
    message = str(exc_info.value)
    assert "HTML" in message
    assert "params=/headers" in message
    assert "unlikely a coverage_id" in message.lower() or "coverage_id typo" in message


def test_build_service_connect_error_preserves_transport_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WCSConfig.from_url("http://example.invalid/wcs", coverage_id="cov-1")

    def fake_describe(
        self: WCSService, coverage_id: str | None = None, **params: Any
    ) -> Any:
        raise NetworkError(
            "inner",
            cause=httpx.ConnectError("Name or service not known"),
        )

    monkeypatch.setattr(WCSService, "describe_coverage", fake_describe)

    with pytest.raises(NetworkError) as exc_info:
        config.build_service()
    message = str(exc_info.value)
    assert "Failed to validate coverage" in message
    assert "ConnectError" in message
    assert "not a coverage_id problem" in message
    assert "Coverage '" not in message  # not the mis-blame config wrap


def test_build_service_html_parse_says_not_coverage_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WCSConfig.from_url("http://example.com/portal", coverage_id="cov-1")

    def fake_describe(
        self: WCSService, coverage_id: str | None = None, **params: Any
    ) -> Any:
        raise ParseError(
            "DescribeCoverage response looks like HTML (login/portal page), not WCS XML."
        )

    monkeypatch.setattr(WCSService, "describe_coverage", fake_describe)

    with pytest.raises(ConfigurationError) as exc_info:
        config.build_service()
    message = str(exc_info.value)
    assert "HTML" in message
    assert "params=/headers" in message
    assert "unlikely a coverage_id" in message.lower() or "coverage_id typo" in message


def test_build_service_unknown_coverage_hints_get_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = WCSConfig.from_url("http://example.com/wcs", coverage_id="typo-id")

    def fake_describe(
        self: WCSService, coverage_id: str | None = None, **params: Any
    ) -> Any:
        raise ParseError(
            "No coverage description found in XML "
            "(empty response or OGC ExceptionReport). "
            "Hint: call get_capabilities() and check CoverageId values."
        )

    monkeypatch.setattr(WCSService, "describe_coverage", fake_describe)

    with pytest.raises(ConfigurationError) as exc_info:
        config.build_service()
    message = str(exc_info.value)
    assert "not available" in message
    assert "get_capabilities" in message
    assert "CoverageId" in message or "coverage" in message.lower()


def test_parser_empty_coverage_xml_hints_capabilities() -> None:
    parser = WCSParser("http://example.com/wcs")
    xml = (
        '<?xml version="1.0"?>'
        '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/2.0">'
        '<ows:Exception exceptionCode="InvalidParameterValue">'
        "<ows:ExceptionText>Unknown coverage</ows:ExceptionText>"
        "</ows:Exception></ows:ExceptionReport>"
    )
    with pytest.raises(ParseError) as exc_info:
        parser.parse_describe_coverage(xml)
    message = str(exc_info.value)
    assert "No coverage description" in message
    assert "get_capabilities" in message


def test_detect_service_type_hint_mentions_typed_config() -> None:
    with pytest.raises(ConfigurationError) as exc_info:
        detect_service_type("https://example.com/unknown-endpoint")
    message = str(exc_info.value)
    assert "Unable to detect service type" in message
    assert "WCSConfig" in message
    assert "{z}/{x}/{y}" in message


def test_redact_url_masks_secret_query_keys() -> None:
    url = "https://example.com/wcs?service=WCS&key=super-secret&coverageId=abc"
    redacted = redact_url(url)
    assert "super-secret" not in redacted
    assert "key=***" in redacted
    assert "coverageId=abc" in redacted


def test_describe_coverage_redacts_secret_in_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = WCSService(
        "http://example.com/wcs?api_key=should-not-leak",
        coverage_id="cov-1",
    )

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("tilearray.service.wcs.http_get", boom)

    with pytest.raises(NetworkError) as exc_info:
        service.describe_coverage()
    assert "should-not-leak" not in str(exc_info.value)
    assert "api_key=***" in str(exc_info.value)
