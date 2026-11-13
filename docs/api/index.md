# API reference

Public entry points for building lazy mosaics, configuring services, and handling failures. Documented against **0.3.1**. Prefer keyword **`bbox_crs=`** for the query CRS; mosaic output CRS is `attrs["mosaic_crs"]`. Prefer {py:func}`~tilearray.compute_mosaic` so Dask workers match the fetch ceiling. CRS roles: {doc}`../user-terms` · {doc}`../design-principles`. Pipeline: {doc}`../how-it-works`. Host ceilings: {doc}`../polite-fetch`.

## Array

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   create_array
   load_array
   compute_mosaic
   preview_array
   recommend_grid_shape
   choose_matrix_for_resolution
   MatrixChoice
   MatrixLevelSummary
   fetch_summary
   ArrayPreview
   FetchSummary
   array.compute_thread_pool_size
```

`create_array` builds a lazy Dask-backed `xarray.DataArray`. {py:func}`~tilearray.load_array` is eager sugar (`create_array(..., compute=True)`). Pass a service **config** (or URL where supported) as the first argument.

Before a large fetch, {py:func}`~tilearray.preview_array` returns a read-only {py:class}`~tilearray.ArrayPreview` (request count, planned bbox, `bbox_crs` / `mosaic_crs`, `matrix_id`, concurrency ceiling, cache warm/cold). {py:func}`~tilearray.recommend_grid_shape` sizes `(rows, cols)` from bbox × chunk × resolution. WMTS happy path: {py:func}`~tilearray.inspect_endpoint` → `build_config(..., target_resolution=...)`. After preview or compute, {py:func}`~tilearray.fetch_summary` reports progress / pressure counters.

**Query CRS:** `bbox_crs=` on `create_array` / `preview_array` / `load_array`. `attrs["bbox_crs"]` records the query; `attrs["mosaic_crs"]` is the mosaic CRS (WCS/WMS match the query; XYZ/slippy → `EPSG:3857`; metadata WMTS → matrix SupportedCRS). No client-side raster warp. Soft-fail / progress / `band_policy` / `cache_dir` are named parameters. ``**service_options`` key families: {doc}`../polite-fetch`.

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
   MatrixLevelSummary
```

URL entry point: {py:func}`~tilearray.inspect_endpoint`. {py:func}`~tilearray.inspect_capabilities` parses a GetCapabilities body you already have. How-to: {doc}`../inspect-endpoint`.

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

Import from `tilearray.fetch_presets` (not re-exported on the package root).

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

{py:exc}`NetworkError` means a tile failed after retries — mosaics do not leave silent NaN holes **by default**. Prefer catching {py:exc}`TileArrayError` (or a subclass) at public boundaries.

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

Pass plan-time ``on_tile_error=`` (or ``soft_fail=True``) into {py:func}`~tilearray.create_array` / {py:func}`~tilearray.load_array`. Soft-fail phases: {doc}`../polite-fetch`.

## Fetch

```{eval-rst}
.. currentmodule:: tilearray

.. autosummary::
   :toctree: generated
   :nosignatures:

   FetchPolicy
```

`FetchPolicy` sets concurrency ceilings, retries, timeouts, rate limits, and optional AIMD / circuit-breaker behaviour for polite tile HTTP. Connect vs idle-read use `httpx.Timeout(connect=, read=)`; `tile_deadline` is a separate wall-clock budget per tile attempt (idle-read alone is not a whole-response deadline). Service configs expose the same knobs (`fetch_timeout`, `fetch_connect_timeout`, `fetch_tile_deadline`) and build a policy via `.fetch_policy()`.

`FetchPolicy.max_concurrent` is the **shared process-local gate** ceiling (threads in one process share it; separate OS processes / distributed workers do not). Per-call politeness is `call_max_concurrent` (or a lower `max_concurrent_requests` override on `create_array`). `compute_mosaic(..., num_workers=)` sizes Dask **thread** workers only — see {doc}`../polite-fetch`.

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

Concrete service clients and parsers are **not** part of the advertised public surface — prefer configs + `create_array`. Arrays from `create_array` are north-up (row 0 = north). Mosaic attrs include `mosaic_crs` / `bbox_crs`, Affine `transform` (`transform_source="mosaic_bbox"`), `band_policy` (default `preserve`), optional `palette_mode` / `dtype_policy`, and elevation `nodata`/`_FillValue` when the format is GeoTIFF.
