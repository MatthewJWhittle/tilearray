# Design principles

**Canonical product and API design for tilearray.** Other docs should link here rather than restate a second copy.

**Core promise:** turn remote tiled geospatial data into reliably georeferenced, lazy [xarray](https://docs.xarray.dev/) arrays, without requiring users to implement service protocols or depend on GDAL.

tilearray is a **data-access library**, not a raster-processing or GIS analysis suite. Inspiration: tidyverse-style conceptual clarity (semantic names, progressive disclosure, composition) — not R syntax.

---

## Ethos

1. **Semantic, readable API** — names express intent; one concept, one name. Do not overload `crs` with incompatible meanings or let `**kwargs` become a hidden second interface.
2. **Simple defaults, progressive disclosure** — common paths stay concise Python; advanced provider controls stay available without dominating the happy path.
3. **Composition over framework-building** — return ordinary xarray/Dask objects that work with other libraries; do not invent a parallel analysis or orchestration ecosystem.
4. **Correctness and predictability before convenience** — no silent raster reprojection, resampling, band changes, value alteration, guessed tile matrices, or falsely labelled CRS. Fail with an actionable error when correctness cannot be established.
5. **Clear boundaries** — own service discovery/configuration, request planning, **coordinate transformation of query extents**, server-aware fetching, decoding, and assembling correctly labelled arrays. Delegate warping/reprojection, resampling, terrain analysis, model training, and file export to established tools.
6. **Transparent behaviour** — a request has understandable contracts: area requested vs area returned; query CRS vs service/request CRS vs output CRS; native pixel size; tile snapping; dtype/bands/nodata; provenance.
7. **GDAL-free by default, pragmatic dependencies** — supported paths must not require GDAL or a large GIS stack; do not pretend every dependency is pure Python, and do not forbid optional integrations later.
8. **Responsible remote access** — respect rate/size limits, provider terms, authentication and secrets; avoid request storms when parallelising. Host ceilings live in presets and compose onto configs (`from_url` + `fetch_presets`); do not grow `for_*` host methods on protocol configs. Fetch concurrency gates are **process-local** — document multi-process / distributed limits honestly rather than promising a global host ceiling the library does not enforce.
9. **Evidence-led scope** — ground work in real host jobs via worked examples, not a unit-test-per-scenario harness. Resist unproven protocols, a provider catalogue, complex orchestration, and broad GIS functionality.

---

## Spatial contract (CRS roles)

Keep these concepts separate in types, docs, and tests:

| Role | Meaning | Suggested vocabulary |
|------|---------|----------------------|
| **Bounding-box / query CRS** | How the caller describes *where* to get data | Prefer an unambiguous name such as `bbox_crs` |
| **Service / request CRS** | CRS used in the HTTP request to the provider | Distinct from the caller's convenience CRS when they differ |
| **Output / array CRS** | Actual spatial reference of returned pixels (correct transform + pixel-centre coords) | Must not be assumed equal to the query CRS |

### Default behaviour (target design)

- Callers may describe an AOI in a convenient CRS (including arbitrary supported EPSG codes). tilearray transforms **request boundaries** to select necessary source tiles (densify when required).
- For fixed tile grids (WMTS / XYZ), return **native-grid** pixels in their true CRS, with the smallest suitable pixel/tile-aligned extent covering the request. Report the actual returned extent and any snapping/cropping; it need not equal the original bbox.
- **No implicit client-side raster reprojection or resampling.** Same-grid trim/pad without interpolation can be appropriate when correctly aligned.
- For WMS / WCS, a provider may generate data in another requested CRS. If exposed, that is **explicit server-side** transformation; returned metadata must match what the server produced. Do not introduce a local warp engine.
- Invalid/unsupported CRS or grid, or ambiguous georeferencing → actionable failure, not a plausible-looking wrong array.

### Current alpha behaviour (honest contract)

Today `create_array(..., crs=)` is a **single** public CRS argument that interprets the bbox. It accepts any pyproj-valid CRS (presets or EPSG strings such as `EPSG:25833`), not a fixed four-value enum. Mosaic georef comes from the planned bbox + shape (`transform_source="mosaic_bbox"`); per-tile GeoTIFF tags never silently replace mosaic CRS. There is still **no** client-side warp of pixels onto a different CRS.

**Labelling today:** for WCS / WMS / XYZ / slippy WMTS, `attrs["crs"]` is that same caller `crs=`. For **WMTS with TileMatrixSet metadata**, the mosaic is labelled with the matrix SupportedCRS and a tile-aligned extent (query bbox may differ). A separate public `bbox_crs=` for every protocol remains **target** design — do not treat the full three-role API as already shipped, but do not document native-WMTS output CRS as unshipped either.

Prefer a clean documented contract over preserving an ambiguous `crs` forever; any migration will be explicit during alpha.

### Imagery vs numerical coverages

| Kind | Typical path | Expect |
|------|--------------|--------|
| **Rendered imagery** (XYZ / WMS / many WMTS) | JPEG/PNG (often palette) | Visual tiles; bands may be RGB or single-band palette; nodata often omitted |
| **Numerical coverages** (WCS elevation, etc.) | GeoTIFF | Analysis values; elevation path maps nodata → NaN; preserve dtype/band honesty |

Do not treat a colourful mosaic as proof of numerical fidelity, or elevation NaN rules as imagery defaults.

---

## Scope and non-goals

**In scope:** polite concurrent fetch and assemble of WCS / WMS / WMTS / XYZ into Dask-backed xarray; plan-time bind of fetch/error/decode behaviour; accurate mosaic metadata; progressive disclosure via policies and presets.

**Out of scope (through ~0.3, see {doc}`roadmap`):** STAC/COG cube builders; map SDKs / tile servers; async-first rewrite; kitchen-sink auth frameworks; full temporal cube API; provider catalogue chase; local GIS processing (warp, resample, terrain, ML training, export).

---

## Trade-off rules

| Prefer | Over |
|--------|------|
| One clear path (`config` / preset → `create_array` → `compute_with_policy`) | Dual habits or parallel helpers for the same job |
| Hard-fail + abort after retries | Silent NaN holes as default “helpfulness” |
| Plan-time bind of load behaviour | Post-plan monkeypatch / global loader install |
| Host ceiling from presets; call-local lower limits | Shrinking the shared process gate for one polite call |
| Fix the library | Documenting caller workarounds (pre-round every bbox, …) |
| Design note before new public knobs / default semantics | Expanding `__all__` or defaults in a “quick” bugfix |
| Additive alpha changes + CHANGELOG honesty | Silent behaviour flips |

---

## Design review questions

Before adding a new public option or adapter:

1. What user job does it solve?
2. Is there already a consistent concept/name for it?
3. Can an ordinary user predict the array’s true CRS, extent, bands, and fetch cost?
4. Does it force tilearray to become a GIS processing library?
5. Can it be tested offline with independent expectations?
6. Are lossy transformations (if any) **explicit**, with accurate metadata and useful errors?
7. Does the change keep GDAL-free defaults and host politeness intact?

Engineering standards for contributors live in [guidelines.md](https://github.com/MatthewJWhittle/tilearray/blob/main/guidelines.md) and [AGENTS.md](https://github.com/MatthewJWhittle/tilearray/blob/main/AGENTS.md). Product ethos stays here.

**When writing user-facing docs** (Sphinx, README, usage guides), apply the cut filter in {doc}`docs-writing-principles` — reader jobs only; keep release-process and agent/PR chatter out of Start-here paths. Do not restate that filter here.
