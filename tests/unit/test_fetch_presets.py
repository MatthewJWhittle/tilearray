"""Tests for fetch-policy presets and bench helpers."""

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
    assert defaults["max_concurrent_requests"] == 8
    assert defaults["initial_concurrent_requests"] == 2
    assert defaults["min_concurrent_requests"] == 1
    assert defaults["adaptive_concurrency"] is True
    assert defaults["rate_limit_per_second"] is None
    assert defaults["fetch_retries"] == 4
    assert defaults["fetch_timeout"] == 60.0


def test_xyz_config_for_openstreetmap_applies_preset() -> None:
    config = XYZConfig.for_openstreetmap(zoom=16, output_format=Format.PNG)
    assert config.base_url == OSM_TILE_TEMPLATE
    assert config.max_concurrent_requests == 2
    assert config.adaptive_concurrency is False
    assert config.rate_limit_per_second == 2.0
    assert "User-Agent" in config.headers
    policy = config.fetch_policy()
    assert policy.max_concurrent == 2
    assert policy.adaptive_concurrency is False
    assert policy.rate_limit_per_second == 2.0


def test_wcs_config_for_ea_dsp_applies_preset() -> None:
    config = WCSConfig.for_ea_dsp(
        "https://environment.data.gov.uk/example/wcs",
        coverage_id="test-coverage",
    )
    assert config.max_concurrent_requests == 8
    assert config.initial_concurrent_requests == 2
    assert config.adaptive_concurrency is True
    assert config.fetch_retries == 4
    assert config.rate_limit_per_second is None
    policy = config.fetch_policy()
    assert isinstance(policy, FetchPolicy)
    assert policy.retries == 4
    assert policy.adaptive_concurrency is True
    assert policy.initial_concurrent == 2
    assert policy.max_concurrent == 8


def test_custom_osm_user_agent() -> None:
    agent = tilearray_user_agent(contact_url="https://example.com/contact")
    config = XYZConfig.for_openstreetmap(zoom=10, user_agent=agent)
    assert config.headers["User-Agent"] == agent
