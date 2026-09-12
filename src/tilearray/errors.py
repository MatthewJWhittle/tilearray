"""Custom exception hierarchy for TileArray."""

from typing import Optional


class TileArrayError(Exception):
    """Base exception for TileArray library."""

    def __init__(self, message: str, cause: Optional[Exception] = None):
        super().__init__(message)
        self.cause = cause


class ServiceError(TileArrayError):
    """Errors related to geospatial service interactions."""

    pass


class ValidationError(TileArrayError):
    """Data validation errors."""

    pass


class NetworkError(TileArrayError):
    """Network-related errors."""

    pass


class ParseError(TileArrayError):
    """Data parsing errors."""

    pass


class ConfigurationError(TileArrayError):
    """Configuration and setup errors."""

    pass
