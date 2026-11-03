"""Fixtures for community acceptance scenarios (#97)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURES_ROOT = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixtures_root() -> Path:
    return FIXTURES_ROOT


def load_json(relative: str) -> dict:
    path = FIXTURES_ROOT / relative
    return json.loads(path.read_text(encoding="utf-8"))
