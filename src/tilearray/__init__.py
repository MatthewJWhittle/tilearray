"""TileArray - high-level helpers for loading OGC services into xarray."""

from ._version import __version__

__author__ = "Your Name"
__email__ = "your.email@example.com"

from .array import create_array, load_array
from .service import (
    BaseService,
    TileGeometry,
    XYZConfig,
    XYZService,
    detect_service_type,
    get_service,
    register_service,
)
from .service.wcs import WCSParser, WCSService
from .types import (
    BBoxTuple,
    BoundingBox,
    CoverageDescription,
    CRS,
    Format,
    ServiceCapabilities,
    ServiceTypeEnum,
    SpatialExtent,
    TileRequest,
    TileResponse,
    TemporalExtent,
    WCSResponse,
)

__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "create_array",
    "load_array",
    "BaseService",
    "TileGeometry",
    "detect_service_type",
    "get_service",
    "register_service",
    "XYZConfig",
    "XYZService",
    "WCSParser",
    "WCSService",
    "BBoxTuple",
    "BoundingBox",
    "CoverageDescription",
    "CRS",
    "Format",
    "ServiceCapabilities",
    "ServiceTypeEnum",
    "SpatialExtent",
    "TileRequest",
    "TileResponse",
    "TemporalExtent",
    "WCSResponse",
]