"""TileArray - high-level helpers for loading OGC services into xarray."""

from ._version import __version__

__author__ = "Matthew Whittle"
__email__ = "47574804+MatthewJWhittle@users.noreply.github.com"

from .array import compute_with_policy, create_array, load_array
from .service import (
    BaseService,
    TileGeometry,
    WMSConfig,
    WMTSConfig,
    XYZConfig,
    XYZService,
    detect_service_type,
    get_service,
    register_service,
)
from .service.wcs import WCSParser, WCSService
from .service.wms import WMSService
from .service.wmts import WMTSParser, WMTSService
from .types import (
    CRS,
    BBoxTuple,
    BoundingBox,
    CoverageDescription,
    Format,
    ServiceCapabilities,
    ServiceTypeEnum,
    SpatialExtent,
    TemporalExtent,
    TileRequest,
    TileResponse,
    WCSResponse,
)

__all__ = [
    "__version__",
    "__author__",
    "__email__",
    "compute_with_policy",
    "create_array",
    "load_array",
    "BaseService",
    "TileGeometry",
    "detect_service_type",
    "get_service",
    "register_service",
    "XYZConfig",
    "XYZService",
    "WMSConfig",
    "WMTSConfig",
    "WCSParser",
    "WCSService",
    "WMSService",
    "WMTSParser",
    "WMTSService",
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
