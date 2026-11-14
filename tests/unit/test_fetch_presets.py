"""Tests for fetch-policy presets and compose-with-from_url helpers."""

from __future__ import annotations

import pytest

from tilearray.fetch import FetchPolicy
from tilearray.fetch_presets import (
    OSM_TILE_TEMPLATE,
    ea_dsp_fetch_defaults,
    osm_fetch_defaults,
    tilearray_user_agent,
)
from tilearray.service.config import WCSConfig, XYZConfig
from tilearray.types import Format

pytestmark = pytest.mark.unit


def test_osm_fetch_defaults_include_user_agent() -> None:
    defaults = osm_fetch_defaults()
    assert defaults["max_concurrent_requests"] == 2
    assert defaults["adaptive_concurrency"] is False
    assert defaults["rate_limit_per_second"] == 2.0
    assert "User-Agent" in defaults["headers"]
    assert "tilearray/" in defaults["headers"]["User-Agent"]


def test_ea_dsp_fetch_defaults_use_aimd() -> None:
    defaults = ea_dsp_fetch_defaults()
    assert defaults["max_concurrent_requests"] == 10
    assert defaults["initial_concurrent_requests"] == 8
    assert defaults["min_concurrent_requests"] == 4
    assert defaults["multiplicative_decrease"] == 0.75
    assert defaults["adaptive_concurrency"] is True
    assert defaults["forbidden_circuit_breaker"] is True
    assert defaults["forbidden_window_seconds"] == 5.0
    assert defaults["forbidden_threshold"] == 6
    assert defaults["forbidden_cooldown_seconds"] == 15.0
    assert defaults["rate_limit_per_second"] is None
    assert defaults["fetch_retries"] == 4
    assert defaults["fetch_timeout"] == 60.0
    classifier = defaults["pressure_classifier"]
    names = {signature.name for signature in classifier.signatures}
    assert "ea_transient_404" in names
    ea_404 = next(s for s in classifier.signatures if s.name == "ea_transient_404")
    assert ea_404.retryable is True
    assert ea_404.max_retries == 2
    assert ea_404.status_codes == frozenset({404})


def test_xyz_config_compose_osm_preset() -> None:
    config = XYZConfig.from_url(
        OSM_TILE_TEMPLATE,
        zoom=16,
        output_format=Format.PNG,
        **osm_fetch_defaults(),
    )
    assert config.base_url == OSM_TILE_TEMPLATE
    assert config.max_concurrent_requests == 2
    assert config.adaptive_concurrency is False
    assert config.rate_limit_per_second == 2.0
    assert "User-Agent" in config.headers
    policy = config.fetch_policy()
    assert policy.max_concurrent == 2
    assert policy.adaptive_concurrency is False
    assert policy.rate_limit_per_second == 2.0


def test_wcs_config_compose_ea_dsp_preset() -> None:
    config = WCSConfig.from_url(
        "https://environment.data.gov.uk/example/wcs",
        coverage_id="test-coverage",
        **ea_dsp_fetch_defaults(),
    )
    assert config.max_concurrent_requests == 10
    assert config.initial_concurrent_requests == 8
    assert config.min_concurrent_requests == 4
    assert config.multiplicative_decrease == 0.75
    assert config.adaptive_concurrency is True
    assert config.forbidden_circuit_breaker is True
    assert config.fetch_retries == 4
    assert config.rate_limit_per_second is None
    assert config.pressure_classifier is not None
    policy = config.fetch_policy()
    assert isinstance(policy, FetchPolicy)
    assert policy.retries == 4
    assert policy.adaptive_concurrency is True
    assert policy.initial_concurrent == 8
    assert policy.min_concurrent == 4
    assert policy.multiplicative_decrease == 0.75
    assert policy.max_concurrent == 10
    assert policy.forbidden_circuit_breaker is True
    assert policy.pressure_classifier is config.pressure_classifier
    assert any(
        s.name == "ea_transient_404" for s in policy.pressure_classifier.signatures
    )


def test_custom_osm_user_agent() -> None:
    agent = tilearray_user_agent(contact_url="https://example.com/contact")
    config = XYZConfig.from_url(
        OSM_TILE_TEMPLATE,
        zoom=10,
        **osm_fetch_defaults(user_agent=agent),
    )
    assert config.headers["User-Agent"] == agent
