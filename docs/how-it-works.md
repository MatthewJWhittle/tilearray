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

Adapters turn your bbox + chunk/grid settings into an ordered list of tile requests (XYZ/WMTS grids, WCS coverage subsets). A larger AOI means more requests — not a different client API. For non-slippy WMTS TileMatrixSets, `create_array` loads GetCapabilities automatically (or you can pass an explicit `matrix=`). Planning uses that set’s SupportedCRS and ScaleDenominator in CRS units; the mosaic extent and `attrs["crs"]` follow the native tile grid rather than a guessed Web Mercator layout. See {doc}`guides/wmts`.

### Inspect the plan before you fetch

For a large AOI, call {py:func}`~tilearray.plan_array` with the same config / bbox / crs (and optional `resolution=` / `chunk_size=` / `cache_dir=`) you would pass to `create_array`. It returns an {py:class}`~tilearray.ArrayPlan` — printable via `print(plan)` / `plan.summary()` — covering:

- requested vs planned (snapped) bbox and CRS
- `grid_shape`, chunk size, mosaic pixel dimensions, request count
- dtype; band count and rough in-memory bytes **when known** (GeoTIFF elevation policy = 1 band; JPEG/PNG stay unknown until a probe or `create_array`)
- fetch concurrency ceiling (`max_concurrent` / optional `call_max_concurrent`)
- cache directory warm/cold tile counts when `cache_dir=` is set

Estimates never invent transfer time, provider cost, or band counts. XYZ/WMTS notes call out **native zoom / matrix** selection — tilearray does not resample pixels to an arbitrary client resolution. WCS `resolution=` sizes GetCoverage requests (the server may resample); that is not a local warp.

Pure geometry helper when you only need the grid: {py:func}`~tilearray.recommend_grid_shape` (`bbox` + `chunk_size` + `resolution` → `(rows, cols)`). After compute (or mid-flight on a lazy array), {py:func}`~tilearray.fetch_summary` reports done/total, soft-fails, and fetcher pressure counters when available.

```python
from tilearray import plan_array, create_array, compute_with_policy, fetch_summary

plan = plan_array(config, bbox, crs, resolution=(1.0, 1.0), cache_dir="./tiles")
print(plan)                       # read-only preflight
da = create_array(config, bbox, crs, resolution=(1.0, 1.0), cache_dir="./tiles")
result = compute_with_policy(da)
print(fetch_summary(da))          # progress / pressure snapshot
```

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

### Concurrency names (short)

**Host ceiling ≠ call politeness ≠ Dask threads.** Preset / config `max_concurrent_requests` is the host ceiling; a lower override on `create_array` is call-local; `compute_with_policy(..., max_concurrent=)` sizes Dask workers only. Do not shrink `FetchPolicy.max_concurrent` to “be polite” — that lowers the shared process gate for sibling sessions. Full name map: {doc}`api/index`.

Stay at or below preset ceilings on multi-tile cold mosaics. Soft-fill is plan-time opt-in; the default remains hard `NetworkError` + sibling abort after retries.

### Scaling up without overwhelming the host

Avoid flooding a server when you move from a laptop to a larger machine. The fetch gate (fixed semaphore or AIMD) is **process-local**. Threads in one Python process share it; separate OS processes and Dask distributed workers do **not**. Using more workers the wrong way multiplies in-flight HTTP by worker count even when each worker looks polite.

| Scale shape | What the gate does | Do this |
|-------------|--------------------|---------|
| **One process, Dask threads** | Shared ceiling holds for sibling mosaics | Prefer {py:func}`~tilearray.compute_with_policy` (always uses `scheduler="threads"`). Keep preset host ceilings; lower per-call with `max_concurrent_requests=` / `call_max_concurrent`. |
| **Several mosaics, same process** | Aggregate in-flight ≤ host ceiling | Same configs / presets; do not shrink `FetchPolicy.max_concurrent` for one polite call. |
| **Multi-process or distributed workers** | Each process has its own gate — **no global ceiling** | Do **not** advertise a cluster-wide host limit. Either stay on threads, or set each worker’s ceiling to about `floor(preset_ceiling / N_workers)` and treat that as a conservative approach, not a library guarantee. |

**What not to do**

- Do not use `da.compute(scheduler="processes")` or a Dask distributed cluster expecting the AIMD / semaphore gate to protect the host — those workers do not share one gate.
- Do not raise `max_concurrent_requests` past the preset to “use the bigger machine”; more CPU does not raise the provider’s ceiling.
- Do not launch N independent Python jobs against the same host each at the full preset ceiling.
- Do not expect tilearray to coordinate politeness across machines — there is no second client or distributed fetch coordinator.

`Retry-After`, AIMD pressure backoff, circuit breakers, and hard-fail / soft-fail abort still apply **inside** each process. Concurrent mosaics in one process share that behaviour; across processes you only get N independent copies.

Same public API on a larger box: `create_array` then `compute_with_policy`. Bigger machines help when the bottleneck is CPU/RAM for decode and assemble under the preset host ceiling — not by multiplying concurrent HTTP requests.

---

## 3 · Decode

```{image} _static/guides/how-it-works-03-decode.png
:alt: HTTP bytes through shared decode into float32 y,x band arrays
:width: 700px
```

