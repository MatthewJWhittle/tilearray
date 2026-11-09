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
8. **Responsible remote access** — respect rate/size limits, provider terms, authentication and secrets; avoid request storms when parallelising. Host ceilings live in presets and compose onto configs (`from_url` + `fetch_presets`); do not grow `for_*` host methods on protocol configs. Fetch concurrency gates are **process-local** — document multi-process / distributed limits explicitly rather than promising a global host ceiling the library does not enforce.
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

### Current alpha behaviour

`create_array(..., bbox_crs=)` is the **query** CRS (how the bbox is described). The old overloaded `crs=` argument on `create_array` / `preview_array` was removed. Any pyproj-valid CRS is accepted (presets or EPSG strings such as `EPSG:25833`). A `BoundingBox` whose CRS conflicts with `bbox_crs=` fails loud — no silent reproject of conflicting declarations. Mosaic georef comes from the planned bbox + shape (`transform_source="mosaic_bbox"`); per-tile GeoTIFF tags never silently replace mosaic CRS. There is still **no** client-side warp of pixels onto a different CRS. `attrs["bbox_crs"]` always records the query CRS; `attrs["crs"]` is the **actual** pixel-grid CRS.

**Labelling today:**

| Protocol | Request / service CRS | Output / `attrs["crs"]` |
|----------|----------------------|-------------------------|
| **WCS / WMS** | Config `crs=` (defaults to `bbox_crs`) drives subsetting / GetMap BBOX | Optional server-side `output_crs=` (WCS `outputCRS` / WMS GetMap CRS). When omitted, equals the request CRS. Unsupported values fail. |
| **XYZ / slippy WMTS** | N/A (query only) | Always `EPSG:3857` tile-union extent; `bbox_requested` keeps the caller's window. `output_crs=` is **rejected**. |
| **WMTS with TileMatrixSet** | Matrix SupportedCRS | Native matrix CRS + tile-aligned extent; query may differ. `output_crs=` is **rejected**. |

Service config `crs=` remains the request-default field — not the create/preview query argument.

### Imagery vs numerical coverages

| Kind | Typical path | Expect |
|------|--------------|--------|
| **Rendered imagery** (XYZ / WMS / many WMTS) | JPEG/PNG (often palette) | Visual tiles; bands may be RGB or single-band palette; nodata often omitted |
| **Numerical coverages** (WCS elevation, etc.) | GeoTIFF | Analysis values; elevation path maps nodata → NaN; preserve returned dtype and bands |

Do not treat a colourful mosaic as proof of numerical fidelity, or elevation NaN rules as imagery defaults.

---

## Scope and non-goals

**In scope:** polite concurrent fetch and assemble of WCS / WMS / WMTS / XYZ into Dask-backed xarray; plan-time bind of fetch/error/decode behaviour; accurate mosaic metadata; progressive disclosure via policies and presets.

**Out of scope (through ~0.3, see {doc}`roadmap`):** STAC/COG cube builders; map SDKs / tile servers; async-first rewrite; kitchen-sink auth frameworks; full temporal cube API; provider catalogue chase; local GIS processing (warp, resample, terrain, ML training, export).

---

## Trade-off rules

| Prefer | Over |
|--------|------|
| One clear path (`config` / preset → `create_array` → `compute_mosaic`) | Dual habits or parallel helpers for the same job |
| Hard-fail + abort after retries | Silent NaN holes as default “helpfulness” |
| Plan-time bind of load behaviour | Post-plan monkeypatch / global loader install |
| Host ceiling from presets; call-local lower limits | Shrinking the shared process gate for one polite call |
| Fix the library | Documenting caller workarounds (pre-round every bbox, …) |
| Design note before new public knobs / default semantics | Expanding `__all__` or defaults in a “quick” bug fix |
| Additive alpha changes + documented CHANGELOG behaviour changes | Silent behaviour flips |

Engineering standards for contributors live in [guidelines.md](https://github.com/MatthewJWhittle/tilearray/blob/main/guidelines.md) and [AGENTS.md](https://github.com/MatthewJWhittle/tilearray/blob/main/AGENTS.md). Product ethos stays here.
