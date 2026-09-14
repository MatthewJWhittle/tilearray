"""Unit tests for the shared HTTP pressure classifier."""

from __future__ import annotations

import httpx
import pytest

from tilearray.pressure import (
    BUILTIN_PRESSURE_SIGNATURES,
    PressureClassifier,
    PressureSignature,
    classify_response,
    reset_pressure_classifier,
)

pytestmark = pytest.mark.unit

_OGC_404_BODY = b"""<?xml version="1.0" encoding="UTF-8"?>
<ExceptionReport xmlns="http://www.opengis.net/ogc">
  <Exception exceptionCode="InvalidParameterValue" locator="SUBSETTINGCRS">
    <ExceptionText>Invalid or unsupported SubsettingCrs</ExceptionText>
  </Exception>
</ExceptionReport>"""


@pytest.fixture(autouse=True)
def reset_classifier() -> None:
    reset_pressure_classifier()
    yield
    reset_pressure_classifier()


@pytest.mark.parametrize(
    ("status_code", "expected_name"),
    [
        (403, "gateway_forbidden"),
        (408, "gateway_timeout"),
        (429, "rate_limit"),
        (500, "upstream_error"),
        (503, "upstream_error"),
    ],
)
def test_classify_gateway_status_codes(status_code: int, expected_name: str) -> None:
    response = httpx.Response(status_code, text="pressure")
    result = classify_response(response)

    assert result.retryable is True
    assert result.aimd_pressure is True
    assert result.signature is not None
    assert result.signature.name == expected_name


def test_classify_ogc_transient_404() -> None:
    response = httpx.Response(
        404,
        content=_OGC_404_BODY,
        headers={"content-type": "application/xml"},
    )
    result = classify_response(response)

    assert result.retryable is True
    assert result.aimd_pressure is True
    assert result.circuit_breaker is False
    assert result.signature is not None
    assert result.signature.name == "ogc_transient_404"


def test_classify_plain_404_is_not_retryable() -> None:
    response = httpx.Response(404, text="Not Found")
    result = classify_response(response)

    assert result.retryable is False
    assert result.signature is None


def test_circuit_breaker_signatures() -> None:
    forbidden = classify_response(httpx.Response(403, text="Forbidden"))
    rate_limit = classify_response(httpx.Response(429, text="Too Many Requests"))
    ogc_404 = classify_response(httpx.Response(404, content=_OGC_404_BODY))
    upstream = classify_response(httpx.Response(503, text="busy"))

    assert forbidden.circuit_breaker is True
    assert rate_limit.circuit_breaker is True
    assert ogc_404.circuit_breaker is False
    assert upstream.circuit_breaker is False


def test_custom_signature_is_pluggable() -> None:
    classifier = PressureClassifier()
    classifier.register(
        PressureSignature(
            name="custom_soft_418",
            status_codes=frozenset({418}),
            circuit_breaker=True,
        )
    )

    result = classifier.classify(httpx.Response(418, text="teapot"))

    assert result.retryable is True
    assert result.circuit_breaker is True
    assert result.signature is not None
    assert result.signature.name == "custom_soft_418"


def test_body_substrings_require_any_match() -> None:
    classifier = PressureClassifier(
        signatures=(
            PressureSignature(
                name="xml_fault",
                status_codes=frozenset({502}),
                body_substrings=(b"<fault>", b"exceptionreport"),
            ),
        )
    )

    match = classifier.classify(httpx.Response(502, content=b"<fault>upstream</fault>"))
    miss = classifier.classify(httpx.Response(502, content=b"plain error"))

    assert match.retryable is True
    assert miss.retryable is False


def test_builtin_signatures_cover_known_dialects() -> None:
    names = {signature.name for signature in BUILTIN_PRESSURE_SIGNATURES}
    assert "gateway_forbidden" in names
    assert "rate_limit" in names
    assert "upstream_error" in names
    assert "ogc_transient_404" in names
