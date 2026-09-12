# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- `TileFetcher` engine: shared `httpx` client, bounded concurrency, `tenacity` retries (429/5xx, `Retry-After`), and pluggable per-host rate limiting via `ServiceConfig`
- Optional `on_progress(done, total, request, response)` callback on `create_array` / `load_array`
- WCS 2.0.1 coverage loading via `create_array` / `load_array` and `WCSService`
- XYZ / slippy-map tile support via `XYZConfig` and `XYZService`
- Service registry (`get_service`, `register_service`, `detect_service_type`)
- `create_tile_grid` helper for origin-aligned gridded tile planning
- Offline WCS contract tests with VCR cassettes (EA Lidar DTM)

### Changed

- Tile HTTP moved from per-call `requests.get` to centralised `TileFetcher` (`httpx` + semaphore); `fetch_tile` delegates to the shared fetcher
- `respx` moved from runtime to dev dependencies
- Package renamed from scaffold `ogc-array` to `tilearray`; README, CI, and metadata aligned with shipped API
- EA Lidar WCS GeoTIFF decode and non-divisible tile resize for native-resolution tiles

## [0.1.0] - TBD

Initial alpha release focused on lazy WCS- and XYZ-backed xarray arrays.

[Unreleased]: https://github.com/MatthewJWhittle/tilearray/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/MatthewJWhittle/tilearray/releases/tag/v0.1.0
