"""Contract: advertised public surface stays minimal and honest."""

from __future__ import annotations

import tilearray

_REMOVED_FROM_PUBLIC = frozenset(
    {
        "BaseService",
        "WCSParser",
        "WCSService",
        "WMSService",
        "WMTSParser",
        "WMTSService",
        "XYZService",
    }
)

_REQUIRED_PUBLIC = frozenset(
    {
        "FetchPolicy",
        "compute_mosaic",
        "create_array",
        "load_array",
        "write_zarr",
        "inspect_endpoint",
        "inspect_capabilities",
        "EndpointInspection",
        "preview_array",
        "recommend_grid_shape",
        "fetch_summary",
        "ArrayPreview",
        "FetchSummary",
        "WCSConfig",
        "WMSConfig",
        "WMTSConfig",
        "XYZConfig",
        "ServiceConfig",
        "TileArrayError",
        "NetworkError",
        "ValidationError",
        "ParseError",
        "ConfigurationError",
        "ServiceError",
        "TileErrorAction",
        "TileErrorContext",
        "TileErrorHandler",
        "soft_fail_exhausted_tiles",
    }
)


def test_all_excludes_parsers_and_concrete_services() -> None:
    public = set(tilearray.__all__)
    leaked = _REMOVED_FROM_PUBLIC & public
    assert not leaked, f"accidental public exports: {sorted(leaked)}"


def test_all_includes_core_contract_and_fetch_policy() -> None:
    public = set(tilearray.__all__)
    missing = _REQUIRED_PUBLIC - public
    assert not missing, f"missing public exports: {sorted(missing)}"


def test_fetch_policy_importable_from_package_root() -> None:
    from tilearray import FetchPolicy
    from tilearray.fetch import FetchPolicy as FetchPolicyFromModule

    assert FetchPolicy is FetchPolicyFromModule
