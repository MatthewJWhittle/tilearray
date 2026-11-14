"""Failed-tile error bodies: parse OGC ExceptionReports; strip HTML for snippets."""

from __future__ import annotations

import httpx
import pytest

from tilearray.error_messages import redact_url
from tilearray.fetch import _response_from_httpx

pytestmark = pytest.mark.unit

_OWS20_LONG_PREAMBLE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<ows:ExceptionReport xmlns:xs="http://www.w3.org/2001/XMLSchema" '
    'xmlns:ows="http://www.opengis.net/ows/2.0" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xsi:schemaLocation="http://www.opengis.net/ows/2.0 '
    'http://schemas.opengis.net/ows/2.0/owsExceptionReport.xsd" version="2.0.1">'
    '<ows:Exception exceptionCode="NoSuchCoverage" locator="coverageId">'
    "<ows:ExceptionText>example</ows:ExceptionText>"
    "</ows:Exception></ows:ExceptionReport>"
)

_WCS10_SERVICE_EXCEPTION = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<ServiceExceptionReport version="1.2.0">'
    '<ServiceException code="InvalidCoverage">coverage not found</ServiceException>'
    "</ServiceExceptionReport>"
)

_MALFORMED_OGC_XML = (
    '<?xml version="1.0"?><ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/2.0">'
    "<ows:Exception exceptionCode="  # truncated / broken
)

_HTML_429_PAGE = """<!DOCTYPE html>
<html>
<head>
  <title>Too Many Requests</title>
  <meta charset="utf-8"/>
  <style>body { color: red; }</style>
  <script>console.log("ignore");</script>
</head>
<body>
  <h1>Rate limited</h1>
  <p>You are making too many requests. Please slow down.</p>
</body>
</html>
"""


def _http_response(
    status: int,
    content: str | bytes,
    *,
    content_type: str,
    url: str = "https://example.com/wcs",
) -> httpx.Response:
    raw = content.encode() if isinstance(content, str) else content
    return httpx.Response(
        status,
        content=raw,
        headers={"content-type": content_type},
        request=httpx.Request("GET", url),
    )


def test_ows20_exception_report_message_includes_code_and_text() -> None:
    """Long namespace preamble must not hide exceptionCode / ExceptionText."""

    response = _response_from_httpx(
        _http_response(404, _OWS20_LONG_PREAMBLE, content_type="application/xml"),
        "https://example.com/wcs",
    )
    msg = response.error_message or ""
    assert response.success is False
    assert response.ogc_exception_code == "NoSuchCoverage"
    assert "NoSuchCoverage" in msg
    assert "locator=coverageId" in msg
    assert "example" in msg
    assert msg.startswith("HTTP 404 (OGC NoSuchCoverage")


def test_wcs10_service_exception_report_shows_code() -> None:
    response = _response_from_httpx(
        _http_response(
            404, _WCS10_SERVICE_EXCEPTION, content_type="application/vnd.ogc.se_xml"
        ),
        "https://example.com/wcs",
    )
    msg = response.error_message or ""
    assert response.ogc_exception_code == "InvalidCoverage"
    assert "InvalidCoverage" in msg
    assert "coverage not found" in msg


def test_malformed_ogc_xml_falls_back_to_raw_snippet_without_raising() -> None:
    response = _response_from_httpx(
        _http_response(404, _MALFORMED_OGC_XML, content_type="application/xml"),
        "https://example.com/wcs",
    )
    msg = response.error_message or ""
    assert response.success is False
    assert response.ogc_exception_code is None
    assert "OGC ExceptionReport" in msg
    assert "ExceptionReport" in msg
    # Raw snippet path — truncated body still appears, no parse raise.
    assert "ows:ExceptionReport" in msg or "ExceptionReport" in msg


def test_html_429_message_shows_visible_sentence_not_head_markup() -> None:
    response = _response_from_httpx(
        _http_response(429, _HTML_429_PAGE, content_type="text/html; charset=utf-8"),
        "https://example.com/tile",
    )
    msg = response.error_message or ""
    assert response.success is False
    assert "You are making too many requests" in msg
    assert "<html" not in msg.lower()
    assert "<head" not in msg.lower()
    assert "<script" not in msg.lower()


def test_failed_tile_url_redaction_still_masks_secrets() -> None:
    url = "https://example.com/wcs?api_key=super-secret&coverageId=abc"
    response = _response_from_httpx(
        _http_response(
            404, _OWS20_LONG_PREAMBLE, content_type="application/xml", url=url
        ),
        url,
    )
    assert "super-secret" not in response.url
    assert "api_key=***" in response.url or "***" in response.url
    assert redact_url(url) == response.url
    assert response.error_message is not None
    assert "super-secret" not in response.error_message


def test_successful_tile_unchanged() -> None:
    response = _response_from_httpx(
        _http_response(
            200,
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
            content_type="image/png",
        ),
        "https://example.com/tile.png",
    )
    assert response.success is True
    assert response.error_message is None
    assert response.ogc_exception_code is None
    assert response.data.startswith(b"\x89PNG")


def test_http_200_ogc_exception_sets_code_and_parsed_message() -> None:
    response = _response_from_httpx(
        _http_response(200, _OWS20_LONG_PREAMBLE, content_type="image/tiff"),
        "https://example.com/wcs",
    )
    msg = response.error_message or ""
    assert response.success is False
    assert response.ogc_exception_code == "NoSuchCoverage"
    assert "NoSuchCoverage" in msg
    assert "example" in msg
    assert "not tile bytes" in msg
