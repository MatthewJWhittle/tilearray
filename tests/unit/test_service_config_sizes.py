"""ServiceConfig size-field validation."""

from __future__ import annotations

import pytest

from tilearray.errors import ValidationError
from tilearray.service.config import WCSConfig
from tilearray.types import ServiceTypeEnum

pytestmark = pytest.mark.unit


def test_service_config_rejects_nonpositive_sizes() -> None:
    with pytest.raises((ValidationError, ValueError)):
        WCSConfig(
            base_url="https://example.com/wcs",
            service_type=ServiceTypeEnum.WCS,
            coverage_id="cov",
            chunk_size=(0, 256),
        )
    with pytest.raises((ValidationError, ValueError)):
        WCSConfig(
            base_url="https://example.com/wcs",
            service_type=ServiceTypeEnum.WCS,
            coverage_id="cov",
            grid_shape=(2, -1),
        )
