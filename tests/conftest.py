"""
Shared test configuration, fixtures, and markers for tilearray tests.
"""

import pytest
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock
import httpx
import respx
from pytest_httpserver import HTTPServer
import vcr


def pytest_configure(config):
    """Configure test markers."""
    config.addinivalue_line("markers", "slow: marks tests as slow (>1s)")
    config.addinivalue_line("markers", "net: marks tests requiring network")
    config.addinivalue_line("markers", "contract: marks VCR-backed tests")
    config.addinivalue_line("markers", "property: marks property-based tests")
    config.addinivalue_line("markers", "unit: marks unit tests (fast, pure logic)")
    config.addinivalue_line("markers", "integration: marks integration tests")


@pytest.fixture
def tmp_cache_dir():
    """Isolated cache directory for tests."""
    cache_dir = tempfile.mkdtemp()
    yield Path(cache_dir)
    shutil.rmtree(cache_dir)


@pytest.fixture
def fake_server():
    """Programmable server for testing redirects/errors/gzip."""
    with HTTPServer(host="127.0.0.1", port=0) as server:
        yield server


@pytest.fixture
def http_client():
    """Configured HTTP client with small timeouts."""
    return httpx.Client(timeout=5.0)


@pytest.fixture
def respx_mock():
    """Respx mock for HTTP requests."""
    with respx.mock() as mock:
        yield mock


@pytest.fixture
def tile_coords():
    """Hypothesis strategy for valid tile coordinates."""
    from hypothesis import strategies as st
    
    return st.tuples(
        st.integers(min_value=0, max_value=20),  # z
        st.integers(min_value=0, max_value=2**20-1),  # x
        st.integers(min_value=0, max_value=2**20-1)   # y
    )


@pytest.fixture
def performance_budget():
    """Enforce performance budgets."""
    return {
        "import_time": 0.2,  # < 200ms
        "tile_fetch": 0.05,  # < 50ms
    }


@pytest.fixture(scope="module")
def vcr_config():
    """Global cassette rules for VCR (pytest-vcr)."""
    return {
        "record_mode": "once",
        "match_on": ["method", "scheme", "host", "port", "path", "query"],
        "filter_headers": [
            "authorization",
            "x-api-key",
            "x-auth-token",
            "cookie",
            "set-cookie",
        ],
        "decode_compressed_response": True,
    }


# Import all test modules to ensure fixtures are available
import tilearray
from tilearray.types import BoundingBox, CRS, Format