Shared decode unwraps and reads JPEG/PNG/GeoTIFF (including multipart WCS). JPEG/PNG keep bands `(y, x, band)` when present; GeoTIFF keeps all bands by default (`band_policy="preserve"`; pass `"first_band"` to reduce). Elevation GeoTIFF pixels go through `tifffile` (no GDAL/`zarr` pin cage) and promote to float32 so nodata can be NaN.

---

## 4 · Dask mosaic

```{image} _static/guides/how-it-works-04-mosaic.png
:alt: Delayed tile blocks assembling into a north-up xarray DataArray
:width: 740px
```

Each tile is a Dask delayed block. Blocks are stitched **north-up** into one `xarray.DataArray` with coordinates and attrs (`crs`, mosaic `transform`, `band_policy`, optional elevation `nodata`, service/fetch summaries, …). Shape is known when the graph is built; tile bytes load when something computes.

**Georef contract (current alpha):** `attrs["transform"]` is a north-up Affine `(a,b,c,d,e,f)` from the **planned** mosaic bbox + shape (`transform_source="mosaic_bbox"`). `y`/`x` are **pixel centers** consistent with that Affine (row 0 = north). Per-tile GeoTIFF tags never silently replace mosaic CRS. Prefer `bbox_crs=` for the query CRS (`attrs["bbox_crs"]`); legacy `crs=` alone still works. For WCS / WMS, `attrs["crs"]` matches that query CRS. For **XYZ / slippy WMTS**, the mosaic is labelled `EPSG:3857` over the planned tile-union extent (`bbox_requested` keeps the query window). For **WMTS with TileMatrixSet metadata**, the mosaic is labelled with the matrix SupportedCRS and a tile-aligned extent — the query bbox may be in another CRS (see §1 and {doc}`guides/wmts`). Elevation GeoTIFF decode maps `GDAL_NODATA`/sentinels → NaN in pixels and sets `nodata`/`_FillValue` to NaN; RGB/RGBA imagery omits nodata and keeps `(y, x, band)`.

**Clipped WCS responses:** when a GeoTIFF comes back smaller than the requested tile (common at coverage edges), tilearray reads `ModelPixelScale` / `ModelTiepoint` and places those pixels on the requested grid, padding the rest with NaN. Matching resolution and integer offsets are required; mismatched CRS/resolution/rotation fails loudly instead of stretching. That pad is **coverage nodata**, not soft-fail fill of a failed fetch.

**Bands / dtype / palette:** default `band_policy` is `preserve` (all bands kept; pass `band_policy="first_band"` to reduce explicitly). GeoTIFF decode uses float32 so nodata can be NaN (`attrs["dtype_policy"]="float32_nodata_nan"`). PNG/JPEG keep native dtype until the `create_array(..., dtype=)` cast. Palette PNGs stay as index arrays unless `expand_palette=True` (then RGB; `attrs["palette_mode"]` records which).

### CRS roles — query vs service vs output

Three CRS roles matter ({doc}`design-principles`):

| Role | Meaning |
|------|---------|
| **Query / bbox CRS** | How you describe the AOI |
| **Service / request CRS** | CRS used when talking to the provider |
| **Output / array CRS** | Actual CRS of returned pixels + transform |

Prefer `bbox_crs=` for the query CRS; legacy `crs=` alone keeps today's query behaviour during alpha (passing both with different values fails). Any pyproj-valid CRS works (presets such as `CRS.EPSG_27700`, or strings like `"EPSG:25833"` / `"EPSG:28992"`); invalid identifiers raise {py:exc}`~tilearray.ValidationError` before planning. For WCS / WMS the query CRS also labels the mosaic (`attrs["crs"]`). For **XYZ / slippy WMTS**, pixels stay on the Web Mercator grid; `attrs["crs"]` is `EPSG:3857` and the planned extent is the tile union (`bbox_requested` records the caller's window). For **WMTS with TileMatrixSet metadata**, pixels stay on the native grid; `attrs["crs"]` and the planned extent follow the matrix SupportedCRS (query CRS may differ; `bbox_requested` records the caller's window). `attrs["bbox_crs"]` always records the query CRS. tilearray does **not** warp pixels client-side — a lon/lat query against a projected grid is planning plus native-grid assemble. See {doc}`design-principles`. Parameter reference: {py:func}`~tilearray.create_array`.

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

**Cache lifecycle:** each successful tile write stores `{sha256}.tile` plus a small `{sha256}.meta` sidecar under `cache_dir`. Keys hash URL, params, format, bbox, and width/height — change any of those and you miss. Warm hits skip HTTP but still decode; they do not bypass the shared host concurrency gate when a cold sibling is fetching. Clear the directory (or delete individual `.tile` files) after changing decode expectations, credentials, or when you suspect corruption. {py:func}`~tilearray.plan_array` reports how many planned tiles are already warm so you can size a cold run before `compute_with_policy`.

Memory tracks **what you compute** (full mosaic, a reduction, a window) — not automatically the whole AOI. Scale with bbox, chunks, and fetch policy. Use `plan_array` for a rough in-memory byte estimate when band count is known.

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
- **Feed ML** — sample chips from one mosaic, or many noncontiguous sites via a per-bbox loop ({doc}`multi-aoi-ml-patches`); do not union distant boxes into a single mosaic
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
