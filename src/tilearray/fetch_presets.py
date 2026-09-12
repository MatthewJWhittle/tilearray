"""Fetch-policy presets for common public tile endpoints."""

from __future__ import annotations

from typing import Any

from ._version import __version__

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

    Uses a modest in-flight cap to avoid overload retry storms on busy hosts,
    while leaving the healthy path unconstrained by a fixed per-second throttle
    (429/503 and ``Retry-After`` are handled by :class:`~tilearray.fetch.TileFetcher`).
    """

    return {
        "max_concurrent_requests": 2,
        "rate_limit_per_second": None,
        "fetch_retries": 4,
        "fetch_timeout": 60.0,
    }
