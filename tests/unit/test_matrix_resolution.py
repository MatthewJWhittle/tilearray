"""Ground resolution → TileMatrix selection (#123)."""

from __future__ import annotations

import pytest

from tilearray.errors import ValidationError
from tilearray.matrix_resolution import (
    MatrixLevelSummary,
    choose_matrix_for_resolution,
)
from tilearray.types import CRS

# Synthetic projected metres-per-pixel levels (scale is present; resolution set).
_LEVELS = (
    MatrixLevelSummary("fine", scale_denominator=1000.0, resolution=5.0),
    MatrixLevelSummary("mid", scale_denominator=2000.0, resolution=10.0),
    MatrixLevelSummary("coarse", scale_denominator=4000.0, resolution=20.0),
)


def test_choose_nearest_coarser_finer() -> None:
    nearest = choose_matrix_for_resolution(_LEVELS, 9.0, policy="nearest")
    assert nearest.identifier == "mid"
    assert nearest.resolution == 10.0

    coarser = choose_matrix_for_resolution(_LEVELS, 9.0, policy="coarser")
    assert coarser.identifier == "mid"  # finest among >= 9

    finer = choose_matrix_for_resolution(_LEVELS, 9.0, policy="finer")
    assert finer.identifier == "fine"  # coarsest among <= 9


def test_choose_fails_when_scale_missing() -> None:
    levels = (
        MatrixLevelSummary("ok", scale_denominator=1000.0, resolution=5.0),
        MatrixLevelSummary("bad", scale_denominator=None, resolution=10.0),
    )
    with pytest.raises(ValidationError, match="ScaleDenominator is missing"):
        choose_matrix_for_resolution(levels, 8.0, policy="nearest")


def test_choose_converts_scale_with_crs() -> None:
    # Web Mercator: metres/pixel = scale * 0.00028
    levels = (
        MatrixLevelSummary("z0", scale_denominator=559_082_264.0287178),
        MatrixLevelSummary("z1", scale_denominator=279_541_132.0143589),
    )
    choice = choose_matrix_for_resolution(
        levels, target_resolution=80_000.0, policy="nearest", crs=CRS.EPSG_3857
    )
    assert choice.identifier in {"z0", "z1"}
    assert choice.resolution > 0


def test_choose_coarser_impossible_raises() -> None:
    with pytest.raises(ValidationError, match="at least as coarse"):
        choose_matrix_for_resolution(_LEVELS, 50.0, policy="coarser")


def test_choose_empty_raises() -> None:
    with pytest.raises(ValidationError, match="No TileMatrix levels"):
        choose_matrix_for_resolution((), 10.0)
