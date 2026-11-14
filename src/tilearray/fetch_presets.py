"""Fetch-policy presets for common public tile endpoints.

Host folklore (politeness ceilings, User-Agent, AIMD) lives here — not on
``WCSConfig`` / ``XYZConfig``. Compose with protocol constructors::

    WCSConfig.from_url(url, coverage_id, **ea_dsp_fetch_defaults())
    XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=16, **osm_fetch_defaults())
"""

from __future__ import annotations

from typing import Any

from ._version import __version__
from .pressure import PressureClassifier, PressureSignature

__all__ = [
    "OSM_TILE_TEMPLATE",
    "EA_LIDAR_WCS_URL",
    "EA_LIDAR_COVERAGE_ID",
    "EA_LIDAR_BENCH_BBOX",
    "DEFAULT_CONTACT_URL",
    "tilearray_user_agent",
    "osm_fetch_defaults",
    "ea_dsp_fetch_defaults",
]

# EA LiDAR WCS occasionally returns a one-off HTTP 404 for an in-extent
# subset that succeeds on the next attempt. Cap retries so permanent 404s
# (out-of-extent / unknown coverage) still fail after a few tries, without
# changing the global fail-fast 404 default for other hosts.
_EA_TRANSIENT_404_MAX_RETRIES = 2


def _ea_dsp_pressure_classifier() -> PressureClassifier:
    """Built-in signatures plus a bounded EA-only retryable 404 rule."""

    classifier = PressureClassifier()
    classifier.register(
        PressureSignature(
            name="ea_transient_404",
            status_codes=frozenset({404}),
            retryable=True,
            max_retries=_EA_TRANSIENT_404_MAX_RETRIES,
        )
    )
    return classifier


OSM_TILE_TEMPLATE = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

EA_LIDAR_WCS_URL = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)
EA_LIDAR_COVERAGE_ID = (
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
)

# Tiny 64 m OSGB bbox used in contract tests / EA bench (4 tiles at 32x32 chunks).
EA_LIDAR_BENCH_BBOX = (431900.0, 382700.0, 431964.0, 382764.0)

DEFAULT_CONTACT_URL = "https://github.com/MatthewJWhittle/tilearray/issues"


def tilearray_user_agent(contact_url: str = DEFAULT_CONTACT_URL) -> str:
    """Return a descriptive User-Agent string for public tile services."""

    return f"tilearray/{__version__} (+{contact_url})"


def osm_fetch_defaults(
    *,
    user_agent: str | None = None,
    contact_url: str = DEFAULT_CONTACT_URL,
) -> dict[str, Any]:
    """
    Polite fetch defaults for OpenStreetMap raster tiles (OSMF policy).

    - Identifiable User-Agent (required by OSM tile usage policy)
    - Low concurrency (2 in-flight) and 2 req/s per host
    """

    return {
        "max_concurrent_requests": 2,
        "adaptive_concurrency": False,
        "rate_limit_per_second": 2.0,
        "fetch_retries": 2,
        "fetch_timeout": 30.0,
        "headers": {
            "User-Agent": user_agent or tilearray_user_agent(contact_url),
        },
    }


def ea_dsp_fetch_defaults() -> dict[str, Any]:
    """
    Fetch defaults for Environment Agency Data Service Platform WCS endpoints.

    Uses in-process AIMD concurrency (starts modest, ramps on success, backs off
    hard on 403/408/429 / timeouts) with a bounded ceiling. No fixed per-second throttle;
    ``Retry-After`` and transient 403/408/429/503 are handled by the polite fetch gate.

    Also registers a host-specific pressure rule so a one-off HTTP 404 can retry
    a couple of times (EA sometimes 404s an in-extent subset that succeeds on
    the next attempt). Other hosts keep the global fail-fast 404 default.
    """

    return {
        "max_concurrent_requests": 10,
        "initial_concurrent_requests": 8,
        "min_concurrent_requests": 4,
        "multiplicative_decrease": 0.75,
        "adaptive_concurrency": True,
        "forbidden_circuit_breaker": True,
        "forbidden_window_seconds": 5.0,
        "forbidden_threshold": 6,
        "forbidden_cooldown_seconds": 15.0,
        "rate_limit_per_second": None,
        "fetch_retries": 4,
        "fetch_timeout": 60.0,
        "pressure_classifier": _ea_dsp_pressure_classifier(),
    }
