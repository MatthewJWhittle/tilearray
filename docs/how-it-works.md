# How it works

tilearray turns **tiled** WCS / WMS / WMTS / XYZ sources into analysis-ready [xarray](https://docs.xarray.dev/) arrays. You pass a bounding box and a service config; the library plans the tile grid, fetches under a concurrency ceiling, decodes, and assembles a mosaic.

```{image} _static/guides/how-it-works-pipeline.png
:alt: Overview strip of plan, fetch, decode, mosaic, then lazy/compute use
:width: 740px
```

```python
from tilearray import create_array, compute_mosaic

da = create_array(config, bbox, bbox_crs=...)   # lazy Dask-backed DataArray by default
result = compute_mosaic(da)                     # fetch + assemble when you need values
```

WCS, WMS, WMTS, and XYZ share one request-and-decode path. Host **presets** in `tilearray.fetch_presets` (`osm_fetch_defaults`, `ea_dsp_fetch_defaults`) set polite defaults for known public endpoints — compose with `XYZConfig.from_url` / `WCSConfig.from_url`. Tuning ceilings, soft-fail, and multi-process scaling: {doc}`polite-fetch`.

---

## 1 · Plan tiles

```{image} _static/guides/how-it-works-01-plan.png
:alt: AOI over a tile grid producing an ordered TileRequest list
:width: 720px
```

Adapters turn your bbox + chunk/grid settings into an ordered list of tile requests (XYZ/WMTS grids, WCS coverage subsets). A larger AOI means more requests — not a different client API. For non-slippy WMTS TileMatrixSets, `create_array` loads GetCapabilities automatically (or you can pass an explicit `matrix=`). Planning uses that set’s SupportedCRS; the mosaic extent and `attrs["mosaic_crs"]` follow the native tile grid. See {doc}`guides/wmts`.

**Preview before you fetch.** {py:func}`~tilearray.preview_array` takes the same config / bbox / `bbox_crs=` (and optional `resolution=` / `chunk_size=` / `cache_dir=`) as `create_array` and returns a printable {py:class}`~tilearray.ArrayPreview`: planned bbox, grid, request count, concurrency ceiling, cache warm/cold. Pure grid helper: {py:func}`~tilearray.recommend_grid_shape`. After compute: {py:func}`~tilearray.fetch_summary`.

```python
from tilearray import preview_array, create_array, compute_mosaic, fetch_summary

preview = preview_array(
    config, bbox, bbox_crs=..., resolution=(1.0, 1.0), cache_dir="./tiles"
)
print(preview)
da = create_array(
    config, bbox, bbox_crs=..., resolution=(1.0, 1.0), cache_dir="./tiles"
)
result = compute_mosaic(da)
print(fetch_summary(da))
```

---

## 2 · Fetch

```{image} _static/guides/how-it-works-02-fetch.png
:alt: Dask thread pool feeding a fetch gate into a tile host via sync httpx
:width: 740px
```

Tiles fetch over **synchronous HTTP** under a process-local concurrency gate. Dask’s **thread** pool runs tile tasks; the gate caps in-flight HTTP. Prefer {py:func}`~tilearray.compute_mosaic` so workers match that ceiling. Defaults stay hard-fail after retries (no silent NaN holes). Host ceilings, AIMD, soft-fail phases, `**service_options`, and multi-process limits: {doc}`polite-fetch`.

---

## 3 · Decode

```{image} _static/guides/how-it-works-03-decode.png
:alt: HTTP bytes through shared decode into float32 y,x band arrays
:width: 700px
```

Shared decode unwraps and reads JPEG/PNG/GeoTIFF (including multipart WCS). JPEG/PNG keep bands `(y, x, band)` when present; GeoTIFF keeps all bands by default (`band_policy="preserve"`; pass `"first_band"` to reduce). Elevation GeoTIFF pixels promote to float32 so nodata can be NaN. PNG/JPEG mosaics default to **uint8** for plot-ready RGB; palette PNGs stay as indices unless `expand_palette=True`.

---

## 4 · Place and mosaic

```{image} _static/guides/how-it-works-04-mosaic.png
:alt: Delayed tile blocks assembling into a north-up xarray DataArray
:width: 740px
```

Each tile is a Dask delayed block. Blocks are stitched **north-up** into one `xarray.DataArray` with coordinates and attrs. Shape is known when the graph is built; tile bytes load when something computes.

**Georef contract:** `attrs["transform"]` is a north-up Affine from the **planned** mosaic bbox + shape. `y`/`x` are **pixel centres** (row 0 = north). `bbox_crs=` is the query CRS (`attrs["bbox_crs"]`). For WCS / WMS, `attrs["mosaic_crs"]` matches that query. For **XYZ / slippy WMTS**, the mosaic is labelled `EPSG:3857` over the tile-union extent. For **WMTS with TileMatrixSet metadata**, the mosaic follows the matrix SupportedCRS. No client-side warp — see {doc}`user-terms` and {doc}`design-principles`.

**Clipped WCS:** when a GeoTIFF comes back smaller than the requested tile, tilearray places those pixels on the requested grid and pads with NaN. Mismatched CRS/resolution/rotation fails loudly.

---

## 5 · Lazy · compute · cache

```{image} _static/guides/how-it-works-05-lazy-compute-cache.png
:alt: Three panels for lazy create_array, compute_mosaic, and optional disk cache
:width: 740px
```

| Path | What happens |
|------|----------------|
| `create_array(...)` (default) | **Lazy** Dask graph — coords/attrs ready; pixels not all in RAM. |
| {py:func}`tilearray.compute_mosaic` | **Preferred** compute path: thread workers aligned to the fetch ceiling. |
| `load_array(...)` | Eager sugar for `create_array(..., compute=True)`. Prefer lazy + `compute_mosaic` on primary paths. |
| `da.compute()` | Works, but default Dask worker count can under-use a high fetch ceiling. |
| `cache_dir=...` | Optional on-disk tile cache; warm hits skip the network. |

Multi-tile mosaics show a tqdm-style progress bar by default (`show_progress=False` or `TILEARRAY_PROGRESS=0` to quiet). Memory tracks **what you compute** — not automatically the whole AOI.

---

## 6 · Downstream use

```{image} _static/guides/how-it-works-06-downstream.png
:alt: DataArray feeding plot, analyse, and ML consumers
:width: 720px
```

With a DataArray you can plot, analyse with xarray/Dask, feed ML chips ({doc}`multi-aoi-ml-patches`), or hand off to rioxarray:

```python
# pip install rioxarray
import rioxarray  # noqa: F401 — registers .rio
from affine import Affine

da.rio.write_crs(da.attrs["mosaic_crs"], inplace=True)
da.rio.write_transform(Affine(*da.attrs["transform"]), inplace=True)
if "nodata" in da.attrs:
    da.rio.write_nodata(da.attrs["nodata"], inplace=True)
```

Next: {doc}`quickstart`, {doc}`guides/index`, {doc}`polite-fetch`, or {doc}`design-principles`.
