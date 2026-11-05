"""Choose a WMTS TileMatrix from a target resolution in CRS units/pixel (#123).

Pure helpers over matrix-level summaries — no HTTP, no silent pick inside
``create_array``, and no resampling. Callers pass the chosen matrix id into
``WMTSConfig`` / ``create_array`` themselves. Public comparison is on
**resolution** (CRS coordinate units per pixel — not “ground metres” on Web
Mercator). ScaleDenominator is best-effort / internal when present.
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
    """One TileMatrix level for resolution selection.

    Prefer setting ``resolution`` (CRS units per pixel). ``scale_denominator``
    is optional OGC metadata used only to derive resolution when needed.
    """

    identifier: str
    scale_denominator: float | None = None
    resolution: float | None = None
    """Native sample distance in CRS units per pixel, when known."""


@dataclass(frozen=True)
class MatrixChoice:
    """Result of :func:`choose_matrix_for_resolution`.

    ``scale_denominator`` is best-effort: ``None`` when the chosen level only
    had a precomputed ``resolution`` (no ScaleDenominator).
    """

    identifier: str
    scale_denominator: float | None
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

    ``target_resolution`` is sample distance in **CRS units per pixel**
    (linear units/pixel for projected matrices such as UTM metres; degrees/pixel
    for geographic; Web Mercator uses its own CRS units — not ground metres).
    Callers can pass a familiar ground spacing (for example ~10 m in a metre
    CRS) without knowing OGC ScaleDenominator.

    Policies:

    * ``nearest`` — smallest absolute difference to the target
    * ``coarser`` — finest matrix that is still at least as coarse as the target
      (``resolution >= target``); never oversamples
    * ``finer`` — coarsest matrix that is still at least as fine as the target
      (``resolution <= target``); never undersamples

    Levels are compared on ``resolution``. When ``resolution`` is missing but
    ``scale_denominator`` is present, resolution is derived (``crs=`` required).
    Levels that only have ``resolution`` (no scale) are accepted; levels with
    neither are skipped. Fails when no usable level remains, when ``crs`` is
    required to convert scales, or when no matrix satisfies the policy. Does
    **not** choose a layer, CRS, or TileMatrixSet.
    """

    if target_resolution <= 0:
        raise ValidationError("target_resolution must be positive")
    if not levels:
        raise ValidationError(
            "No TileMatrix levels provided. Pass matrix levels from "
            "inspect_endpoint / GetCapabilities (with resolution or "
            "ScaleDenominator)."
        )
    if policy not in {"coarser", "nearest", "finer"}:
        raise ValidationError(
            f"Unknown resolution policy {policy!r}; use 'coarser', 'nearest', or 'finer'."
        )

    parsed_crs = CRS.parse(crs) if crs is not None else None
    resolved: list[tuple[str, float | None, float]] = []
    skipped: list[str] = []
    for level in levels:
        identifier = str(level.identifier)
        scale = level.scale_denominator
        native = level.resolution
        if native is None:
            if scale is None:
                skipped.append(identifier)
                continue
            if parsed_crs is None:
                raise ValidationError(
                    "crs= is required to convert ScaleDenominator to CRS units "
                    "per pixel when MatrixLevelSummary.resolution is not set."
                )
            native = pixel_size_from_scale_denominator(float(scale), parsed_crs)
        scale_value = float(scale) if scale is not None else None
        resolved.append((identifier, scale_value, float(native)))

    if not resolved:
        detail = (
            f" Skipped levels with neither resolution nor ScaleDenominator: "
            f"{', '.join(skipped)}."
            if skipped
            else ""
        )
        raise ValidationError(
            "No TileMatrix levels with usable resolution (CRS units/pixel)."
            + detail
        )

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

    return MatrixChoice(
        identifier=identifier,
        scale_denominator=scale,
        resolution=native,
        policy=policy,
    )
