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

WCS, WMS, WMTS, and XYZ share one request-and-decode path. Host **presets** (`XYZConfig.for_openstreetmap`, `WCSConfig.for_ea_dsp`) set polite defaults for known public endpoints.

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

AIMD only helps if Dask can schedule enough tile loads — use `compute_with_policy` for multi-tile mosaics.

---

## 3 · Decode

```{image} _static/guides/how-it-works-03-decode.png
:alt: HTTP bytes through shared decode into float32 y,x band arrays
:width: 700px
```

Shared decode unwraps and reads JPEG/PNG/GeoTIFF (including multipart WCS) into `float32` arrays. JPEG/PNG keep bands `(y, x, band)` when present; GeoTIFF elevation uses the first band.

---

## 4 · Dask mosaic

```{image} _static/guides/how-it-works-04-mosaic.png
:alt: Delayed tile blocks assembling into a north-up xarray DataArray
:width: 740px
```

Each tile is a Dask delayed block. Blocks are stitched **north-up** into one `xarray.DataArray` with coordinates and attrs (`crs`, mosaic `transform`, `band_policy`, optional elevation `nodata`, service/fetch summaries, …). Shape is known when the graph is built; tile bytes load when something computes.

**Georef contract:** mosaic CRS is always the `create_array(..., crs=)` you passed — never silently replaced by per-tile GeoTIFF tags. `attrs["transform"]` is a north-up Affine `(a,b,c,d,e,f)` from the planned mosaic bbox + shape (`transform_source="mosaic_bbox"`). `y`/`x` are **pixel centers** consistent with that Affine (row 0 = north). Elevation GeoTIFF decode maps `GDAL_NODATA`/sentinels → NaN in pixels and sets `nodata`/`_FillValue` to NaN; RGB/RGBA imagery omits nodata and keeps `(y, x, band)`.

---

## 5 · Lazy · compute · cache

```{image} _static/guides/how-it-works-05-lazy-compute-cache.png
:alt: Three panels for lazy create_array, compute_with_policy, and optional disk cache
:width: 740px
```

| Path | What happens |
|------|----------------|
| `create_array(...)` (default) | **Lazy** Dask graph — coords/attrs ready; pixels not all in RAM. |
| `create_array(..., compute=True)` / `load_array(...)` | Build then compute immediately. |
| `da.compute()` | Works, but default Dask worker count can under-use a high fetch ceiling. |
| {py:func}`tilearray.compute_with_policy` | Preferred for multi-tile mosaics: thread workers aligned to the fetch ceiling. |
| `cache_dir=...` | Optional on-disk tile cache; warm hits skip the network. |

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

Next: {doc}`quickstart` or {doc}`guides/index`.
