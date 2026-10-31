# How it works

tilearray’s job is simple to say and fiddly to do well: turn **tiled** WCS / WMS / WMTS / XYZ sources into **analysis-ready** [xarray](https://docs.xarray.dev/) arrays — efficiently, politely, and with the **same API** on a laptop or a bigger box.

You bring a bounding box and a service config. The library plans the tile grid, fetches under a concurrency ceiling, decodes, and assembles a mosaic. You keep working in the usual scientific-Python array stack (xarray + Dask + NumPy), whether the window is a few tiles or a large AOI.

```{image} _static/guides/how-it-works-pipeline.png
:alt: Pipeline from AOI and config through tile planning, polite fetch, decode/mosaic, to an xarray DataArray
:width: 720px
```

## One call, big AOI

```python
from tilearray import create_array, compute_with_policy
from tilearray.service import WCSConfig
from tilearray.types import CRS, Format

da = create_array(config, bbox, CRS.EPSG_27700)   # lazy Dask-backed DataArray
elevation = compute_with_policy(da)               # fetch + assemble when you need values
```

For a large area of interest the clever work is mostly **inside** that path:

| Stage | What tilearray does |
|-------|---------------------|
| **Plan** | Service adapters turn your bbox + chunk/grid settings into a list of `TileRequest`s (origin-aware tiling for XYZ/WMTS; coverage subsets for WCS). |
| **Fetch** | Shared `TileFetcher` pulls tiles with **bounded concurrency** (sync `httpx` + threads — not asyncio). Optional per-host rate limits; EA-style hosts can use **AIMD** concurrency under a hard ceiling plus a pressure circuit breaker. |
| **Decode** | Shared JPEG/PNG/GeoTIFF (and multipart WCS) decode → `float32` arrays with a consistent band policy. |
| **Assemble** | Tiles become Dask delayed blocks, stitched north-up into one `xarray.DataArray`. |

WCS, WMS, WMTS, and XYZ share that request-and-decode base. Thin **presets** (`XYZConfig.for_openstreetmap`, `WCSConfig.for_ea_dsp`) only appear when a public host needs different politeness — not as a catalogue of every layer id.

## Memory, concurrency, and “when does work happen?”

### Lazy by default

`create_array(..., compute=False)` (the default) builds a **Dask graph**: each tile is a delayed fetch/decode. The DataArray’s shape, coordinates, and attrs are known up front; **tile bytes are not all in RAM** until something computes them.

That matters for a big AOI:

- You can attach reductions (`.mean()`, windowed stats, ML sampling) **before** pulling every pixel.
- Memory pressure tracks **what you compute**, not necessarily the full mosaic footprint.
- Chunking follows the tile grid you planned — natural for Dask and for “same code, larger machine.”

### When you compute

| Path | Behaviour |
|------|-----------|
| `create_array(..., compute=True)` | Builds the lazy array, then computes immediately with a thread pool sized for the fetch policy. |
| `load_array(...)` | Convenience wrapper; defaults to `compute=True`. |
| `da.compute()` | Standard Dask — workers default toward CPU count, which can **under-use** a high fetch ceiling. |
| {py:func}`tilearray.compute_with_policy` | Preferred for multi-tile mosaics: threaded scheduler with `num_workers` aligned to the fetch ceiling stored on `da.attrs`. |

So: **lazy graph → optional compute**. On a laptop, keep windows modest or reduce first. On a bigger machine, raise workers *with* the policy ceiling — don’t confuse “more Dask threads” with “ignore the host’s don’t-cross line.”

### Concurrent requests (threaded, not async)

The fetch engine is **synchronous HTTP** (`httpx.Client`) gated by a per-policy concurrency limit:

- Fixed ceiling via a semaphore when adaptive mode is off (e.g. OSM preset: low in-flight + rate limit).
- **AIMD** when enabled: start at `initial_concurrent`, additive increase on success, multiplicative decrease on classified pressure (403/408/429/5xx and known OGC transient bodies), never above `max_concurrent`.
- Optional **circuit breaker**: a burst of breaker-classified events freezes concurrency at the floor for a cooldown (used by the EA DSP preset).
- Retries via Tenacity; `Retry-After` honoured when present.
- After retries fail, tiles raise `NetworkError` — mosaics do **not** succeed with silent NaN holes.

Dask’s thread pool runs tile tasks; the fetch gate caps how many HTTP calls are in flight. AIMD only helps if Dask can actually schedule that many tile loads — hence `compute_with_policy`.

### Disk cache

Pass `cache_dir=` to `create_array` to store successful tile payloads (content-addressed `.tile` + `.meta`). Warm runs skip the network for cache hits. Cache is **optional disk**, not an automatic store; cold runs still hit the host under the same politeness rules.

## What you get

```{image} _static/guides/ea-lidar-elevation.png
:alt: Example analysis-ready EA Lidar elevation mosaic and histogram from a small OSGB window
:width: 640px
```

A north-up `xarray.DataArray` with spatial coordinates and useful attrs (`crs`, `service_type`, fetch-policy summary, …). From there the usual stack applies:

- **Plot** with matplotlib / holoviews / etc. on a computed window or a reduction.
- **Analyse** with xarray/Dask (`groupby`, resampling, terrain derivatives) without rewriting I/O for each host.
- **ML** — sample chips, batch from chunks, or materialise tensors from the same array API whether the source was XYZ basemap tiles or WCS elevation.

Scale story for a geo data engineer: **one mental model**. Small AOI on a laptop and a large AOI on a workstation differ in bbox, chunking, and fetch policy — not in “which client library do I use?”

## North star

Make tiled OGC / slippy sources **easy to get into formats people already use for analysis, plotting, and ML** — without each project re-implementing tile math, polite HTTP, decode quirks, and mosaic assembly.

See {doc}`guides/index` for visual loads from OSM, NASA GIBS, and EA Lidar, and {doc}`quickstart` for copy-paste recipes.
