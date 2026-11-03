"""Catalogue integrity for community acceptance scenarios (#97)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .catalog import SCENARIOS, owning_issue_index, scenario_by_id

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def test_nine_scenarios_tracked() -> None:
    ids = [s["id"] for s in SCENARIOS]
    assert ids == [f"S{i}" for i in range(1, 10)]


def test_each_scenario_has_fixture_dir_and_provenance() -> None:
    for scenario in SCENARIOS:
        fixture_dir = Path(__file__).resolve().parent / scenario["fixture_dir"]
        assert fixture_dir.is_dir(), scenario["id"]
        assert scenario["source_url"].startswith("http")
        assert scenario["source_date"]
        assert scenario["owning_issues"]
        assert scenario["baseline"] in {
            "pass",
            "fail",
            "unsupported",
            "user_config_error",
            "correct_rejection",
            "not_reproduced",
        }


def test_owning_issues_cross_link_scenarios() -> None:
    index = owning_issue_index()
    # Uniqueness-spine issues from #97 must point at scenarios.
    assert "S1" in index[92]
    assert "S1" in index[93]
    assert "S4" in index[95]
    assert "S8" in index[94]
    assert scenario_by_id("S6")["baseline"] == "user_config_error"


def test_readme_lists_all_scenario_ids() -> None:
    readme = (Path(__file__).resolve().parent / "README.md").read_text(encoding="utf-8")
    for scenario in SCENARIOS:
        assert scenario["id"] in readme
