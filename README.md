# tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays for geographic information system (GIS) and machine learning / deep learning work — one `create_array` call, polite to servers, fast when they're healthy. WCS, WMS, WMTS, and XYZ share a request-and-decode base with adaptive AIMD fetch under a hard ceiling; thin presets appear only when a host quirk requires one.

[![Tests](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml)
[![Build and Publish](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml)

> **Alpha.** The API may change. Today **Web Coverage Service (WCS)**, **Web Map Service (WMS)**, **Web Map Tile Service (WMTS)**, and **XYZ slippy-map tile URLs** are implemented as thin adapters behind one `create_array` entry point.

## What it does

- Talks to **WCS 2.0.1** (GeoTIFF tiles), **WMS 1.3.0** (GetMap PNG/JPEG), **WMTS 1.0.0** (GetTile REST or KVP), or **XYZ** PNG tiles from a URL template
- Builds a **Dask-backed** `xarray.DataArray` via `create_array` — “lazy” means the tiles are only fetched when you `.compute()` / `.load()`
- Configures endpoints with `WCSConfig`, `WMSConfig`, `WMTSConfig`, or `XYZConfig` (coordinate reference system, chunk size, cache, etc.)
- Fetches tiles through a shared HTTP engine with bounded concurrency, retries (including gateway **403** / **408**), optional rate limits, and **adaptive concurrency (AIMD)** where enabled — plus thin **fetch presets** for well-known public hosts (OpenStreetMap Foundation tiles, Environment Agency WCS). Failed tiles after retries raise `NetworkError` rather than leaving silent NaN holes.
- Lets you register other service backends later via a small service registry
- Shared **decode** and **request** helpers: JPEG/PNG tiles keep RGB bands `(y, x, band)`; GeoTIFF elevation uses the first band only; config headers/params (User-Agent, etc.) are wired onto every outgoing `TileRequest` via all service adapters

## Install

Python **3.9+**. Prefer [uv](https://github.com/astral-sh/uv):

```bash
uv add tilearray
```

or:

```bash
pip install tilearray
```

## Quick start

Public [Environment Agency Lidar digital terrain model (DTM) WCS](https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs) — full URL and coverage id are also in [example-sources.md](example-sources.md).

### WCS coverage

```python
from tilearray import create_array
from tilearray.service import WCSConfig
from tilearray.types import CRS, Format

wcs_url = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)
# EA Lidar CoverageId is UUID-style from GetCapabilities — not the URL path slug
coverage_id = (
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
)

config = WCSConfig.for_ea_dsp(
    wcs_url,
    coverage_id=coverage_id,
    crs=CRS.EPSG_27700,  # British National Grid
    output_format=Format.GEOTIFF,
    chunk_size=(800, 800),
    grid_shape=(1, 1),
)

# 800 m × 800 m window in British National Grid (EPSG:27700)
bbox = (431900.0, 382700.0, 432700.0, 383500.0)

da = create_array(
    service_url=config,
    bbox=bbox,
    crs=CRS.EPSG_27700,
)

# Still lazy until you compute
elevation = da.compute()
print(elevation.shape, float(elevation.mean()))
```

### XYZ slippy-map tiles

```python
from tilearray import XYZConfig, create_array
from tilearray.types import CRS

config = XYZConfig.for_openstreetmap(zoom=16)

da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),  # small bbox near central London
    crs=CRS.EPSG_4326,
)
print(da.shape, da.attrs["service_type"])  # (256, 256) or (256, 256, 3) for RGB — backed by Dask until computed
```

`WCSConfig.from_url`, `WMSConfig.from_url`, `WMTSConfig.from_url`, and `XYZConfig.from_url` work for custom endpoints — see [example-sources.md](example-sources.md) for URLs and coverage ids.

### WMS GetMap

```python
from tilearray import WMSConfig, create_array
from tilearray.types import CRS, Format

config = WMSConfig.from_url(
    "https://example.com/wms",
    layers="roads",
    output_format=Format.PNG,
    chunk_size=(256, 256),
    grid_shape=(1, 1),
)

da = create_array(
    config,
    bbox=(-0.1, 51.4, 0.1, 51.6),
    crs=CRS.EPSG_4326,
)
```

### WMTS GetTile

```python
from tilearray import WMTSConfig, create_array
from tilearray.types import CRS, Format

config = WMTSConfig.from_url(
    "https://example.com/wmts",
    layer="roads",
    tile_matrix_set="EPSG4326",
    tile_matrix=12,
    url_template=(
        "https://example.com/wmts/rest/{Layer}/{Style}/{TileMatrixSet}/"
        "{TileMatrix}/{TileRow}/{TileCol}.png"
    ),
    output_format=Format.PNG,
    chunk_size=(256, 256),
)

da = create_array(
    config,
    bbox=(-0.2, 51.4, 0.2, 51.6),
    crs=CRS.EPSG_4326,
)
```

Arrays from `create_array` have north-up y coordinates (row 0 = north).

## Fetch presets

Tile fetches go through a shared engine with bounded concurrency, retries, and optional per-host rate limits. **Dask** stays lazy for mosaic assembly: tiles are only pulled over HTTP when you actually compute.

Two thin presets exist because public hosts want **different** behaviour — not because we catalogue every coverage id:

- **`WCSConfig.for_ea_dsp()`** — [Environment Agency Data Service Platform (EA DSP)](https://environment.data.gov.uk/) WCS. Uses **AIMD** (*additive increase, multiplicative decrease*): starts at 8 in-flight (warm), ramps while the host is happy (up to 32), remembers the last good per-host limit in-process for later mosaics, and backs off on 403/408/429/`Retry-After`/503/timeouts (×0.75, floor 4). No fixed requests-per-second cap — you do not set the limit on every call. Four retries, 60 s timeout; `Retry-After` is still honoured by `TileFetcher`. Failed tiles after retries raise `NetworkError` (no silent NaN holes).
- **`XYZConfig.for_openstreetmap()`** — [OpenStreetMap Foundation (OSMF)](https://operations.osmfoundation.org/policies/tiles/) tile policy: identifiable User-Agent, **fixed** polite limits (max 2 in-flight, ~2 requests per second). Not AIMD — OSMF policy wants low, steady load rather than ramping concurrency.

**Retries / gateway pressure**

- Retryable HTTP codes: **403, 408, 429, 500, 502, 503, 504** (Azure Application Gateway often returns **403** for throttle/WAF pressure, not only 429; EA DSP may return **500** `internal_error` under load).
- EA preset: **4** retries; wait = `Retry-After` if present, else exponential backoff + jitter.
- Those codes also trigger AIMD **pressure** (EA preset: ×0.75 in-flight, floor 4).
- If a tile still fails after retries: raises `NetworkError` — mosaics do **not** succeed with silent NaN holes.
- Live 256-tile EA stress: previously ~18% holes at ~27 s; after fix `finite_frac=1.0` with ~40 retries (~49 s) at ×0.5 pressure; after less-jumpy AIMD (×0.75, floor 4) ~53 s, `finite_frac=1.0`, ~89 retries (same ~50 s band as ×0.5 / ~40 retries — stays hotter under Azure blips, still complete).

These are example policies for testing host quirks, not a product catalogue. For custom endpoints, use `from_url` and tune `FetchPolicy` / `ServiceConfig` fields (`adaptive_concurrency`, `initial_concurrent_requests`, `min_concurrent_requests`, `max_concurrent_requests`, `rate_limit_per_second`, and so on). The EA preset ceiling of 32 is a safety max AIMD tunes under (`max_concurrent_requests`) — it only helps if Dask can run that many tile fetches in parallel (`create_array(..., compute=True)` sets `num_workers=max(cpu_count, max_concurrent)` automatically; for manual `.compute()`, pass the same or use `compute_thread_pool_size(fetch_policy)`).

Offline before/after bench: `uv run python scripts/bench_fetch_engine.py` (results in `benchmarks/fetch_engine_bench_results.txt`). On a live Skipton-scale EA mosaic (~16 tiles), warm start often lands ~5–10 s (EA jitter); the prior AIMD preset (start=2) was ~14 s, legacy unbounded ~10–13 s when healthy, and the old polite fixed cap of 2 at 1 req/s ~25 s — stability under 429 still matters. On a live 64-tile (~5 km / 1000×1000) EA mosaic, ceiling 16 with Dask stuck at default workers was ~20 s (`max_inflight` 8); ceiling 32 with Dask workers aligned via `compute_thread_pool_size` was ~14.7 s (`max_inflight` 32).

See [example-sources.md](example-sources.md) for additional public endpoints.

### Decode / request pipeline

Tile bytes and HTTP metadata follow two shared paths so WCS, WMS, WMTS, and XYZ behave consistently:

**Decode** (`tilearray.decode`) — unwrap response bytes → read (GeoTIFF / JPEG / PNG) → apply band policy → `float32`. JPEG and PNG use **`preserve`**: colour mosaics stay `(y, x, band)` (e.g. RGB is 3 bands). GeoTIFF elevation uses **`first_band`** (single `(y, x)` surface). Multipart WCS unwrap is an extension point today (`unwrap_multipart` is a pass-through stub; USGS ArcGIS ImageServer is not supported yet). You can still pass a custom `tile_decoder` to `create_array` when you need different behaviour.

Multi-band mosaics stitch tiles with spatial `concatenate` on `y`/`x` (not `da.block`, which would stack along `band` — e.g. a 2×3 grid of RGB tiles would become `band=9`). Single-band GeoTIFF elevation mosaics still use `da.block`.

**Request composition** (`tilearray.service.requests.compose_tile_request`) — merges `headers` and `params` from service config and call-time options onto every `TileRequest`. Preset User-Agent strings and auth hooks therefore reach the fetch layer without each service re-implementing header wiring.

### Public API

| Export | Role |
|--------|------|
| `create_array`, `load_array` | Lazy or eager xarray from a service; `compute_thread_pool_size` (`from tilearray.array import …`) sizes Dask workers for manual `.compute()` |
| `WCSService`, `WCSParser` | Low-level WCS client + capabilities |
| `WMSService` | WMS 1.3.0 GetMap client (CRS-aware bbox) |
| `WMTSService`, `WMTSParser` | WMTS GetTile client (REST `{TileMatrix}/{TileRow}/{TileCol}` or KVP; optional GetCapabilities) |
| `XYZService` | XYZ tile URL template client |
| `WCSConfig`, `WMSConfig`, `WMTSConfig`, `XYZConfig`, `ServiceConfig` | Endpoint / CRS / chunk config; fetch presets `for_openstreetmap()` / `for_ea_dsp()` only |
| `get_service`, `register_service`, `detect_service_type` | Service registry |

Service types live under `tilearray.service` (e.g. `from tilearray.service import WCSConfig, WMSConfig, WMTSConfig, XYZConfig`).

## Development

```bash
git clone https://github.com/MatthewJWhittle/tilearray.git
cd tilearray
uv sync --dev
make pre-commit
```

```bash
make test          # unit tests
make test-cov      # with coverage
uv run pytest -m integration   # live WCS (slow)
```

Conventions: [guidelines.md](guidelines.md). Testing notes: [TEST_IMPROVEMENT_PLAN.md](TEST_IMPROVEMENT_PLAN.md).

```bash
make lint
make format
make pre-commit-run
```

## Layout

```
tilearray/
├── src/tilearray/          # library
│   ├── array.py            # create_array / load_array
│   ├── decode.py           # shared unwrap → read → band-policy decode
│   ├── service/            # WCS, WMS, WMTS, XYZ + registry
│   │   ├── config.py       # WCSConfig, WMSConfig, WMTSConfig, XYZConfig
│   │   ├── requests.py     # compose_tile_request (headers/params from config)
│   │   ├── wms.py          # WMS GetMap adapter
│   │   └── wmts.py         # WMTS GetTile adapter (+ optional GetCapabilities)
│   └── types.py
├── tests/
│   ├── unit/
│   └── integration/
├── notebooks/examples/
├── guidelines.md
└── example-sources.md
```

## Contributing

1. Fork and branch from `main`.
2. Change code; run `make test && make lint`.
3. Open a pull request.

## License

Apache License 2.0 — see [LICENSE](LICENSE).
