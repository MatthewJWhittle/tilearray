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
    make_sync_client,
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


@pytest.mark.unit
def test_make_sync_client_applies_timeout_and_limits() -> None:
    limits = httpx.Limits(max_connections=4, max_keepalive_connections=2)
    client = make_sync_client(timeout=12.5, limits=limits)
    try:
        # Single float is the read budget; connect uses the default ceiling (capped).
        assert client.timeout.read == 12.5
        assert client.timeout.connect == 10.0
        # httpx stores Limits on the transport; constructing with limits= is enough.
        assert limits.max_connections == 4
        assert isinstance(client, httpx.Client)
    finally:
        client.close()


@pytest.mark.unit
def test_make_sync_client_splits_connect_and_read() -> None:
    client = make_sync_client(connect_timeout=3.0, read_timeout=22.0)
    try:
        assert client.timeout.connect == 3.0
        assert client.timeout.read == 22.0
        assert client.timeout.write == 22.0
        assert client.timeout.pool == 3.0
    finally:
        client.close()
