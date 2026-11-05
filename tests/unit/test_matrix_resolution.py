"""Target resolution → TileMatrix selection (#123)."""

from __future__ import annotations

import pytest

from tilearray.errors import ValidationError
from tilearray.matrix_resolution import (
    MatrixLevelSummary,
    choose_matrix_for_resolution,
)
from tilearray.types import CRS

# Synthetic projected CRS-units/pixel levels (scale present; resolution set).
_LEVELS = (
    MatrixLevelSummary("fine", scale_denominator=1000.0, resolution=5.0),
    MatrixLevelSummary("mid", scale_denominator=2000.0, resolution=10.0),
    MatrixLevelSummary("coarse", scale_denominator=4000.0, resolution=20.0),
)


def test_choose_nearest_coarser_finer() -> None:
    nearest = choose_matrix_for_resolution(_LEVELS, 9.0, policy="nearest")
    assert nearest.identifier == "mid"
    assert nearest.resolution == 10.0
    assert nearest.scale_denominator == 2000.0

    coarser = choose_matrix_for_resolution(_LEVELS, 9.0, policy="coarser")
    assert coarser.identifier == "mid"  # finest among >= 9

    finer = choose_matrix_for_resolution(_LEVELS, 9.0, policy="finer")
    assert finer.identifier == "fine"  # coarsest among <= 9


def test_choose_accepts_resolution_only_levels() -> None:
    levels = (
        MatrixLevelSummary("ok", scale_denominator=1000.0, resolution=5.0),
        MatrixLevelSummary("res_only", scale_denominator=None, resolution=10.0),
    )
    choice = choose_matrix_for_resolution(levels, 9.0, policy="nearest")
    assert choice.identifier == "res_only"
    assert choice.resolution == 10.0
    assert choice.scale_denominator is None


def test_choose_skips_levels_with_neither_scale_nor_resolution() -> None:
    levels = (
        MatrixLevelSummary("empty", scale_denominator=None, resolution=None),
        MatrixLevelSummary("mid", scale_denominator=None, resolution=10.0),
    )
    choice = choose_matrix_for_resolution(levels, 9.0, policy="nearest")
    assert choice.identifier == "mid"
    assert choice.scale_denominator is None


def test_choose_fails_when_no_usable_resolution() -> None:
    levels = (
        MatrixLevelSummary("a", scale_denominator=None, resolution=None),
        MatrixLevelSummary("b", scale_denominator=None, resolution=None),
    )
    with pytest.raises(ValidationError, match="usable resolution"):
        choose_matrix_for_resolution(levels, 8.0, policy="nearest")


def test_choose_converts_scale_with_crs() -> None:
    # Web Mercator: CRS units/pixel = scale * 0.00028
    levels = (
        MatrixLevelSummary("z0", scale_denominator=559_082_264.0287178),
        MatrixLevelSummary("z1", scale_denominator=279_541_132.0143589),
    )
    choice = choose_matrix_for_resolution(
        levels, target_resolution=80_000.0, policy="nearest", crs=CRS.EPSG_3857
    )
    assert choice.identifier in {"z0", "z1"}
    assert choice.resolution > 0
    assert choice.scale_denominator is not None


def test_choose_coarser_impossible_raises() -> None:
    with pytest.raises(ValidationError, match="at least as coarse"):
        choose_matrix_for_resolution(_LEVELS, 50.0, policy="coarser")


def test_choose_empty_raises() -> None:
    with pytest.raises(ValidationError, match="No TileMatrix levels"):
        choose_matrix_for_resolution((), 10.0)
