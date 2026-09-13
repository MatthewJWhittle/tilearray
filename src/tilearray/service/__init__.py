"""Service abstractions and implementations for OGC-style tile services."""

from .base import (
    BaseService,
    TileGeometry,
    detect_service_type,
    get_service,
    register_service,
)
from .config import ServiceConfig, WCSConfig, WMSConfig, WMTSConfig, XYZConfig
from .wcs import WCSParser, WCSService
from .wms import WMSService
from .wmts import WMTSParser, WMTSService
from .xyz import XYZService

__all__ = [
    "BaseService",
    "TileGeometry",
    "detect_service_type",
    "get_service",
    "register_service",
    "ServiceConfig",
    "WCSConfig",
    "WMSConfig",
    "WMTSConfig",
    "XYZConfig",
    "WCSParser",
    "WCSService",
    "WMSService",
    "WMTSParser",
    "WMTSService",
    "XYZService",
]
