# How it works

tilearray turns **tiled** WCS / WMS / WMTS / XYZ sources into analysis-ready [xarray](https://docs.xarray.dev/) arrays — the same API on a laptop or a bigger machine.

You pass a bounding box and a service config. The library plans the tile grid, fetches under a concurrency ceiling, decodes, and assembles a mosaic.

```{image} _static/guides/how-it-works-pipeline.png
:alt: Overview strip of plan, fetch, decode, mosaic, then lazy/compute use
:width: 740px
```

```python
from tilearray import create_array, compute_with_policy

da = create_array(config, bbox, crs)   # lazy Dask-backed DataArray by default
result = compute_with_policy(da)       # fetch + assemble when you need values
```

WCS, WMS, WMTS, and XYZ share one request-and-decode path. Host **presets** in `tilearray.fetch_presets` (`osm_fetch_defaults`, `ea_dsp_fetch_defaults`) set polite defaults for known public endpoints — compose with `XYZConfig.from_url` / `WCSConfig.from_url`.

---

## 1 · Plan tiles

```{image} _static/guides/how-it-works-01-plan.png
:alt: AOI over a tile grid producing an ordered TileRequest list
:width: 720px
```

Adapters turn your bbox + chunk/grid settings into an ordered list of tile requests (XYZ/WMTS grids, WCS coverage subsets). A larger AOI means more requests — not a different client API.

---

## 2 · Polite concurrent fetch

```{image} _static/guides/how-it-works-02-fetch.png
:alt: Dask thread pool feeding a fetch gate into a tile host via sync httpx
:width: 740px
```

Fetching uses **synchronous HTTP** (`httpx.Client`) with a concurrency gate:

- Dask’s **thread** pool runs tile tasks; the gate caps in-flight HTTP (not asyncio).
- Fixed ceiling + optional rate limit when adaptive mode is off (OSM preset).
- **AIMD** when enabled: ramp under `max_concurrent`, back off on pressure (403/408/429/5xx and known OGC transient responses).
- Optional **circuit breaker** (EA DSP preset): a burst of pressure freezes concurrency at the floor for a cooldown.
- Retries with `Retry-After` support; hard failure raises `NetworkError` by default (no silent NaN holes). Opt-in `on_tile_error` / `soft_fail=True` can soft-fill exhausted tile-local failures after retries without aborting siblings.

WCS/WMTS **GetCapabilities** / **DescribeCoverage** (and direct `WCSService.get_coverage`) use the same sync httpx stack: library User-Agent by default, `ServiceConfig.fetch_timeout` (default 30s), and config `headers`/`params` so private-server credentials apply at plan time as well as on tiles.

AIMD only helps if Dask can schedule enough tile loads — use `compute_with_policy` for multi-tile mosaics.

### `**service_options` key families

`create_array(..., **service_options)` is one bag today. Keys fall into three families — know which family you are editing:

| Family | Keys (illustrative) | What consumes them |
|--------|---------------------|--------------------|
| **Fetch** | `max_concurrent_requests`, `max_connections`, `fetch_retries`, `fetch_timeout`, `rate_limit_per_second`, `rate_limiter`, AIMD / circuit-breaker fields (`adaptive_concurrency`, `initial_concurrent_requests`, …) | Popped into `FetchPolicy` before planning |
| **Planner** | `resolution`, `origin` | Snap / grid planning (`ArrayRequest`); leftovers join tile options |
| **Identity / protocol** | `headers`, `params`, coverage / layer / style / TMS / zoom… | Leftover options → service / `TileRequest` |

Soft-fail knobs (`on_tile_error`, `soft_fail`, `soft_fill_value`, `soft_fail_max_fraction`) are **named** parameters on `create_array` / `load_array`, not bag keys — same progressive-disclosure pattern as `on_progress` and `cache_dir`.

API surface: {doc}`api/index`. When a knob misbehaves: {doc}`troubleshooting`.

### `max_concurrent*` — one job per name

Same-ish names, different jobs. **Host ceiling ≠ call politeness ≠ Dask threads.**

| Name | Where | Job |
|------|-------|-----|
| `max_concurrent_requests` | `*Config` field **and** `create_array` override via `**service_options` | Config / preset **host ceiling** input. On a preset config, a **lower** override is call-local only; a **raise** lifts the shared ceiling. |
| `FetchPolicy.max_concurrent` | Policy / attrs `tilearray_fetch_policy` | **Shared process-gate** (part of the fetcher `cache_key`). |
| `call_max_concurrent` | `FetchPolicy` + `FetchProgress` | **Per-call / per-session** in-flight cap; **not** in `cache_key`. |
| `compute_with_policy(..., max_concurrent=)` | Compute helper | **Dask worker sizing only** (merges into attrs policy for `num_workers`). The HTTP gate was fixed at `create_array`. |

Do **not** treat `FetchPolicy(max_concurrent=1)` as the polite-call recipe — that shrinks the shared gate for sibling sessions. Prefer a lower `max_concurrent_requests=` override on `create_array` (or `call_max_concurrent` on the session) so the host ceiling stays intact.

Stay at or below preset ceilings on multi-tile cold mosaics. Soft-fill is plan-time opt-in; the default remains hard `NetworkError` + sibling abort after retries. Maintainer soak commands live in {doc}`building-docs`.

---

## 3 · Decode

```{image} _static/guides/how-it-works-03-decode.png
:alt: HTTP bytes through shared decode into float32 y,x band arrays
:width: 700px
```

Shared decode unwraps and reads JPEG/PNG/GeoTIFF (including multipart WCS) into `float32` arrays. JPEG/PNG keep bands `(y, x, band)` when present; GeoTIFF elevation uses the first band (pixels via `tifffile`, no GDAL/`zarr` pin cage).

---

## 4 · Dask mosaic

```{image} _static/guides/how-it-works-04-mosaic.png
:alt: Delayed tile blocks assembling into a north-up xarray DataArray
:width: 740px
```

Each tile is a Dask delayed block. Blocks are stitched **north-up** into one `xarray.DataArray` with coordinates and attrs (`crs`, mosaic `transform`, `band_policy`, optional elevation `nodata`, service/fetch summaries, …). Shape is known when the graph is built; tile bytes load when something computes.

**Georef contract (current alpha):** mosaic CRS is always the `create_array(..., crs=)` you passed — never silently replaced by per-tile GeoTIFF tags. `attrs["transform"]` is a north-up Affine `(a,b,c,d,e,f)` from the planned mosaic bbox + shape (`transform_source="mosaic_bbox"`). `y`/`x` are **pixel centers** consistent with that Affine (row 0 = north). Elevation GeoTIFF decode maps `GDAL_NODATA`/sentinels → NaN in pixels and sets `nodata`/`_FillValue` to NaN; RGB/RGBA imagery omits nodata and keeps `(y, x, band)`.

**Clipped WCS responses:** when a GeoTIFF comes back smaller than the requested tile (common at coverage edges), tilearray reads `ModelPixelScale` / `ModelTiepoint` and places those pixels on the requested grid, padding the rest with NaN. Matching resolution and integer offsets are required; mismatched CRS/resolution/rotation fails loudly instead of stretching. That pad is **coverage nodata**, not soft-fail fill of a failed fetch.

**Bands / dtype / palette:** default `band_policy` is `preserve` (all bands kept; pass `band_policy="first_band"` to reduce explicitly). GeoTIFF decode uses float32 so nodata can be NaN (`attrs["dtype_policy"]="float32_nodata_nan"`). PNG/JPEG keep native dtype until the `create_array(..., dtype=)` cast. Palette PNGs stay as index arrays unless `expand_palette=True` (then RGB; `attrs["palette_mode"]` records which).

### CRS roles — query vs service vs output

Product vocabulary (canonical: {doc}`design-principles`) separates three ideas:

| Role | Meaning |
|------|---------|
| **Query / bbox CRS** | How you describe the AOI |
| **Service / request CRS** | CRS used when talking to the provider |
| **Output / array CRS** | Actual CRS of returned pixels + transform |

**Today:** one `crs=` argument both interprets the bbox and labels the mosaic. Any pyproj-valid CRS works (presets such as `CRS.EPSG_27700`, or strings like `"EPSG:25833"` / `"EPSG:28992"`); invalid identifiers raise {py:exc}`~tilearray.ValidationError` before planning. tilearray does **not** run a client-side raster warp onto a different CRS; pixels stay on the planned mosaic grid. **Target design:** callers may use a convenient `bbox_crs` while fixed grids (WMTS/XYZ) return native-grid pixels in their true output CRS (with reported snapped extent). Server-side WMS/WCS CRS changes, if exposed, stay explicit and must match returned metadata. Do not treat separate CRS roles as already shipped — see {doc}`design-principles`.

**Imagery vs numerical coverages:** XYZ/WMS (and many WMTS) paths are often *rendered* tiles (JPEG/PNG, sometimes palette indices unless `expand_palette=True`). WCS elevation is a *numerical* coverage (GeoTIFF, nodata → NaN). Do not assume colour mosaics imply analysis-grade values, or elevation nodata rules for imagery.

---

## 5 · Lazy · compute · cache

```{image} _static/guides/how-it-works-05-lazy-compute-cache.png
:alt: Three panels for lazy create_array, compute_with_policy, and optional disk cache
:width: 740px
```

| Path | What happens |
|------|----------------|
| `create_array(...)` (default) | **Lazy** Dask graph — coords/attrs ready; pixels not all in RAM. |
| {py:func}`tilearray.compute_with_policy` | **Preferred** compute path: thread workers aligned to the fetch ceiling. |
| `load_array(...)` | Eager sugar for `create_array(..., compute=True)` — builds then computes immediately (same fetch-aligned workers). Prefer lazy + `compute_with_policy` on primary paths. |
| `da.compute()` | Works, but default Dask worker count can under-use a high fetch ceiling. |
| `cache_dir=...` | Optional on-disk tile cache; warm hits skip the network. |

**Tile progress:** multi-tile mosaics show a tqdm-style bar by default while tiles complete (same `on_progress` / `FetchProgress` path in scripts and notebooks). Quiet for CI / redirected scripts with `show_progress=False` or `TILEARRAY_PROGRESS=0`. Pass your own `on_progress=` to replace the default bar. Single-tile plans stay quiet unless you force progress.

Memory tracks **what you compute** (full mosaic, a reduction, a window) — not automatically the whole AOI. Scale with bbox, chunks, and fetch policy.

---

## 6 · Downstream use

```{image} _static/guides/how-it-works-06-downstream.png
:alt: DataArray feeding plot, analyse, and ML consumers
:width: 720px
```

```{image} _static/guides/ea-lidar-elevation.png
:alt: Example EA Lidar elevation mosaic and histogram after compute
:width: 640px
```

With a DataArray you can:

- **Plot** a computed window or a reduction (`da.plot()` / `da.isel(band=0).plot()`)
- **Analyse** with xarray/Dask (stats, terrain, resample)
- **Feed ML** — sample chips or materialise tensors from the same chunked array
- **Handoff to rioxarray** (optional install) using mosaic attrs — no silent CRS rewrite:

```python
# pip install rioxarray
import rioxarray  # noqa: F401 — registers .rio
from affine import Affine

da.rio.write_crs(da.attrs["crs"], inplace=True)
da.rio.write_transform(Affine(*da.attrs["transform"]), inplace=True)
if "nodata" in da.attrs:
    da.rio.write_nodata(da.attrs["nodata"], inplace=True)
```

Next: {doc}`quickstart`, {doc}`guides/index`, or {doc}`design-principles`.
