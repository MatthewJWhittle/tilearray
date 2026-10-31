# API reference

Public entry points for building lazy mosaics, configuring services, and handling failures.

## Array

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   create_array
   load_array
   compute_with_policy
   array.compute_thread_pool_size
```

`create_array` builds a lazy Dask-backed `xarray.DataArray`. Prefer {py:func}`~tilearray.compute_with_policy` over plain `.compute()` on multi-tile mosaics so Dask workers match the fetch ceiling. Pass a service **config** (or URL where supported) as the first argument — not only a bare string for every service type.

## Configs

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   ServiceConfig
   WCSConfig
   WMSConfig
   WMTSConfig
   XYZConfig
```

Presets: `WCSConfig.for_ea_dsp`, `XYZConfig.for_openstreetmap`. Custom hosts: each config's `from_url`. `WCSConfig.build_service()` validates coverage via live DescribeCoverage (network on first `create_array` path that needs capabilities, not only at import time).

## Types

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   types.CRS
   types.Format
   types.BoundingBox
   types.TileRequest
   types.TileResponse
```

## Errors

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   TileArrayError
   ServiceError
   ValidationError
   NetworkError
   ParseError
   ConfigurationError
```

{py:exc}`NetworkError` means a tile failed after retries — mosaics do not leave silent NaN holes.

## Fetch

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   fetch.FetchPolicy
```

`FetchPolicy` controls concurrency, retries, timeouts, rate limits, and optional AIMD / circuit-breaker behaviour. Service configs expose the same knobs and build a policy via `.fetch_policy()`.

## Registry / services

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   get_service
   register_service
   detect_service_type
   WCSService
   WMSService
   WMTSService
   XYZService
```

Lower-level clients and parsers also live under `tilearray.service`. Arrays from `create_array` are north-up (row 0 = north).
