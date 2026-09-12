"""Offline regression checks for the fetch engine benchmark script."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "bench_fetch_engine.py"
RESULTS = ROOT / "benchmarks" / "fetch_engine_bench_results.txt"


def test_bench_script_runs_offline() -> None:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    output = completed.stdout
    assert "Legacy (unbounded)" in output
    assert (
        "TileFetcher + EA preset" in output or "TileFetcher + EA AIMD preset" in output
    )
    assert "TileFetcher + OSM preset" in output
    assert "Scenario D — realistic RTT mock" in output
    assert "max_inflight=2" in output or "max_inflight=1" in output
    assert RESULTS.is_file()
