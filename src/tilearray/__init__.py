"""TileArray - high-level helpers for loading OGC services into xarray."""

from ._version import __version__

__author__ = "Matthew Whittle"
__email__ = "47574804+MatthewJWhittle@users.noreply.github.com"

from .array import compute_with_policy, create_array, load_array
from .errors import (
    ConfigurationError,
    NetworkError,
    ParseError,
    ServiceError,
    TileArrayError,
    ValidationError,
)
from .fetch import FetchPolicy
from .service import (
    ServiceConfig,
    TileGeometry,
    WCSConfig,
    WMSConfig,
    WMTSConfig,
    XYZConfig,
    detect_service_type,
    get_service,
    register_service,
)
from .tile_errors import (
    TileErrorAction,
    TileErrorContext,
    TileErrorHandler,
    soft_fail_exhausted_tiles,
)
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
    "FetchPolicy",
    "TileArrayError",
    "ServiceError",
    "ValidationError",
    "NetworkError",
    "ParseError",
    "ConfigurationError",
    "TileErrorAction",
    "TileErrorContext",
    "TileErrorHandler",
    "soft_fail_exhausted_tiles",
    "TileGeometry",
    "detect_service_type",
    "get_service",
    "register_service",
    "ServiceConfig",
    "XYZConfig",
    "WCSConfig",
    "WMSConfig",
    "WMTSConfig",
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
