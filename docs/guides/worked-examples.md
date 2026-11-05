# Worked examples by job

These examples group common geo-data jobs (georeferenced mosaic, WCS edges, CRS mistakes, polite multi-tile fetch) so you can try them against public hosts.

Prefer the interactive notebook `notebooks/community_worked_examples.ipynb` in this repository (`uv sync --dev` then Jupyter). Published host guides with figures: {doc}`osm-london`, {doc}`gibs-bluemarble`, {doc}`ea-lidar`, {doc}`ea-vom-wms`, {doc}`wmts`.

Stay polite: modest bboxes, fetch presets where they exist, no bulk OSM scrape. Attribution where the host requires it.

## Georeferenced mosaic from a tile grid

Pass a bbox (and zoom / matrix) and get a spatially referenced array — not a pile of ungeoreferenced tile files.

Works on Mercator / slippy-style XYZ (and GIBS-as-XYZ) — {doc}`gibs-bluemarble` — and on native projected WMTS TileMatrixSets (e.g. Kartverket topo EPSG:25833) — {doc}`wmts`. Unknown or incomplete matrices raise {py:exc}`~tilearray.ValidationError` rather than guessing Web Mercator.

## Numerical WCS under size and edge limits

Get analysis-ready coverage values for an AOI when servers limit request size or trim at coverage edges.

Chunked EA Lidar DTM with `ea_dsp_fetch_defaults()` — {doc}`ea-lidar`. Clipped or partial GeoTIFF GetCoverage responses are placed on the request grid with NaN padding (no stretch or silent shift); mismatched resolution or CRS fails loudly.

## Config and CRS mistakes

Fail early on typos and unsupported CRS instead of a misleading remote error.

Bad CRS format (e.g. `EPGS:…`) raises {py:exc}`~tilearray.ValidationError`. Any pyproj-valid projected or geographic CRS works (`EPSG:25833`, integers, `pyproj.CRS`, …). Format / URL / `coverage_id` messages: {doc}`../troubleshooting`. Discover layer / coverage / matrix ids on an unfamiliar host with {doc}`../inspect-endpoint`.

## Polite multi-tile fetch

Run larger mosaics without overwhelming the host: visible progress, loud failure by default, opt-in soft-fill.

Fetch presets, default progress, and `soft_fail=True` / `on_tile_error=` — see {doc}`../how-it-works`. Multi-AOI / ML sample loops: per-bbox `create_array` then `compute_mosaic` with a shared `cache_dir` — {doc}`../multi-aoi-ml-patches`. Fetch gates are process-local; do not multiply them with multi-process Dask workers.
