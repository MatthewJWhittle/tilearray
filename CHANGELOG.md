# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Documentation

- Maintainer stage-gate notebook (`notebooks/stage_gate_0_2.ipynb`) + run notes in `notebooks/README.md`: local editable install → live OSM / EA DSP mosaics → politeness knobs, soft-fail attrs, and a human checklist before the 0.2.0 cut
- Document private/keyed hosts via existing config `params=` / `headers=` (query keys, legacy username/password query params, Bearer/Basic headers); OS Maps XYZ recipe uses env-backed `params` instead of `?key=` in the URL template; soft-fail still hard-aborts 401 (related #58). Caps credential parity for WCS/WMTS is under Changed (#57)

### Fixed

- Soft-fail observability: `compute_with_policy` refreshes `attrs["tilearray_tile_errors"]` (`soft_failed`, `aborted`, `examples`) from `FetchProgress` after lazy `create_array` — same counters as the eager `compute=True` path (#73)

### Changed

- WCS GeoTIFF decode now uses ``tifffile`` directly (bytes or path); dropped the ``geotiff`` → ``zarr<2.13`` / ``numcodecs<0.16`` pin cage so clean-env installs no longer need that dependency gymnastics. XYZ-without-GDAL posture unchanged (no hard rasterio/GDAL runtime dep) (#53)
- Public-boundary failures now raise the typed `TileArrayError` hierarchy instead of bare `ValueError` / `RuntimeError`: bad inputs → `ValidationError`, caps/XML parse → `ParseError`, service detection / missing decoder → `ConfigurationError`, WCS/WMTS capabilities HTTP (`httpx`) → `NetworkError`. **Compat:** callers that catch bare `ValueError` for library validation must switch to `TileArrayError` / the specific subclass (or catch both during transition). Pydantic model field checks (`BoundingBox` coordinates, etc.) still surface via pydantic as before (#72)
- WCS/WMTS capabilities and DescribeCoverage (plus direct `WCSService.get_coverage`) now use sync **httpx** with one shared UA/timeout policy and inherit config `headers`/`params` — private-server credentials reach plan-time GETs the same way they reach tiles. Drops the runtime `requests` dependency (#57)
- Docs: one compute habit on primary paths — OSM/GIBS guides, how-it-works, troubleshooting, and API index use `compute_with_policy`; `load_array` documented as eager sugar for `create_array(..., compute=True)`; quickstart WMTS honesty note (`WMTSConfig` supported, host guide TBD / #60) (#55)

### Added

- Analysis-ready mosaic attrs for xarray/rioxarray handoff: Affine `transform` + `transform_source="mosaic_bbox"`, `band_policy`, and elevation GeoTIFF `nodata`/`_FillValue` (NaN after decode). Documented north-up + optional rioxarray recipe; mosaic CRS remains the request `crs=` (never silently replaced by per-tile GeoTIFF tags) (#54)
- Large-AOI politeness evidence scaffold (#56): `@pytest.mark.soak` multi-tile cold mosaics under EA DSP + OSM preset ceilings (nightly via `@net`/`@slow`, deselected on PRs); offline fail-loud abort under concurrent pressure; maintainer script `scripts/soak_large_aoi_presets.py` for county-/valley-scale soaks; how-it-works / troubleshooting / building-docs note on nightly vs maintainer split
- Opt-in plan-time tile error policy for mosaics (`on_tile_error=` / `soft_fail=True`) with library preset `soft_fail_exhausted_tiles`: after retries, soft-fill tile-local exhausted failures (404 / 5xx / transport) as NaN blocks without aborting siblings; auth (401), exhausted 429, decode errors, and soft-fail fraction above `soft_fail_max_fraction` still hard-abort. Default remains hard-fail + `FetchProgress.abort` (#65)
- Top-level `FetchPolicy` export (`from tilearray import FetchPolicy`) so docs/API reference match `__all__` (#70)

### Changed

- Public `__all__` no longer advertises parsers (`WCSParser`, `WMTSParser`), concrete services (`WCSService`, `WMSService`, `WMTSService`, `XYZService`), or `BaseService`. Prefer configs + `create_array`; low-level clients remain importable from `tilearray.service` / submodules during alpha (#70)
- Mosaic `y`/`x` coordinates are **pixel centers** consistent with `attrs["transform"]` (north-up Affine from planned bbox + shape), instead of edge-span `linspace` endpoints (#54)
- Docs: `**service_options` key families (fetch / planner / identity) and `max_concurrent*` one-job-per-name map in how-it-works, with API + troubleshooting cross-links — no public signature change (#71)

## [0.1.1] - 2026-11-01

### Fixed

- README header logo uses an absolute HTTPS PNG URL so PyPI / Warehouse renders the mark (relative `docs/_static/...` paths do not resolve in the uploaded long description)

## [0.1.0] - 2026-10-31

Initial alpha release focused on lazy WCS- and XYZ-backed xarray arrays.

### Fixed

- **Shared pressure pipeline:** HTTP responses are classified (status + body signatures) before retry / AIMD / circuit-breaker handling — replaces host-shaped forks (EA 403-only breaker, USGS-only OGC 404 helpers). Built-in signatures cover gateway 403/408/429/5xx and OGC `ExceptionReport` / `InvalidParameterValue` / `SUBSETTINGCRS` transient 404 bodies; custom signatures are pluggable via `PressureClassifier.register`
- Disk tile cache + decode: trust bytes over `Content-Type` — `sniff_effective_content_type` detects multipart / TIFF markers; `.meta` sidecar is kept but warm reads no longer require perfect headers

### Added

- `tilearray.pressure`: `PressureSignature`, `PressureClassifier`, `classify_response`, and built-in signature registry
- EA DSP fetch preset: **pressure circuit breaker** (6+ classified breaker events within 5 s — gateway 403 or 429 — → freeze AIMD at floor for 15 s) to limit WAF/retry storms on large cold mosaics
- `TileFetcher.stats`: live `current_limit`, `pressure_event_count`, `circuit_breaker_trips`, and `circuit_breaker_frozen` for observing AIMD backoff during gateway pressure
- Mosaic fetch **abort**: after a hard tile failure (`NetworkError`), sibling tile tasks stop scheduling new HTTP (via shared `FetchProgress`); in-flight requests may still complete (Dask thread pool limitation)

- Capabilities-driven WCS GetCoverage: axis labels and native CRS from DescribeCoverage; automatic reprojection to native CRS when needed; shared `unwrap_multipart` for ArcGIS `multipart/related` GeoTIFF payloads
- Offline USGS 3DEP ArcGIS WCS contract tests (DescribeCoverage + multipart GetCoverage cassettes)
- Thin WMS 1.3.0 and WMTS 1.0.0 adapters: `WMSService` (GetMap with CRS-aware bbox), `WMTSService` (GetTile REST or KVP, optional GetCapabilities), `WMSConfig` / `WMTSConfig`, shared decode and request composition with WCS/XYZ (no host-specific presets)
- `TileFetcher` engine: shared `httpx` client, bounded concurrency, `tenacity` retries (429/5xx, `Retry-After`), and pluggable per-host rate limiting via `ServiceConfig`
- Retryable HTTP status codes now include gateway throttling **403** and **408** (same AIMD / `Retry-After` path as 429/503)
- Fetch presets: `XYZConfig.for_openstreetmap()` (OSMF User-Agent + polite limits) and `WCSConfig.for_ea_dsp()` (EA WCS Retry-After / 429 tuning)
- Offline before/after bench script: `scripts/bench_fetch_engine.py` (results in `benchmarks/fetch_engine_bench_results.txt`)
- Optional `on_progress(done, total, request, response)` callback on `create_array` / `load_array`
- WCS 2.0.1 coverage loading via `create_array` / `load_array` and `WCSService`
- XYZ / slippy-map tile support via `XYZConfig` and `XYZService`
- Service registry (`get_service`, `register_service`, `detect_service_type`)
- `create_tile_grid` helper for origin-aligned gridded tile planning
- Offline WCS contract tests with VCR cassettes (EA Lidar DTM)

### Changed

- EA DSP preset AIMD ceiling lowered from 32 to **10** (live county-scale evidence: ceiling 32 fails on ≥~256-tile cold mosaics; ceiling 8 completes Aire ~1015-tile valley)
- README / example-sources: county-scale EA guidance (`max_concurrent_requests` ≤ preset ceiling)
- README: shared client AIMD gate, mosaic queue vs concurrency, EA sustained ceiling (~10)

- WCS subset axes no longer hard-code Long/Lat per CRS; `WCSService` reads envelope `axisLabels` from DescribeCoverage (EA `E`/`N`, ArcGIS `x`/`y`, geographic fallback `Long`/`Lat`)
- README: capabilities-driven WCS, shared `multipart/related` GeoTIFF unwrap, and EA Lidar + USGS 3DEP proof on real endpoints
- `create_array` / `load_array`: y coordinates now decrease with row index (north at row 0), matching `_organize_tiles` mosaic layout so north-up display no longer requires a manual flip
- Multi-band mosaic assembly: spatial `concatenate` on `y`/`x` for RGB JPEG/PNG tiles (fixes `da.block` stacking along `band`); single-band GeoTIFF mosaics still use `da.block`
- README: `create_array` output has north-up y coordinates (row 0 = north)
- README: north-star positioning (GIS/ML objective, shared request-and-decode base, adaptive AIMD under a ceiling, presets for host quirks only)
- example-sources: peer quirk one-liners for GIBS BlueMarble (layer-specific WMTS/XYZ template), EA VOM WMS (year-suffixed layers, transparent PNG RGBA)
- README and example-sources: document shared decode pipeline (JPEG/PNG `preserve`, GeoTIFF `first_band`) and request composition (`compose_tile_request` wires config headers/params)
- README and example-sources: less-jumpy AIMD 256-tile bench note (×0.75, floor 4); peer stress source shortlist
- README Fetch presets: retries / gateway pressure note (403/408, AIMD pressure, fail-loud mosaics)
- `load_array` / `create_array` raise `NetworkError` when a tile fetch fails after retries instead of silently filling failed regions with NaN
- README Fetch presets: live 64-tile (~5 km) bench (~20 s at ceiling 16 / `max_inflight` 8 → ~14.7 s at ceiling 32 with aligned Dask workers); `compute_thread_pool_size` noted in Public API table
- README Fetch presets: honest Skipton warm-start timings and note that EA ceiling 32 is a tunable safety max
- README and example-sources: fetch preset docs updated for AIMD EA DSP defaults (adaptive concurrency, no fixed 1 req/s cap)
- README and example-sources: fetch preset quick start, Fetch presets section, and corrected EA Lidar CoverageId in examples
- Tile HTTP moved from per-call `requests.get` to centralised `TileFetcher` (`httpx` + semaphore); `fetch_tile` delegates to the shared fetcher
- `respx` moved from runtime to dev dependencies
- Package renamed from scaffold `ogc-array` to `tilearray`; README, CI, and metadata aligned with shipped API
- EA Lidar WCS GeoTIFF decode and non-divisible tile resize for native-resolution tiles

[Unreleased]: https://github.com/MatthewJWhittle/tilearray/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/MatthewJWhittle/tilearray/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/MatthewJWhittle/tilearray/releases/tag/v0.1.0
