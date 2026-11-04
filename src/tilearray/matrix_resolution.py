"""Choose a WMTS TileMatrix from a target ground resolution (#123).

Pure helpers over matrix-level summaries — no HTTP, no silent pick inside
``create_array``, and no resampling. Callers pass the chosen matrix id into
``WMTSConfig`` / ``create_array`` themselves.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from .errors import ValidationError
from .service.wmts import pixel_size_from_scale_denominator
from .types import CRS

ResolutionPolicy = Literal["coarser", "nearest", "finer"]

__all__ = [
    "MatrixChoice",
    "MatrixLevelSummary",
    "ResolutionPolicy",
    "choose_matrix_for_resolution",
]


@dataclass(frozen=True)
class MatrixLevelSummary:
    """One TileMatrix level with the scale needed for resolution selection."""

    identifier: str
    scale_denominator: float | None = None
    resolution: float | None = None
    """Native ground sample distance in CRS units per pixel, when known."""


@dataclass(frozen=True)
class MatrixChoice:
    """Result of :func:`choose_matrix_for_resolution`."""

    identifier: str
    scale_denominator: float
    resolution: float
    policy: ResolutionPolicy


def choose_matrix_for_resolution(
    levels: Sequence[MatrixLevelSummary],
    target_resolution: float,
    *,
    policy: ResolutionPolicy = "nearest",
    crs: CRS | str | int | None = None,
) -> MatrixChoice:
    """Pick a TileMatrix whose native resolution matches ``target_resolution``.

    ``target_resolution`` is ground sample distance in **CRS units per pixel**
    (metres/pixel for projected matrices; degrees/pixel for geographic).
    Policies:

    * ``nearest`` — smallest absolute difference to the target
    * ``coarser`` — finest matrix that is still at least as coarse as the target
      (``resolution >= target``); never oversamples
    * ``finer`` — coarsest matrix that is still at least as fine as the target
      (``resolution <= target``); never undersamples

    Fails when any level is missing a scale denominator (and no precomputed
    ``resolution``), when ``crs`` is required to convert scales, or when no
    matrix satisfies the policy. Does **not** choose a layer, CRS, or TileMatrixSet.
    """

    if target_resolution <= 0:
        raise ValidationError("target_resolution must be positive")
    if not levels:
        raise ValidationError(
            "No TileMatrix levels provided. Pass matrix levels from "
            "inspect_endpoint / GetCapabilities (with ScaleDenominator)."
        )
    if policy not in {"coarser", "nearest", "finer"}:
        raise ValidationError(
            f"Unknown resolution policy {policy!r}; use 'coarser', 'nearest', or 'finer'."
        )

    parsed_crs = CRS.parse(crs) if crs is not None else None
    resolved: list[tuple[str, float | None, float]] = []
    missing_scale: list[str] = []
    for level in levels:
        identifier = str(level.identifier)
        scale = level.scale_denominator
        native = level.resolution
        if scale is None:
            missing_scale.append(identifier)
            continue
        if native is None:
            if parsed_crs is None:
                raise ValidationError(
                    "crs= is required to convert ScaleDenominator to CRS units "
                    "per pixel when MatrixLevelSummary.resolution is not set."
                )
            native = pixel_size_from_scale_denominator(float(scale), parsed_crs)
        resolved.append((identifier, float(scale), float(native)))

    if missing_scale:
        raise ValidationError(
            "TileMatrix ScaleDenominator is missing for: "
            + ", ".join(missing_scale)
            + ". Cannot choose a matrix from ground resolution without scales."
        )
    if not resolved:
        raise ValidationError("No TileMatrix levels with usable scale/resolution")

    if policy == "nearest":
        identifier, scale, native = min(
            resolved, key=lambda item: (abs(item[2] - target_resolution), item[2])
        )
    elif policy == "coarser":
        candidates = [item for item in resolved if item[2] >= target_resolution]
        if not candidates:
            raise ValidationError(
                f"No TileMatrix is at least as coarse as target_resolution="
                f"{target_resolution} (CRS units / pixel). Closest finer native "
                f"resolution is {min(item[2] for item in resolved)}."
            )
        identifier, scale, native = min(candidates, key=lambda item: item[2])
    else:  # finer
        candidates = [item for item in resolved if item[2] <= target_resolution]
        if not candidates:
            raise ValidationError(
                f"No TileMatrix is at least as fine as target_resolution="
                f"{target_resolution} (CRS units / pixel). Closest coarser native "
                f"resolution is {max(item[2] for item in resolved)}."
            )
        identifier, scale, native = max(candidates, key=lambda item: item[2])

    assert scale is not None
    return MatrixChoice(
        identifier=identifier,
        scale_denominator=scale,
        resolution=native,
        policy=policy,
    )
