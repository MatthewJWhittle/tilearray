# Problem-shaped examples

Public WMTS / WCS / WMS threads keep asking the same *jobs*. These examples are organized by **problem class** so you can drive whether tilearray helps — not as a one-to-one replay of every forum post.

Prefer the interactive notebook `notebooks/community_worked_examples.ipynb` in this repository (`uv sync --dev` then Jupyter). Published host recipes with figures: {doc}`osm-london`, {doc}`gibs-bluemarble`, {doc}`ea-lidar`.

Stay polite: modest bboxes, fetch presets where they exist, no bulk OSM scrape. Attribution where the host requires it.

## 1. Tile-grid AOI → georeferenced mosaic

**Job:** bbox (+ zoom) → spatially referenced array, not ungeoreferenced tile files.

**Works today** on Mercator / slippy-style XYZ (and GIBS-as-XYZ). Example: {doc}`gibs-bluemarble`.

**Still open:** native projected WMTS matrices and careful WGS84 TileMatrix maths ([#92](https://github.com/MatthewJWhittle/tilearray/issues/92), [#93](https://github.com/MatthewJWhittle/tilearray/issues/93), [#60](https://github.com/MatthewJWhittle/tilearray/issues/60)).

## 2. Numerical WCS under size / edge habits

**Job:** analysis-ready coverage values for an AOI when servers limit request size or trim at coverage edges.

**Works today** for chunked EA Lidar DTM with `ea_dsp_fetch_defaults()` — {doc}`ea-lidar`.

**Still open:** clipped GetCoverage that must pad/align onto the request grid without stretching ([#95](https://github.com/MatthewJWhittle/tilearray/issues/95)).

## 3. Config / CRS mistakes

**Job:** fail early on typos and unsupported CRS instead of a misleading remote error.

**Works today** for bad CRS *format* (e.g. `EPGS:…` → {py:exc}`~tilearray.ValidationError`). Many valid EPSG codes are still outside the four-value enum ([#92](https://github.com/MatthewJWhittle/tilearray/issues/92)). Format / URL / `coverage_id` messages: {doc}`../troubleshooting`.

## 4. Polite multi-tile fetch

**Job:** larger mosaics without stampeding the host; visible progress; loud failure by default; opt-in soft-fill.

**Works today** via fetch presets, default progress, and `soft_fail=True` / `on_tile_error=` — see {doc}`../how-it-works`. Multi-AOI: ordinary bbox loop + shared `cache_dir` is enough for now ([#87](https://github.com/MatthewJWhittle/tilearray/issues/87)).

## Honesty rule

A community post that *inspired* an example is not automatic proof that tilearray “fixes” that post. Treat notebook runs and these guides as evidence you can inspect, then file gaps on the owning issues.
