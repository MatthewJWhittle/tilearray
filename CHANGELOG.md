# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- EA DSP fetch preset: sustained-403 **circuit breaker** (6+ gateway 403s within 5 s → freeze AIMD at floor for 15 s) to limit WAF/retry storms on large cold mosaics
- `TileFetcher.stats`: live `current_limit`, `pressure_403_count`, `circuit_breaker_trips`, and `circuit_breaker_frozen` for observing AIMD backoff during gateway pressure
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

## [0.1.0] - TBD

Initial alpha release focused on lazy WCS- and XYZ-backed xarray arrays.

[Unreleased]: https://github.com/MatthewJWhittle/tilearray/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MatthewJWhittle/tilearray/releases/tag/v0.1.0
