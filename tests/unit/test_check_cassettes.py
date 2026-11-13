"""Cassette integrity stamps catch hand-edited request URIs (#177)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "check_cassettes.py"


def _load_check_cassettes():
    spec = importlib.util.spec_from_file_location("check_cassettes", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_check_integrity_fails_when_uri_edited_without_rerecord(
    tmp_path: Path,
) -> None:
    cc = _load_check_cassettes()
    cassette = {
        "interactions": [
            {
                "request": {"uri": "https://example.com/wcs?request=GetCoverage"},
                "response": {"body": {"string": "ok"}, "status": {"code": 200}},
            }
        ],
        "version": 1,
    }
    path = tmp_path / "sample.yaml"
    path.write_text(yaml.safe_dump(cassette, sort_keys=False), encoding="utf-8")
    cc.stamp_cassette(path)

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["interactions"][0]["request"]["uri"] += "&scaleSize=x(64),y(64)"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    error = cc.check_integrity(path)
    assert error is not None
    assert "request URI hash mismatch" in error
