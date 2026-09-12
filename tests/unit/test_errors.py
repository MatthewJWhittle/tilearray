"""Unit tests for the TileArray exception hierarchy."""

from __future__ import annotations

import pytest

from tilearray.errors import (
    ConfigurationError,
    NetworkError,
    ParseError,
    ServiceError,
    TileArrayError,
    ValidationError,
)

pytestmark = pytest.mark.unit


def test_tile_array_error_stores_message_and_cause() -> None:
    cause = ValueError("root cause")
    error = TileArrayError("something failed", cause=cause)

    assert str(error) == "something failed"
    assert error.cause is cause


@pytest.mark.parametrize(
    "exc_cls",
    [ServiceError, ValidationError, NetworkError, ParseError, ConfigurationError],
)
def test_specialized_errors_inherit_from_tile_array_error(
    exc_cls: type[TileArrayError],
) -> None:
    error = exc_cls("boom")
    assert isinstance(error, TileArrayError)
    assert str(error) == "boom"
