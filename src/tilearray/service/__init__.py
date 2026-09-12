"""Service abstractions and implementations for OGC-style tile services."""

from .base import (
    BaseService,
    TileGeometry,
    detect_service_type,
    get_service,
    register_service,
)
from .config import ServiceConfig, WCSConfig, XYZConfig
from .wcs import WCSParser, WCSService
from .xyz import XYZService

__all__ = [
    "BaseService",
    "TileGeometry",
    "detect_service_type",
    "get_service",
    "register_service",
    "ServiceConfig",
    "WCSConfig",
    "XYZConfig",
    "WCSParser",
    "WCSService",
    "XYZService",
]
