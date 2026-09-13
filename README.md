# tilearray

Pull geospatial **coverage tiles** from remote map services into Python as lazy **xarray** / **dask** arrays — so you can work with big rasters without downloading everything up front.

[![Tests](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml)
[![Build and Publish](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml)

> **Alpha.** The API may change. Today **Web Coverage Service (WCS)** — the Open Geospatial Consortium protocol that returns the actual raster values (not just a picture) — and **XYZ slippy-map tile URLs** are implemented. **Web Map Service (WMS)** and **Web Map Tile Service (WMTS)** show up in types/config but are **not supported yet**.

## What it does

- Talks to a **WCS 2.0.1** endpoint and fetches GeoTIFF tiles, or fetches **XYZ** PNG tiles from a URL template
- Builds a **Dask-backed** `xarray.DataArray` via `create_array` — “lazy” means the tiles are only fetched when you `.compute()` / `.load()`
- Configures endpoints with `WCSConfig` or `XYZConfig` (coordinate reference system, chunk size, cache, etc.)
- Fetches tiles through a shared HTTP engine with bounded concurrency, retries (including gateway **403** / **408**), optional rate limits, and **adaptive concurrency (AIMD)** where enabled — plus thin **fetch presets** for well-known public hosts (OpenStreetMap Foundation tiles, Environment Agency WCS). Failed tiles after retries raise `NetworkError` rather than leaving silent NaN holes.
- Lets you register other service backends later via a small service registry

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
print(da.shape, da.attrs["service_type"])  # (256, 256) 'XYZ' — backed by Dask until computed
```

`WCSConfig.from_url` and `XYZConfig.from_url` still work for custom endpoints — see [example-sources.md](example-sources.md) for URLs and coverage ids.

## Fetch presets

Tile fetches go through a shared engine with bounded concurrency, retries, and optional per-host rate limits. **Dask** stays lazy for mosaic assembly: tiles are only pulled over HTTP when you actually compute.

Two thin presets exist because public hosts want **different** behaviour — not because we catalogue every coverage id:

- **`WCSConfig.for_ea_dsp()`** — [Environment Agency Data Service Platform (EA DSP)](https://environment.data.gov.uk/) WCS. Uses **AIMD** (*additive increase, multiplicative decrease*): starts at 8 in-flight (warm), ramps while the host is happy (up to 32), remembers the last good per-host limit in-process for later mosaics, and backs off on 403/408/429/`Retry-After`/503/timeouts (×0.75, floor 4). No fixed requests-per-second cap — you do not set the limit on every call. Four retries, 60 s timeout; `Retry-After` is still honoured by `TileFetcher`. Failed tiles after retries raise `NetworkError` (no silent NaN holes).
- **`XYZConfig.for_openstreetmap()`** — [OpenStreetMap Foundation (OSMF)](https://operations.osmfoundation.org/policies/tiles/) tile policy: identifiable User-Agent, **fixed** polite limits (max 2 in-flight, ~2 requests per second). Not AIMD — OSMF policy wants low, steady load rather than ramping concurrency.

**Retries / gateway pressure**

- Retryable HTTP codes: **403, 408, 429, 502, 503, 504** (Azure Application Gateway often returns **403** for throttle/WAF pressure, not only 429).
- EA preset: **4** retries; wait = `Retry-After` if present, else exponential backoff + jitter.
- Those codes also trigger AIMD **pressure** (EA preset: ×0.75 in-flight, floor 4).
- If a tile still fails after retries: raises `NetworkError` — mosaics do **not** succeed with silent NaN holes.
- Live 256-tile EA stress: previously ~18% holes at ~27 s; after fix `finite_frac=1.0` with ~40 retries (~49 s).

These are example policies for testing host quirks, not a product catalogue. For custom endpoints, use `from_url` and tune `FetchPolicy` / `ServiceConfig` fields (`adaptive_concurrency`, `initial_concurrent_requests`, `min_concurrent_requests`, `max_concurrent_requests`, `rate_limit_per_second`, and so on). The EA preset ceiling of 32 is a safety max AIMD tunes under (`max_concurrent_requests`) — it only helps if Dask can run that many tile fetches in parallel (`create_array(..., compute=True)` sets `num_workers=max(cpu_count, max_concurrent)` automatically; for manual `.compute()`, pass the same or use `compute_thread_pool_size(fetch_policy)`).

Offline before/after bench: `uv run python scripts/bench_fetch_engine.py` (results in `benchmarks/fetch_engine_bench_results.txt`). On a live Skipton-scale EA mosaic (~16 tiles), warm start often lands ~5–10 s (EA jitter); the prior AIMD preset (start=2) was ~14 s, legacy unbounded ~10–13 s when healthy, and the old polite fixed cap of 2 at 1 req/s ~25 s — stability under 429 still matters. On a live 64-tile (~5 km / 1000×1000) EA mosaic, ceiling 16 with Dask stuck at default workers was ~20 s (`max_inflight` 8); ceiling 32 with Dask workers aligned via `compute_thread_pool_size` was ~14.7 s (`max_inflight` 32).

See [example-sources.md](example-sources.md) for additional public endpoints.

### Public API

| Export | Role |
|--------|------|
| `create_array`, `load_array` | Lazy or eager xarray from a service; `compute_thread_pool_size` (`from tilearray.array import …`) sizes Dask workers for manual `.compute()` |
| `WCSService`, `WCSParser` | Low-level WCS client + capabilities |
| `XYZService` | XYZ tile URL template client |
| `WCSConfig`, `XYZConfig`, `ServiceConfig` | Endpoint / CRS / chunk config; presets `for_openstreetmap()` / `for_ea_dsp()` |
| `get_service`, `register_service`, `detect_service_type` | Service registry |

Service types live under `tilearray.service` (e.g. `from tilearray.service import WCSConfig, XYZConfig`).

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
│   ├── service/            # WCS, XYZ + registry
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
