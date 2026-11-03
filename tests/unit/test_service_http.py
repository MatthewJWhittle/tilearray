"""Unit tests for shared capabilities HTTP helpers."""

from __future__ import annotations

import httpx
import pytest
import respx

from tilearray.service.http import (
    DEFAULT_HTTP_TIMEOUT,
    compose_http_headers,
    compose_http_params,
    default_headers,
    http_get,
    resolve_timeout,
)


@pytest.mark.unit
def test_resolve_timeout_uses_config_then_default() -> None:
    assert resolve_timeout(None) == DEFAULT_HTTP_TIMEOUT
    assert resolve_timeout({}) == DEFAULT_HTTP_TIMEOUT
    assert resolve_timeout({"fetch_timeout": 12.5}) == 12.5
    assert resolve_timeout({"fetch_timeout": 0}) == DEFAULT_HTTP_TIMEOUT
    assert resolve_timeout({"fetch_timeout": "nope"}) == DEFAULT_HTTP_TIMEOUT


@pytest.mark.unit
def test_compose_http_headers_overlays_config() -> None:
    headers = compose_http_headers(
        {"headers": {"User-Agent": "override/1.0", "X-Key": "a"}},
        {"X-Peer": "1"},
    )
    assert headers["User-Agent"] == "override/1.0"
    assert headers["X-Key"] == "a"
    assert headers["X-Peer"] == "1"
    assert default_headers()["User-Agent"].startswith("tilearray/")


@pytest.mark.unit
def test_compose_http_params_merges_config() -> None:
    params = compose_http_params(
        {"service": "WCS", "request": "GetCapabilities"},
        {"params": {"username": "u", "password": "p"}},
        {"token": "t"},
    )
    assert params == {
        "service": "WCS",
        "request": "GetCapabilities",
        "username": "u",
        "password": "p",
        "token": "t",
    }


@pytest.mark.unit
@respx.mock
def test_http_get_raises_on_error_status() -> None:
    respx.get("https://example.com/caps").mock(
        return_value=httpx.Response(503, text="busy")
    )
    with pytest.raises(httpx.HTTPStatusError):
        http_get("https://example.com/caps")
