"""Shared create/preview entry path from the public argument bag.

Owns the duplicated ``create_array`` / ``preview_array`` preamble: config vs URL,
``service_type`` checks, ``crs=`` rejection, ``output_crs`` merge, fetch-policy
resolution, ``ArrayRequest`` construction, service build, and tile planning.
Callers then diverge into mosaic assembly vs preview summary.

Helpers that already live in :mod:`tilearray._planning` stay there; this module
composes them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ._planning import ArrayRequest, resolve_fetch_policy
from .errors import ValidationError
from .fetch import FetchPolicy
from .service.base import BaseService
from .service.config import ServiceConfig as ServiceConfigModel
from .types import (
    CRS,
    BBoxTuple,
    BoundingBox,
    Format,
    ServiceTypeEnum,
    TileRequest,
)


@dataclass(frozen=True)
class ArrayEntry:
    """Validated create/preview entry: request, service, fetch policy, and tiles."""

    request: ArrayRequest
    service: BaseService
    fetch_policy: FetchPolicy
    tile_requests: tuple[TileRequest, ...]
    tile_options: Mapping[str, Any]
    request_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "tile_requests", tuple(self.tile_requests))
        object.__setattr__(self, "tile_options", dict(self.tile_options))


def from_public_args(
    service_url: str | ServiceConfigModel,
    bbox: BoundingBox | BBoxTuple,
    bbox_crs: CRS | str | int,
    *,
    service_type: ServiceTypeEnum | None = None,
    chunk_size: tuple[int, int] | None = None,
    grid_shape: tuple[int, int] | None = None,
    output_format: Format | None = None,
    cache_dir: str | Path | None = None,
    output_crs: CRS | str | int | None = None,
    materialize_tiles: bool = True,
    **service_options: Any,
) -> ArrayEntry:
    """Build the shared create/preview entry from the public argument bag.

    Returns ``(request, service, fetch_policy, tile_requests, tile_options)``
    bundled as :class:`ArrayEntry`. Does not decode tiles or build a mosaic.

    When ``materialize_tiles=False`` (preview), huge plans may return an empty
    ``tile_requests`` tuple with ``request_count`` set from an O(1) estimate.
    """

    service_config = (
        service_url if isinstance(service_url, ServiceConfigModel) else None
    )
    base_service_url = (
        service_config.base_url if service_config is not None else str(service_url)
    )

    if (
        service_config is not None
        and service_type is not None
        and service_type != service_config.service_type
    ):
        raise ValidationError(
            "Provided service_type does not match the ServiceConfig service_type"
        )

    options = dict(service_options)
    if "crs" in options:
        raise ValidationError(
            "create_array / preview_array no longer accepts crs=; pass bbox_crs= "
            "for the query CRS (service config crs= for request defaults is "
            "unchanged)."
        )
    if output_crs is not None:
        if "output_crs" in options:
            raise ValidationError(
                "pass output_crs= once (named argument or service option, not both)"
            )
        options["output_crs"] = output_crs

    fetch_policy = resolve_fetch_policy(service_config, options)
    request = ArrayRequest.from_inputs(
        service_url=base_service_url,
        service_config=service_config,
        bbox_input=bbox,
        bbox_crs_input=bbox_crs,
        chunk_size_input=chunk_size,
        grid_shape_input=grid_shape,
        output_format_input=output_format,
        cache_dir_input=cache_dir,
        service_options_input=options,
    )
    service = request.build_service(service_type)
    tile_requests, tile_options, request_count = request.plan_tile_requests(
        service,
        max_tile_requests=fetch_policy.max_tile_requests,
        allow_estimate_only=not materialize_tiles,
    )
    if not tile_requests and request_count == 0:
        raise ValidationError("Planning produced no tile requests for this AOI")

    return ArrayEntry(
        request=request,
        service=service,
        fetch_policy=fetch_policy,
        tile_requests=tuple(tile_requests),
        tile_options=tile_options,
        request_count=request_count,
    )


__all__ = [
    "ArrayEntry",
    "from_public_args",
]
