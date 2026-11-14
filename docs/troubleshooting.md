# Troubleshooting

Public tile hosts fail in familiar ways — 403/429 pressure, wrong CoverageIds, CRS attrs that look “off,” hung multi-tile computes. Use this page when a fetch or mosaic does not match what you expected.

Symptom → cause → action below. Pipeline: {doc}`how-it-works`. Host ceilings / soft-fail / scaling: {doc}`polite-fetch`. Endpoints: {doc}`example-sources`.

## Prefer `compute_mosaic`

Lazy `create_array` only fetches when you compute. Plain `.compute()` uses Dask's default thread pool (often ~CPU count), which can leave fetch headroom unused. Prefer:

```python
from tilearray import compute_mosaic

result = compute_mosaic(da)          # or compute_mosaic(da.mean())
```

`create_array` stores fetch policy on `da.attrs`; `compute_mosaic` reads it automatically. Size Dask threads with `num_workers=` if needed — do not pass fetch-ceiling kwargs to compute.

## How big will this AOI be?

Before a large compute, preview the job without fetching mosaic pixels:

```python
from tilearray import preview_array, recommend_grid_shape, fetch_summary

preview = preview_array(
    config, bbox, bbox_crs=..., resolution=(1.0, 1.0), cache_dir="./tiles"
)
print(preview)  # requests, pixels, concurrency ceiling, cache warm/cold, unknowns
```

Tune `chunk_size` / `resolution` / `grid_shape` / `max_concurrent_requests` from that summary — you should not need protocol-specific tile maths. `recommend_grid_shape(bbox, chunk_size=…, resolution=…)` is the pure helper when you only need `(rows, cols)`. Band counts and byte estimates stay **unknown** for JPEG/PNG until a probe or `create_array`; GeoTIFF elevation format policy reports 1 band. After compute, `print(fetch_summary(da))` shows done/total, soft-fails, and AIMD / breaker counters when available.

## Large mosaic looks hung / want quiet CI

Multi-tile computes show a **tile progress bar by default** (tqdm-style via `on_progress`). If a large job still looks idle, confirm you are on `compute_mosaic` (or `compute=True`) so the graph is actually running.

| Goal | What to do |
|------|------------|
| Quiet CI / scripts | `create_array(..., show_progress=False)` or set env `TILEARRAY_PROGRESS=0` |
| Non-TTY / redirected stderr | Auto progress stays off unless you force `show_progress=True` or `TILEARRAY_PROGRESS=1` |
| Custom UI | Pass your own `on_progress(done, total, request, response)=` (replaces the default bar) |
| Force a bar | `show_progress=True` (even single-tile or non-TTY) |

Host ceiling / concurrency semantics are unchanged — progress only observes tile completions.

## Concurrency names and `**service_options`

| Symptom | Likely mix-up | What to do |
|---------|---------------|------------|
| One polite call serialises sibling mosaics | Shared gate shrunk (`FetchPolicy.max_concurrent=1` / raised-then-shared override) | Lower `max_concurrent_requests=` on `create_array` (call-local) or set `call_max_concurrent`; keep the preset host ceiling |
| Many sample sites produce one huge empty mosaic or flood the host | Union bbox, or raised ceiling “for 20 jobs” | One `create_array` per bbox; sequential (or small-batch) compute under the shared gate — {doc}`multi-aoi-ml-patches` |
| `compute_mosaic(..., num_workers=N)` changed HTTP in-flight | That kwarg sizes **Dask workers only** | Set fetch politeness at `create_array` / config; see the concurrency name map in {doc}`polite-fetch` |
| Headers / `resolution` / AIMD knobs “ignored” or applied oddly | Same `**service_options` bag feeds **fetch**, **planner**, and **identity** families | Match the key to its family in {doc}`polite-fetch` (fetch keys are popped into `FetchPolicy` first) |
| Many 403/429 after moving to “more workers” / processes / distributed | Process-local gate × N workers overwhelms the host | Stay on `compute_mosaic` (threads). Multi-process/distributed does **not** share one ceiling — see {doc}`polite-fetch`; divide ceilings by worker count only as a conservative approach |
| `compute_mosaic(..., scheduler="processes")` still threads | Helper **always** uses `scheduler="threads"` so the process gate can apply | Use threads on purpose; for processes, read the scaling section in {doc}`polite-fetch` first — there is no cross-process host gate |

