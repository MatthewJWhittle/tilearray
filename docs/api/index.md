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
   plan_array
   recommend_grid_shape
   fetch_summary
   ArrayPlan
   FetchSummary
   array.compute_thread_pool_size
```

`create_array` builds a lazy Dask-backed `xarray.DataArray`. Prefer {py:func}`~tilearray.compute_with_policy` over plain `.compute()` so Dask workers match the fetch ceiling. {py:func}`~tilearray.load_array` is eager sugar (`create_array(..., compute=True)` by default) — useful for tiny windows, not a second primary habit. Pass a service **config** (or URL where supported) as the first argument — not only a bare string for every service type.

Before a large fetch, {py:func}`~tilearray.plan_array` returns a read-only {py:class}`~tilearray.ArrayPlan` (request count, planned bbox, concurrency ceiling, cache warm/cold, honest unknowns). {py:func}`~tilearray.recommend_grid_shape` is the pure AOI helper for resolution × chunk sizing. After planning or compute, {py:func}`~tilearray.fetch_summary` reports progress / pressure counters without inventing wall time or cost.

**`crs=` today:** one argument interprets the bbox and labels the mosaic (`attrs["crs"]`). Accepts presets (`CRS.EPSG_27700`), EPSG strings / integers (`"EPSG:25833"`, `25833`), or a `pyproj.CRS` — validated via pyproj; typos like `EPGS:…` fail early. There is no client-side raster warp onto another CRS. Separate query / service / output CRS roles are **target** design — see {doc}`../design-principles`.

Multi-tile mosaics attach a default tqdm-style progress bar through `on_progress` during compute. Opt out with `show_progress=False` or `TILEARRAY_PROGRESS=0`; pass a custom `on_progress=` to replace the bar. Details: {doc}`../how-it-works` · {doc}`../troubleshooting`.

`**service_options` on `create_array` / `load_array` is one bag with three key families (**fetch** politeness, **planner** geometry, **identity** / protocol). Concurrency name map: {doc}`../how-it-works`.

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

Configs stay protocol-shaped (`from_url`, …). Host politeness lives in `tilearray.fetch_presets` — compose rather than a second config habit:

```python
from tilearray import WCSConfig, XYZConfig
from tilearray.fetch_presets import (
    OSM_TILE_TEMPLATE,
    ea_dsp_fetch_defaults,
    osm_fetch_defaults,
)

WCSConfig.from_url(url, coverage_id, **ea_dsp_fetch_defaults())
XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=16, **osm_fetch_defaults())
```

Custom hosts: `from_url` alone (no preset unpack). `WCSConfig.build_service()` validates coverage via live DescribeCoverage (network on first `create_array` path that needs capabilities, not only at import time).

## Inspect endpoint

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   inspect_endpoint
   inspect_capabilities
   EndpointInspection
   ResourceSummary
   MatrixSetSummary
```

Unknown WMS / WMTS / WCS URL → capability summary → validated config. How-to: {doc}`../inspect-endpoint`. Does not invent a second `create_array` path or silently guess layer / coverage / matrix / CRS / format.

## Fetch presets

```{eval-rst}
.. currentmodule:: tilearray.fetch_presets

.. autosummary::
   :toctree: generated
   :nosignatures:

   ea_dsp_fetch_defaults
   osm_fetch_defaults
   tilearray_user_agent
   OSM_TILE_TEMPLATE
   EA_LIDAR_WCS_URL
   EA_LIDAR_COVERAGE_ID
```

Import from `tilearray.fetch_presets` (not re-exported on the package root — keeps host folklore off the primary config surface). Constants such as `EA_LIDAR_BENCH_BBOX` are for tests/benches; prefer the helpers above for callers.

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

{py:exc}`NetworkError` means a tile failed after retries — mosaics do not leave silent NaN holes **by default**. Capabilities / DescribeCoverage HTTP failures on the WCS/WMTS httpx path also raise {py:exc}`NetworkError`.

Prefer catching {py:exc}`TileArrayError` (or a subclass) at public boundaries: bad inputs → {py:exc}`ValidationError`, XML/capabilities parse → {py:exc}`ParseError`, service detection / missing decoder → {py:exc}`ConfigurationError`. Do not rely on bare `ValueError` / `RuntimeError` from library entry points.

## Tile errors (opt-in soft-fail)

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   TileErrorAction
   TileErrorContext
   soft_fail_exhausted_tiles
```

Pass plan-time ``on_tile_error=`` (or ``soft_fail=True``) into {py:func}`~tilearray.create_array` / {py:func}`~tilearray.load_array`. The library preset {py:func}`~tilearray.soft_fail_exhausted_tiles` soft-fills exhausted tile-local fetch failures (404 / 5xx / transport) as NaN (or ``soft_fill_value``) blocks, keeps retries, hard-fails auth / exhausted 429 / decode errors, and aborts when soft-failed fraction exceeds ``soft_fail_max_fraction``. Default remains hard-fail + sibling abort.

## Fetch

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   FetchPolicy
```

`FetchPolicy` controls concurrency, retries, timeouts, rate limits, and optional AIMD / circuit-breaker behaviour. Service configs expose the same knobs and build a policy via `.fetch_policy()`. Advanced callers may also import it as `tilearray.fetch.FetchPolicy`.

`FetchPolicy.max_concurrent` is the **shared process-gate** ceiling. Per-call politeness is `call_max_concurrent` (or a lower `max_concurrent_requests` override on `create_array`). `compute_with_policy(..., max_concurrent=)` sizes Dask workers only — see {doc}`../how-it-works`.

## Registry

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   get_service
   register_service
   detect_service_type
```

Concrete service clients and parsers (`WCSService`, `WMSService`, `WMTSService`, `XYZService`, `BaseService`, `WCSParser`, `WMTSParser`) are **not** part of the advertised public surface — import them from `tilearray.service` / `tilearray.service.wcs` (etc.) only if you need low-level access. Prefer configs + `create_array`. Arrays from `create_array` are north-up (row 0 = north). Mosaic attrs include `crs`, Affine `transform` (`transform_source="mosaic_bbox"`), `band_policy` (default `preserve`), optional `palette_mode` / `dtype_policy`, and elevation `nodata`/`_FillValue` when the format is GeoTIFF — see {doc}`../how-it-works` for the rioxarray handoff, clipped WCS placement, and `expand_palette`. Product boundaries and CRS vocabulary: {doc}`../design-principles`.
