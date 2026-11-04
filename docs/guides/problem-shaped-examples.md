# Worked examples by job

These examples group common geo-data jobs (georeferenced mosaic, WCS edges, CRS mistakes, polite multi-tile fetch) so you can try them against public hosts.

Prefer the interactive notebook `notebooks/community_worked_examples.ipynb` in this repository (`uv sync --dev` then Jupyter). Published host recipes with figures: {doc}`osm-london`, {doc}`gibs-bluemarble`, {doc}`ea-lidar`, {doc}`wmts`.

Stay polite: modest bboxes, fetch presets where they exist, no bulk OSM scrape. Attribution where the host requires it.

## 1. Tile-grid AOI → georeferenced mosaic

**Job:** bbox (+ zoom) → spatially referenced array, not ungeoreferenced tile files.

**Works today** on Mercator / slippy-style XYZ (and GIBS-as-XYZ) — {doc}`gibs-bluemarble` — and on native projected WMTS TileMatrixSets (e.g. Kartverket topo EPSG:25833) — {doc}`wmts`. Unknown / incomplete matrices still raise {py:exc}`~tilearray.ValidationError` rather than guessing Web Mercator.

## 2. Numerical WCS under size / edge habits

**Job:** analysis-ready coverage values for an AOI when servers limit request size or trim at coverage edges.

**Works today** for chunked EA Lidar DTM with `ea_dsp_fetch_defaults()` — {doc}`ea-lidar`. Clipped / partial GeoTIFF GetCoverage responses are placed on the request grid with NaN padding (no stretch / silent shift); mismatched resolution or CRS fails loudly.

## 3. Config / CRS mistakes

**Job:** fail early on typos and unsupported CRS instead of a misleading remote error.

**Works today** for bad CRS *format* (e.g. `EPGS:…` → {py:exc}`~tilearray.ValidationError`) and for any pyproj-valid projected/geographic CRS (`EPSG:25833`, integers, `pyproj.CRS`, …). Invalid / unparseable codes still fail early. Format / URL / `coverage_id` messages: {doc}`../troubleshooting`. Discover layer / coverage / matrix ids on an unfamiliar host with {doc}`../inspect-endpoint`.

## 4. Polite multi-tile fetch

**Job:** larger mosaics without overwhelming the host; visible progress; loud failure by default; opt-in soft-fill.

**Works today** via fetch presets, default progress, and `soft_fail=True` / `on_tile_error=` — see {doc}`../how-it-works`. Multi-AOI / ML sample loops: per-bbox `create_array` → `compute_with_policy` with a shared `cache_dir` — {doc}`../multi-aoi-ml-patches`. Fetch gates are process-local; do not multiply them with multi-process Dask workers.

## Scope

These recipes show what works on the named hosts today. Gaps and host quirks still belong as issues once you have a failing run to cite.