Soft-fail (`on_tile_error` / `soft_fail`) is a named plan-time argument, not a `**service_options` key.

## CRS / attrs / rioxarray handoff

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| “Wrong” CRS vs GeoTIFF tags | Mosaic CRS comes from planning (`attrs["mosaic_crs"]`), not tags inside tiles | Query CRS is `attrs["bbox_crs"]` (`bbox_crs=`). For WCS / WMS mosaic `attrs["mosaic_crs"]` matches the query; for XYZ / slippy WMTS it is `EPSG:3857`; for metadata-backed WMTS it is the matrix SupportedCRS — see {doc}`user-terms` · {doc}`how-it-works` |
| `Invalid CRS` / `Did you mean 'EPSG:…'` | Typo or unknown authority (e.g. `EPGS:28992`) | Use a pyproj-valid EPSG string/integer or preset (`CRS.EPSG_27700`); provider codes such as `EPSG:25833` are fine |
| Expected native-grid CRS ≠ query CRS | **XYZ / slippy WMTS** label mosaics `EPSG:3857` over the tile-union extent; **WMTS with TileMatrixSet metadata** labels with the matrix SupportedCRS. Query bbox may differ (`bbox_crs=` / `bbox_requested`). WCS / WMS use the query CRS for bbox + mosaic label | Fixed grids plan in native CRS without warping downloaded pixels — prefer `bbox_crs=` — see {doc}`guides/wmts` / {doc}`design-principles` |
| `TileMatrix '8' was not found` but caps list `08` | Host uses zero-padded matrix ids | Pass `tile_matrix=8` (matched automatically) or the exact caps id `"08"` |
| Plot looks flipped | Expecting south-up rows | Arrays are **north-up** (row 0 = north); do not flip for display |
| Missing `nodata` on RGB | Imagery path omits elevation nodata | Expected for JPEG/PNG; elevation GeoTIFF sets `nodata`/`_FillValue` to NaN |
| Colour mosaic ≠ analysis values | Rendered imagery vs numerical coverage | XYZ/WMS are often pictures; WCS elevation carries numbers — see {doc}`design-principles` |
| Multiband GeoTIFF became 1 band | Older default reduced to first band | Default is now `preserve`; pass `band_policy="first_band"` only when you want a single band |
| Palette PNG is grayscale indices | Mode `P` kept as indices by default | Pass `expand_palette=True` for RGB, or use a truecolor source |
| Edge tile smaller / ValidationError on shape | WCS clipped the coverage | Georeferenced GeoTIFF responses are placed+padded with NaN; missing tags or a resolution/CRS mismatch still fail — see {doc}`how-it-works` |
| Edge NaNs look like soft-fail | Coverage pad vs fetch soft-fail | Outside-coverage pads do not increment `tilearray_tile_errors`; soft-fail only records exhausted tile fetches |
| WCS mosaic fails with `InvalidSubsetting` / HTTP 404 near the coverage edge | Planned tiles left the DescribeCoverage envelope | tilearray clips GetCoverage subsets to `spatial_extent` and pads wholly outside tiles with nodata (no HTTP). An AOI entirely outside the envelope raises `ValidationError` |
| Need rasterio/rioxarray write | Optional downstream | Use the rioxarray recipe in {doc}`how-it-works` (`write_crs` / `write_transform` / `write_nodata` from attrs) |

