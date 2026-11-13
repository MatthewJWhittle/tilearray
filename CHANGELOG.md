# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Process-local GetCapabilities / DescribeCoverage memo (~1 hour TTL) keyed by URL + params + auth identity, with `refresh=` bypass on `get_capabilities` / `describe_coverage` / `inspect_endpoint`. Shares one in-process fetch across inspect → preview → create; disk / `cache_dir` persistence remains out of scope (#184, #178)

### Changed

- `create_array` reuses the decoded band-count probe for the matching mosaic tile when fetch identity (URL + params) and shape/dtype match, so preserve-policy formats no longer download that tile twice (#183, #178)
- WMS/WCS default ``chunk_size`` is now ``1024×1024`` when the caller omits it (still coupled to HTTP request size). XYZ/WMTS stay near native ``~256``. Aligns with GDAL WMS default block size; full request≠chunk decoupling is a follow-on (#181, #178)
- README / PyPI long description: replace the verbose OSM “Success: shape…” prose with the committed London mosaic figure (absolute GitHub Pages URL so Warehouse renders it). Keep `expand_palette=True` in the OSM snippet for plot-ready RGB; default remains indices

## [0.3.2] - 2026-11-13

### Fixed

- WCS mosaics that straddle a coverage envelope no longer send GetCoverage subsets outside `DescribeCoverage` `spatial_extent` (HTTP 404 `InvalidSubsetting`). Wholly outside tiles skip HTTP and pad coverage nodata; straddling tiles clip the subset to the intersection. Band-count probes sample the first in-extent tile. Envelope `axisLabels` (including `y x`) are honoured when parsing the extent. An AOI entirely outside the envelope raises `ValidationError` naming the coverage extent (#168)
- Resolution mosaics keep the requested pixel count when the AOI is not origin-aligned: `snap_bbox_outward` uses `round(span / resolution)` and floor-aligns the minimum corner instead of taking the intersecting pixel-union (which added a 1-px sliver, e.g. 513 for a 512-px request). Tile walks place any chunk remainder on the mosaic-trailing edge (south / east) so a short leftover is never the leading dask/Zarr chunk (#171)
- Lazy mosaics no longer hang a live `FetchProgress` (with `threading.Lock`) on `DataArray.attrs`; attrs store a JSON-safe registry token so `deepcopy` / `pickle` / `to_zarr` work. Combined with trailing chunk remainders from the snap fix, Zarr accepts the dask chunk layout (#169)
- HTTP 404 responses (including OGC `ExceptionReport` bodies such as `InvalidSubsetting`, `InvalidParameterValue`, and `NoSuchCoverage`) fail fast with a single attempt instead of being retried as transient pressure (#170)
- `fetch_retries=0` is honoured: `TileFetcher` uses `FetchPolicy.retries` alone for the attempt budget, so the `TileRequest.retries` default no longer forces four attempts (#170)
- WCS 2.0.1 GetCoverage omits `scaleSize` / `width` / `height` when the requested grid matches DescribeCoverage native resolution (RectifiedGrid `offsetVector`). Always-on `scaleSize` from #165 hung EA LiDAR at native size; non-native requests still send `scaleSize` for MapServer (#154). Scaled GetCoverage failures fail fast (`retries=0`) with a Scaling hint instead of burning every retry (#177)
- Packaging cassette integrity check installs via `uv sync --dev`; PyYAML is declared in the project `dev` dependency group (#180)
- WMS / WCS / WMTS-KVP `params=` no longer raises `TypeError` (protocol query keys and caller params used the same keyword). Caller API keys and vendor query params merge into the tile request alongside GetMap / GetCoverage / GetTile keys (#151)
- Versioned OGC EPSG URNs with multi-dot versions (e.g. `urn:ogc:def:crs:EPSG:6.18.3:3857`) canonicalise to `EPSG:3857` instead of truncating to `EPSG:6` (#163)
- Sync HTTP clients follow redirects (`follow_redirects=True`) so caps/tile GETs through a 301/302 succeed (#159)
- Query strings on service `base_url` (e.g. MapServer `?map=/path/to.map`) peel into request `params` and survive GetMap / inspect / tile fetch (#155)
- Thin RGB PNG tiles (e.g. height=1, shape `(1,256,3)`) keep band-last layout using request width/height instead of a bands-first heuristic (#152)
- GeoTIFF PREDICTOR / missing-`imagecodecs` failures raise `ConfigurationError` naming imagecodecs instead of a bare `ValueError` (#153)
- WCS 2 GetCoverage emits Scaling `scaleSize` (e.g. `x(256),y(256)`); lat-first axis labels map ranges by axis meaning; `subsettingCRS` is omitted when subsetting in the coverage native CRS (#154, #158)
- Public create/compute surfaces wrap zero-area / inverted bbox and decode failures as `ValidationError` / `ParseError` (not bare pydantic / PIL) (#162)
- Ground `resolution=` is snapped / used to infer `grid_shape` in the **mosaic** CRS after `output_crs=` transforms the AOI — lon/lat queries no longer produce ~1e5 one-pixel tiles when targeting a projected mosaic (#160)
- Huge tile plans: `FetchPolicy.max_tile_requests` (default 10_000) refuses `create_array` with `ValidationError`; `preview_array` returns an O(1) estimate with empty `tile_requests` instead of materialising every `TileRequest`. WMS/WCS resolution/grid plans and WMTS slippy/matrix ranges all estimate in O(1) (#161)
- World-edge / out-of-domain AOIs (XYZ antimeridian or polar, WMTS past ±180) raise `ValidationError` instead of silently clamping tile indices; exact domain edges (e.g. lon ``180``) stay inclusive of the last in-domain tile via half-open tile ranging (#164)
- WCS `scaleSize` uses the same CRS-aware axis meaning as subsets for opaque labels under lat-first CRSs (#154/#158)
- Caps/DescribeCoverage error text still shows redacted secrets (e.g. `api_key=***`) after base-URL query peeling into `params` (#155)
- WMTS GetCapabilities Dimension / Style / Format defaults flow into KVP and REST GetTile URLs (#156, #157)

## [0.3.1] - 2026-11-10

### Breaking

- Mosaic pixel-grid CRS attr renamed **`attrs["crs"]` → `attrs["mosaic_crs"]`**. Query CRS stays `attrs["bbox_crs"]` / `bbox_crs=`. Matching preview field: **`ArrayPreview.crs` → `ArrayPreview.mosaic_crs`**. No dual-write / compatibility alias for the old attr key. Service config `crs=` (request default) and nested `bbox_requested["crs"]` are unchanged
- Renamed public preflight API **`plan_array` → `preview_array`** and **`ArrayPlan` → `ArrayPreview`** (module `tilearray.plan` → `tilearray.preview`). Same read-only job summary; names match domain **Preview** (what the job will look like). No compatibility aliases. **`MosaicPlan`** (tile list / how) is unchanged
- `create_array` / `preview_array` / `load_array` take **`bbox_crs=`** as the query CRS (third positional argument). The overloaded `crs=` argument on those entry points is **removed** — use `bbox_crs=` (service config `crs=` for request defaults is unchanged). A `BoundingBox` whose CRS conflicts with `bbox_crs=` raises `ValidationError` (no silent reproject of conflicting declarations). `attrs["bbox_crs"]` records the query CRS; `attrs["mosaic_crs"]` is the **actual** mosaic / output CRS. No client-side warp. Docs: design-principles, how-it-works, API, quickstart, troubleshooting
- Renamed **`compute_with_policy` → `compute_mosaic`** (canonical compute habit). Removed the compute-path `max_concurrent=` shorthand — use `num_workers=` to size Dask threads only (does **not** change HTTP in-flight). Host ceilings stay on `create_array` / `FetchPolicy` / `max_concurrent_requests` / `call_max_concurrent`
- **PNG/JPEG mosaics default to `uint8`** (plot-ready for `xarray.plot.imshow(rgb="band")`). Previously the default `dtype=float32` cast left values in 0–255 and washed out / speckled RGB plots. Pass `dtype="float32"` explicitly when you need float imagery; soft-fail still auto-selects float32 so NaN fill works. GeoTIFF elevation remains float32. `attrs["dtype_policy"]` is `plot_ready_uint8` for the new imagery default

### Added

- Optional server-side **`output_crs=`** for WMS / WCS (`WMSConfig` / `WCSConfig` / create/preview named arg). WCS sends `outputCRS`; WMS uses it as the GetMap CRS. Mosaic `attrs["mosaic_crs"]` matches. Unsupported values fail when the service advertises a supported-CRS list. **Rejected** on XYZ / WMTS (native grid only — never implies client reproject)
- `choose_matrix_for_resolution` (+ `MatrixLevelSummary` / `MatrixChoice`): pure helper to pick a WMTS TileMatrix from a target resolution in **CRS units/pixel** and policy (`coarser` / `nearest` / `finer`). Compares on `resolution`; ScaleDenominator is best-effort (resolution-only levels accepted; `MatrixChoice.scale_denominator` may be `None`). Does not choose inside `create_array` and does not resample. Inspect `MatrixSetSummary.levels` carries scale + resolution. `ArrayPreview` gains `matrix_id` and `planned_pixel_size` (extent ÷ shape; CRS units/pixel — not ground metres for Web Mercator). Docs recipe on the WMTS guide (#123; related #93 / #61)
- `EndpointInspection.build_config` for WMTS accepts **`target_resolution=`** (+ optional `resolution_policy=`) as an alternative to `tile_matrix=` — wraps `choose_matrix_for_resolution` on the selected TileMatrixSet's levels and still attaches `url_template` from GetCapabilities when present

### Fixed

- GetCapabilities **OGC ExceptionReport** bodies raise a loud parse/service error (with server `ExceptionText`) instead of sniffing as empty capabilities / “0 layers”
- Soft-fail + **integer/uint mosaic dtype** with NaN `soft_fill_value` raises `ValidationError` (NaN→0 coercion previously hid failed tiles as black imagery). Use float dtype or a finite fill
- WMS `build_tile_request` rejects **bbox CRS ≠ GetMap CRS** when `output_crs=` / `crs=` disagree with the tile bbox (no silent metre→lat/lon GetMap)
- `EndpointInspection.build_config` for WMS validates **styles per layer** and enforces GetMap **LAYERS/STYLES arity** (typos and one-style-for-N-layers no longer slip through)
- Tile fetch treats **HTTP 200 HTML portals** and **OGC ExceptionReports** (even with `Content-Type: image/*`) as fetch failures with actionable HTML/OGC messages — not Pillow `UnidentifiedImageError`
- JPEG mosaic attrs set `palette_mode="rgb"` (never `"indices"`); PNG still records indices vs expanded RGB via `expand_palette=`

### Changed

- Docs first-run path: install / quickstart / README / OSM London guide state that plotting needs a separate `matplotlib` install (not a core dependency; no `[plot]` extra). Primary OSM snippets use `expand_palette=True` for full-colour RGB and note that the default keeps palette indices; guide figure artifact script aligned
- Docs usage guides / quickstart / multi-AOI: live-host mosaic figures for EA Vegetation Object Model (WMS) and three noncontiguous OSM patches; sibling guides gain `plot.imshow` after create/compute; quickstart embeds EA Lidar / WMS / Kartverket figures; artifact script gains `--only vom` / `--only multi-aoi`
- WMTS happy path: `inspect_endpoint` → `build_config(..., target_resolution=...)` (optional `resolution_policy`) picks a TileMatrix and attaches REST templates from GetCapabilities — guides / quickstart / stage-gate lead with service + layer + matrix set + resolution + AOI/`bbox_crs`, not URL templates or matrix wire ids. Explicit `tile_matrix=` / `url_template=` remain advanced. Stage-gate notebook updated for `bbox_crs` / `compute_mosaic` / Kartverket WMTS knobs
- `inspect_endpoint` probes GetCapabilities (WMTS → WMS → WCS) when the URL path has no protocol token (e.g. Kartverket `…/v1/service`); pass `service_type=` to skip. Soft-fail demo uses `band_policy="first_band"` so the hard probe does not block an all-404 soft-fail pad. Soft-fail **phases** (probe hard / post-graph soft-fill / auth·429 abort) are documented in how-it-works and troubleshooting

## [0.3.0] - 2026-11-04

Alpha cut: native XYZ/slippy georef, inspect/plan preflight, broader CRS/WMTS fidelity, and a five-minute first-run docs path. The API remains Alpha.

### Added

- `inspect_endpoint` / `inspect_capabilities` + `EndpointInspection.build_config`: discover layers/coverages, formats, CRS, and WMTS matrices from GetCapabilities (or explain XYZ template limits), then build a validated `*Config` without guessing identifiers. Supports `headers=` / `params=` with secret redaction in printed summaries. How-to: `docs/inspect-endpoint.md` (#96)
- AOI / fetch preflight helpers: `recommend_grid_shape`, `plan_array` → `ArrayPlan`, and `fetch_summary` → `FetchSummary`. Inspect planned bbox, grid, request count, concurrency ceiling, cache warm/cold, and unknown band/byte counts where not yet knowable before mosaic pixel fetch; summarise progress / pressure after compute. Default progress bar and `create_array` path unchanged (#61)
- Maintainer stage-gate notebook (`notebooks/stage_gate_0_3.ipynb`): five-minute OSM path with editable knobs, `plan_array` / `fetch_summary`, and EPSG:3857 georef checks before the 0.3.0 cut

### Breaking

- **XYZ / slippy WMTS mosaic georef (alpha):** mosaics are now labelled **`EPSG:3857`** with a **tile-union** extent and metre Affine/`y`/`x` coords — matching the unwarped Web Mercator pixel grid. Previously `attrs["crs"]` echoed the query `crs=` (often `EPSG:4326`) and the Affine was stretched from the **query** lon/lat bbox onto the full multi-tile grid (wrong scale/origin; falsely geographic). `crs=` remains the **query** CRS for bbox interpretation only; `bbox_requested` keeps the caller's window. **No client-side warp** of pixels. Callers that assumed XYZ/`GoogleMapsCompatible` mosaics were geographic must read `attrs["crs"]` / `transform` as Web Mercator (or reproject downstream). Metadata-backed WMTS native matrices are unchanged.

### Fixed

- XYZ and no-caps slippy WMTS planning set `inferred_mosaic_bbox` in EPSG:3857 (same adopt path as metadata WMTS), so mosaic CRS / transform / coords describe the native slippy grid instead of the query window
- Clipped / partial WCS GeoTIFF responses are placed on the requested tile grid using `ModelPixelScale` / `ModelTiepoint` (and optional GeoKey EPSG), with NaN padding for uncovered cells — never stretched or silently shifted. Mismatched resolution, CRS, rotation, or missing georef fails with a clear `ValidationError`. Coverage pads are distinct from soft-fail tile fills (#95)
- Raster fidelity: GeoTIFF default `band_policy` is now `preserve` (multiband coverages keep all bands; pass `band_policy="first_band"` to reduce explicitly). Palette PNGs stay as indices unless `expand_palette=True`. Imagery decode keeps native dtype until the `create_array(..., dtype=)` cast; GeoTIFF still promotes to float32 for NaN nodata. Mosaic attrs record `band_policy`, `palette_mode`, and `dtype_policy` (#94)

### Changed

- `FetchPolicy` is a frozen Pydantic model with the same field bounds as `ServiceConfig` fetch knobs (invalid ceilings / timeouts fail at construction); use `model_copy` instead of `dataclasses.replace`
- Shared sync ``httpx`` client factory (`make_sync_client`) used by plan-time `http_get` and `TileFetcher`; `Format.coerce` shared by WCS/WMS/WMTS; `ServiceConfig` rejects non-positive `chunk_size` / `grid_shape`
- `CRS.parse` accepts common OGC URN/URI spellings (`urn:ogc:def:crs:EPSG::…`, OpenGIS EPSG HTTP URIs, CRS84 aliases) via shared `canonicalize_crs_input`; WCS/WMTS/inspect reuse that path
- Internal planning helpers (`ArrayRequest`, decoder resolution, band probing, `recommend_grid_shape`, fetch-policy attrs snapshots, tile-grid organisation, cache keys) live in `tilearray._planning` so `plan_array` imports nothing private from `array`; `ArrayRequest` rejects non-positive `chunk_size` / `grid_shape` / `resolution` at construction; removed unused `TileFetcher._record_pressure`; service CRS coercion uses `CRS.parse` directly
- Split `tilearray.inspect` into an internal package (`_models` / `_parse` / `_resolve` + façade) along existing seams; public imports (`inspect_endpoint`, `EndpointInspection`, …) stay stable
- `CRS` accepts any pyproj-valid projected/geographic CRS (EPSG authority strings, integers, `pyproj.CRS`), not only the four former enum members. Presets (`CRS.EPSG_4326`, `3857`, `32633`, `27700`) remain. Invalid codes (including typos like `EPGS:28992`) raise an actionable `ValidationError`. WMS 1.3 bbox axis order and WCS default subset axis labels follow geographic vs projected axes via pyproj (#92)
- WMTS planning uses the service TileMatrixSet native CRS and ScaleDenominator units (projected metres vs geographic degrees), including non-numeric TileMatrix ids and layer TileMatrixSetLimits. Query bboxes may be in another CRS; the mosaic is labelled with the matrix SupportedCRS and tile-aligned extent. Unknown or incomplete matrices raise `ValidationError` instead of guessing Web Mercator (#93)
- WMTS `create_array` auto-loads GetCapabilities for non-slippy TileMatrixSets (so native grids work without a separate `apply_capabilities()` call). Numeric `tile_matrix` values match zero-padded capabilities ids (e.g. `8` → `08`); planned TileMatrix ids win over config when building GetTile requests (#60)
- `compute_mosaic` emits a `UserWarning` when a non-`threads` `scheduler=` is passed (still forces `scheduler="threads"` so the process-local fetch gate applies) (#59)

### Documentation

- First-run UX: homepage / README value prop (why vs hand-rolled OGC assemble, fit vs rioxarray/GDAL, non-goals); Start-here reorder (tutorial → inspect → how-it-works → guides → troubleshooting → API; multi-AOI demoted); five-minute OSM XYZ primary path with plot + CRS/transform/extent; other protocols under “Also”; thinned how-it-works concurrency name map (link API)
- Quickstart / guides / README / artifact script: XYZ examples show query lon/lat `crs=` vs mosaic `EPSG:3857` (never describe slippy output as EPSG:4326); OSM/GIBS figure axes labelled in metres
- Design principles / how-it-works / API / troubleshooting: XYZ and slippy WMTS mosaics are labelled native `EPSG:3857` (tile-union extent); `crs=` is query-only for those paths — no longer documented as “still target” vs metadata WMTS
- Broader plain-reader docs pass: drop agent/process slogans and checklist voice from user-facing pages; demote maintainer freeze / writing-principles / building-docs pages off the published Sphinx site (kept in-repo for CONTRIBUTING / AGENTS); rename worked-examples guide; trim roadmap and duplicated CRS digressions
- Inspect how-to / API index: everyday entry is `inspect_endpoint(url)`; `inspect_capabilities` is offline / fixture body parsing only (no longer a peer “Offline / fixtures” how-to section)
- CRS / georef after native WMTS: mosaic `attrs["crs"]` follows TileMatrixSet SupportedCRS when caps metadata is present (query `crs=` may differ); README links the WMTS guide + inspect how-to; decode blurb matches default `band_policy="preserve"`; `create_array` docstring gains a full Parameters section plus an explicit CRS contract (WCS/WMS/XYZ/slippy vs native WMTS)
- how-it-works / troubleshooting / OSM guide: clipped WCS placement, band/dtype/palette contract, and coverage-pad vs soft-fail distinction (#94, #95)
- how-it-works / troubleshooting / API: plan-before-fetch helpers, cache lifecycle (`{sha}.tile` + `.meta`, warm vs cold, when to clear), and fetch summary for diagnosing host pressure (#61)
- Multi-AOI / ML sample patches how-to: per-bbox `create_array` → `compute_mosaic` → save/release loop; warn against one mosaic over the union of all sample boxes; shared host-gate vs overlapping computes; snapping/provenance, cache reuse, soft-fail vs nodata (#87)
- Process-local fetch gates when scaling up: how-it-works / troubleshooting / API note that AIMD / semaphore ceilings are shared by threads in one process, not by multi-process or distributed workers (#59)
- Worked examples guide + notebook (`docs/guides/worked-examples.md`, `notebooks/community_worked_examples.ipynb`): tile-grid AOI georef, WCS size habits, config/CRS mistakes, polite multi-tile fetch (#97)
- Design principles page: ethos, CRS roles, access vs processing, imagery vs numerical coverages; labels current single-`crs=` behaviour vs planned `bbox_crs` / native-grid output (#98)
- Document arbitrary CRS inputs on `create_array` / API / troubleshooting; EA Lidar guide notes British National Grid as a non-Mercator example (#92)
- WMTS quickstart / how-it-works / troubleshooting note native TileMatrixSet grids (load GetCapabilities or pass `matrix=`) (#93)
- WMTS usage guide (`docs/guides/wmts.md`): GIBS Web Mercator + Kartverket topo EPSG:25833 copy-paste paths; example-sources / quickstart / guides index updated; offline Kartverket capabilities contract + spatial expectations (#60)

## [0.2.0] - 2026-11-03

Trustworthy-core release: clean-env GeoTIFF install, analysis-ready mosaic attrs, one preferred compute habit, typed public errors, default tile progress, and actionable stage-gate DX — without holding the cut for multi-AOI orchestration or later uniqueness work.

### Documentation

- Maintainer stage-gate notebook (`notebooks/stage_gate_0_2.ipynb`) + run notes in `notebooks/README.md`: local editable install → live OSM / EA DSP mosaics → politeness knobs, soft-fail attrs, and a human checklist before the 0.2.0 cut
- Document private/keyed hosts via existing config `params=` / `headers=` (query keys, legacy username/password query params, Bearer/Basic headers); OS Maps XYZ recipe uses env-backed `params` instead of `?key=` in the URL template; soft-fail still hard-aborts 401 (related #58). Caps credential parity for WCS/WMTS is under Changed (#57)
- Default tile progress + quiet opt-out documented in how-it-works, troubleshooting (“looks hung”), and API index (#86)
- Troubleshooting: stage-gate symptom→cause table for wrong format / mistyped URL / bad `coverage_id`, and httpx (not `requests`) wording for caps failures (#88)

### Fixed

- Soft-fail observability: `compute_mosaic` refreshes `attrs["tilearray_tile_errors"]` (`soft_failed`, `aborted`, `examples`) from `FetchProgress` after lazy `create_array` — same counters as the eager `compute=True` path (#73)

### Breaking

- Removed `WCSConfig.for_ea_dsp` and `XYZConfig.for_openstreetmap`. Protocol configs stay on `from_url`; host politeness comes from `tilearray.fetch_presets` — compose with `WCSConfig.from_url(url, coverage_id, **ea_dsp_fetch_defaults())` and `XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=…, **osm_fetch_defaults())`. Docs, soak scripts, and tests updated to the compose path (alpha; no deprecation shim).

### Changed

- Stage-gate errors are more actionable: unsupported formats list library (and cached DescribeCoverage) MIME values; DescribeCoverage / GetCapabilities / `build_service` messages keep status or httpx type, distinguish DNS vs HTML/login vs unknown CoverageId, and redact secret query keys; soft-fail / 401 hard-abort behaviour unchanged (#88)
- WCS GeoTIFF decode now uses ``tifffile`` directly (bytes or path); dropped the ``geotiff`` → ``zarr<2.13`` / ``numcodecs<0.16`` pin cage so clean-env installs no longer need that dependency gymnastics. XYZ-without-GDAL posture unchanged (no hard rasterio/GDAL runtime dep) (#53)
- Public-boundary failures now raise the typed `TileArrayError` hierarchy instead of bare `ValueError` / `RuntimeError`: bad inputs → `ValidationError`, caps/XML parse → `ParseError`, service detection / missing decoder → `ConfigurationError`, WCS/WMTS capabilities HTTP (`httpx`) → `NetworkError`. **Compat:** callers that catch bare `ValueError` for library validation must switch to `TileArrayError` / the specific subclass (or catch both during transition). Pydantic model field checks (`BoundingBox` coordinates, etc.) still surface via pydantic as before (#72)
- WCS/WMTS capabilities and DescribeCoverage (plus direct `WCSService.get_coverage`) now use sync **httpx** with one shared UA/timeout policy and inherit config `headers`/`params` — private-server credentials reach plan-time GETs the same way they reach tiles. Drops the runtime `requests` dependency (#57)
- Docs: one compute habit on primary paths — OSM/GIBS guides, how-it-works, troubleshooting, and API index use `compute_mosaic`; `load_array` documented as eager sugar for `create_array(..., compute=True)`; quickstart WMTS honesty note (`WMTSConfig` supported, host guide TBD / #60) (#55)

### Added

- Default tqdm-style tile progress for multi-tile mosaics via existing `on_progress` / `FetchProgress` (scripts + notebooks). Quiet opt-out: `show_progress=False`, `TILEARRAY_PROGRESS=0`, or non-TTY/non-Jupyter display. Custom `on_progress=` still replaces the default bar. Runtime dependency: `tqdm`. No second compute API (#86)
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

[Unreleased]: https://github.com/MatthewJWhittle/tilearray/compare/v0.3.2...HEAD
[0.3.2]: https://github.com/MatthewJWhittle/tilearray/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/MatthewJWhittle/tilearray/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/MatthewJWhittle/tilearray/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/MatthewJWhittle/tilearray/compare/v0.1.1...v0.2.0
[0.1.1]: https://github.com/MatthewJWhittle/tilearray/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/MatthewJWhittle/tilearray/releases/tag/v0.1.0
