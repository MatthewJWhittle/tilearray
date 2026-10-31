# How it works

tilearray’s job is simple to say and fiddly to do well: turn **tiled** WCS / WMS / WMTS / XYZ sources into **analysis-ready** [xarray](https://docs.xarray.dev/) arrays — efficiently, politely, and with the **same API** on a laptop or a bigger box.

You bring a bounding box and a service config. The library plans the tile grid, fetches under a concurrency ceiling, decodes, and assembles a mosaic. Below is that pipeline as a visual walkthrough — claims match the real fetch engine and `create_array` API.

```{image} _static/guides/how-it-works-pipeline.png
:alt: Overview strip of plan, fetch, decode, mosaic, then lazy/compute use
:width: 740px
```

```python
from tilearray import create_array, compute_with_policy

da = create_array(config, bbox, crs)   # lazy Dask-backed DataArray by default
result = compute_with_policy(da)       # fetch + assemble when you need values
```

WCS, WMS, WMTS, and XYZ share one request-and-decode base. Thin **presets** (`XYZConfig.for_openstreetmap`, `WCSConfig.for_ea_dsp`) only appear when a public host needs different politeness — not as a catalogue of every layer id.

---

## 1 · Plan tiles

```{image} _static/guides/how-it-works-01-plan.png
:alt: AOI over a tile grid producing an ordered TileRequest list
:width: 720px
```

Service adapters turn your bbox + chunk/grid settings into an ordered list of `TileRequest`s (XYZ/WMTS grids, WCS coverage subsets). A large AOI is mostly **more requests in that list** — not a different client API.

---

## 2 · Polite concurrent fetch

```{image} _static/guides/how-it-works-02-fetch.png
:alt: Dask thread pool feeding a fetch gate into a tile host via sync httpx
:width: 740px
```

The fetch engine is **synchronous HTTP** (`httpx.Client`) gated by a concurrency limit:

- Dask’s **thread** pool runs tile tasks; a fetch gate caps in-flight HTTP (not asyncio).
- Fixed ceiling + optional rate limit when adaptive mode is off (OSM preset).
- **AIMD** when enabled: ramp under `max_concurrent`, back off on classified pressure (403/408/429/5xx and known OGC transient bodies).
- Optional **circuit breaker** (EA DSP preset): burst pressure freezes concurrency at the floor for a cooldown.
- Retries via Tenacity; `Retry-After` honoured; hard failure → `NetworkError` (no silent NaN holes).

AIMD only helps if Dask can schedule enough tile loads — hence `compute_with_policy` later.

---

## 3 · Decode

```{image} _static/guides/how-it-works-03-decode.png
:alt: HTTP bytes through shared decode into float32 y,x band arrays
:width: 700px
```

Shared `tilearray.decode` unwraps and reads JPEG/PNG/GeoTIFF (including multipart WCS payloads) into `float32` arrays. JPEG/PNG keep bands `(y, x, band)` when present; GeoTIFF elevation uses the first band. Same path for every service adapter.

---

## 4 · Dask mosaic

```{image} _static/guides/how-it-works-04-mosaic.png
:alt: Delayed tile blocks assembling into a north-up xarray DataArray
:width: 740px
```

Each tile becomes a Dask delayed block; blocks are stitched **north-up** into one `xarray.DataArray` with spatial coordinates and attrs (`crs`, `service_type`, fetch-policy summary, …). Shape is known when the graph is built; tile bytes load when something computes.

---

## 5 · Lazy · compute · cache

```{image} _static/guides/how-it-works-05-lazy-compute-cache.png
:alt: Three panels for lazy create_array, compute_with_policy, and optional disk cache
:width: 740px
```

| Path | What happens |
|------|----------------|
| `create_array(...)` (default) | **Lazy** Dask graph — coords/attrs ready; pixels not all in RAM. |
| `create_array(..., compute=True)` / `load_array(...)` | Build then compute; workers sized for the fetch policy when using the eager path. |
| `da.compute()` | Works, but default Dask worker count can **under-use** a high fetch ceiling. |
| {py:func}`tilearray.compute_with_policy` | Preferred for multi-tile mosaics: threaded scheduler aligned to `FetchPolicy.max_concurrent`. |
| `cache_dir=...` | Optional disk cache of successful tile payloads (`.tile` + `.meta`); warm hits skip the network; cold fills still obey politeness. |

Memory tracks **what you compute** (full mosaic, a reduction, a window), not automatically the whole AOI footprint. Same code path on a laptop or a bigger machine — scale via bbox, chunks, and policy.

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

Once you have the DataArray, the usual stack applies without re-implementing tile I/O per host:

- **Plot** a computed window or a reduction.
- **Analyse** with xarray/Dask (stats, terrain, resample).
- **ML** — sample chips or materialise tensors from the same chunked array API.

---

## North star

Make tiled OGC / slippy sources **easy to get into formats people already use for analysis, plotting, and ML** — without each project re-implementing tile math, polite HTTP, decode quirks, and mosaic assembly.

Regenerate committed docs assets (diagrams + live usage-guide figures):

```bash
make docs-artifacts
```

See {doc}`guides/index` for live-host usage guides and {doc}`quickstart` for copy-paste recipes.