## Environment Agency WCS

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Empty / invalid coverage | CoverageId ≠ URL path slug | Use the GetCapabilities id (e.g. `UUID__Lidar_Composite_Elevation_DTM_1m`), or `WCSConfig.from_url` + `**ea_dsp_fetch_defaults()` with the id from {doc}`example-sources` |
| Many 403s / hard fail mid-mosaic | Burst above the EA ceiling | Keep AIMD ceiling at **10** (`ea_dsp_fetch_defaults`). Do not raise `max_concurrent_requests` for county-scale cold mosaics |
| Slow multi-tile mosaic | Dask workers << fetch ceiling | Use `compute_mosaic` |
| Native 1 m GetCoverage hangs / times out with `scaleSize` in the URL | EA advertises Scaling but does not implement it | tilearray omits `scaleSize` / `width` / `height` at native resolution; request `resolution=` matching the coverage GSD (1 m for this DTM) |

## OpenStreetMap XYZ

- Stay on `XYZConfig.from_url(OSM_TILE_TEMPLATE, …, **osm_fetch_defaults())` (identifiable User-Agent, max 2 in-flight, ~2 req/s).
- Palette PNGs decode as **single-band palette indices** by default (not RGB). Pass `expand_palette=True` for colour — a small London window at z16 is about `(1024, 512, 3)`, or `(1024, 512)` without expansion.
- Attribute © OpenStreetMap contributors when displaying imagery.

## NASA GIBS XYZ

- Template order is `{z}/{y}/{x}` (row before column), unlike OSM `{z}/{x}/{y}`.
- Wrong order yields empty or scrambled mosaics — copy the template from {doc}`example-sources` / {doc}`guides/gibs-bluemarble`.

## WMS (EA Vegetation Object Model)

- Layer names are year-suffixed (`Vegetation_Object_Model_2022`, …). Confirm the year with {py:func}`~tilearray.inspect_endpoint` (or GetCapabilities) before hard-coding.
- Transparent PNG GetMap responses decode to **RGBA (4 bands)**.

## Failures are loud (by default)

After retries, a failed tile raises {py:exc}`tilearray.NetworkError`. Mosaics do **not** succeed with silent NaN holes; sibling in-flight tile fetches abort when the session fails hard. Capabilities / DescribeCoverage plan-time GETs use the same transient-retry budget (`fetch_retries`) for transport errors and 408 / 429 / 5xx; client errors (400 / 401 / 403 / 404) still fail on the first response. Exhausted caps / DescribeCoverage failures raise {py:exc}`tilearray.NetworkError` naming the attempt count.

Bad inputs and setup mistakes raise other {py:exc}`tilearray.TileArrayError` subclasses (`ValidationError`, `ParseError`, `ConfigurationError`) rather than bare `ValueError` / `RuntimeError`. Catch the hierarchy (or a subclass), not bare builtins, when handling library failures.

Opt in to visible soft-fill with plan-time ``on_tile_error=soft_fail_exhausted_tiles`` (or ``soft_fail=True``). Soft-fill runs **only after** fetcher retries finish, never calls ``FetchProgress.abort``, and records failures in ``progress.errors`` / ``attrs["tilearray_tile_errors"]``. Auth (401), exhausted 429, and decode/library errors still hard-abort under the preset; a soft-fail fraction above ``soft_fail_max_fraction`` (default ``0.1``) also aborts.

| Phase | When | Soft-fail? |
|-------|------|------------|
| Pre-graph band probe | `create_array` discovers band count for `preserve` formats | **Hard** — probe ignores soft-fail; pass `band_policy="first_band"` to skip the probe |
| Post-graph tile load | Mosaic tiles after the Dask graph exists | Soft-fill exhausted tile-local 404 / 5xx / transport |
| Auth / rate-limit | HTTP 401 or exhausted 429 | **Hard abort** (siblings stop) |

## Format / URL / coverage mistakes

These fail **before** a useful mosaic exists. Exception text names what was wrong, what was tried, and what to check next.

| Symptom (exception text / class) | Likely cause | What to do |
|----------------------------------|--------------|------------|
| `Unsupported WCS/WMS/WMTS format` ({py:exc}`~tilearray.ValidationError`) | Typo or non-enum MIME | Use `Format.GEOTIFF` / `Format.PNG` / `Format.JPEG` (or their MIME strings); when DescribeCoverage already ran, the message also lists server formats |
| `Failed to validate coverage` + `ConnectError` / Name or service not known | Bad host / DNS / VPN | Fix `base_url` spelling — **not** `coverage_id` |
| `…` + HTTP 401/403 or HTML / login portal body | Auth or portal URL, not WCS XML | Pass credentials via config `params=` / `headers=` (see private hosts below); do **not** soft-fail 401 |
| `Coverage '…' is not available` / no coverage description | Wrong or misspelled CoverageId | Use {py:func}`~tilearray.inspect_endpoint` (or `service.get_capabilities()`) and pick a listed CoverageId; for EA DSP, id looks like `UUID__…` and ≠ the URL path slug |
| `Unknown coverage` / `Unknown layer` from `build_config` | Identifier not in GetCapabilities | Print the inspection summary and pass an exact listed id — no fuzzy rename |
| `Unable to detect service type` | Ambiguous URL | Pass `service_type=` to `inspect_endpoint`, or a typed config / `{z}/{x}/{y}` for XYZ |
| Tile `NetworkError` after plan succeeded | Host reject / politeness / transient | Keep preset ceilings; prefer `compute_mosaic`; HTML tile bodies usually mean auth/portal URL |

## Disk cache

Pass `cache_dir=` to `create_array` / `load_array` (and the same path to `preview_array` when preflighting) to reuse tile bytes across runs. Each tile is `{sha256}.tile` + `{sha256}.meta` keyed by URL/params/format/bbox/size. Warm reads sniff the body when response headers lie (e.g. multipart WCS). Warm hits skip the network for that tile but cold siblings still share the host concurrency gate. Clear the directory if you change decode expectations, auth params, or suspect corrupt cache entries. `preview_array(...).summary()` reports warm/cold counts for the planned request set before you spend a cold compute.

## Private / keyed hosts

Every `*Config` already accepts `params=` (extra query) and `headers=` (extra HTTP headers) — same path as public hosts. Put credentials there at plan time; there is no separate auth client.

| Need | Put it on |
|------|-----------|
| API key / token query (`key=`, `api_key=`, …) | `params={"key": …}` |
| Legacy username + password as query params | `params={"username": …, "password": …}` (vendor names vary) |
| Bearer / API-key header / HTTP Basic | `headers={"Authorization": "Bearer …"}` (or `X-API-Key`, `Basic …`) |

Prefer `params=` / `headers=` over baking secrets into XYZ URL templates (`?key=…` or `{key}` placeholders). Load values from the environment or a secret store; never commit keys. Soft-fail does **not** paper over bad credentials — auth **401** still hard-aborts (see above).

Config `params=` / `headers=` also reach WCS/WMTS **GetCapabilities** / **DescribeCoverage** (same shared httpx policy as tiles) so private WCS can plan without a second credential path. Keyed XYZ recipe: {doc}`example-sources`.

## Custom endpoints

Start from {doc}`inspect-endpoint` when you do not know layer / CoverageId / tile matrix values yet. Then use `WCSConfig.from_url` / `WMSConfig.from_url` / `WMTSConfig.from_url` / `XYZConfig.from_url` (or `inspection.build_config(...)`) and tune {py:class}`~tilearray.FetchPolicy` / config knobs (`max_concurrent_requests`, `rate_limit_per_second`, AIMD fields). Pass credentials via `params=` / `headers=` (see above). Presets exist only for known host quirks (EA DSP WCS, OSMF XYZ) — they are not a catalogue of every public layer.
