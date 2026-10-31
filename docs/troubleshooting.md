# Troubleshooting

Short answers for the failures that show up first with public hosts. Background: {doc}`how-it-works`. Endpoints: {doc}`example-sources`.

## Prefer `compute_with_policy`

Lazy `create_array` only fetches when you compute. Plain `.compute()` uses Dask's default thread pool (often ~CPU count), which can leave fetch headroom unused. Prefer:

```python
from tilearray import compute_with_policy

result = compute_with_policy(da)          # or compute_with_policy(da.mean())
```

`create_array` stores fetch policy on `da.attrs`; `compute_with_policy` reads it automatically. You do not need to pass `max_concurrent` by hand.

## Environment Agency WCS

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| Empty / invalid coverage | CoverageId ≠ URL path slug | Use the GetCapabilities id (e.g. `UUID__Lidar_Composite_Elevation_DTM_1m`), or `WCSConfig.for_ea_dsp` with the id from {doc}`example-sources` |
| Many 403s / hard fail mid-mosaic | Burst above the EA ceiling | Keep AIMD ceiling at **10** (the EA preset). Do not raise `max_concurrent_requests` for county-scale cold mosaics |
| Slow multi-tile mosaic | Dask workers << fetch ceiling | Use `compute_with_policy` (or `create_array(..., compute=True)`) |

## OpenStreetMap XYZ

- Stay on `XYZConfig.for_openstreetmap` (identifiable User-Agent, max 2 in-flight, ~2 req/s).
- Palette PNGs often decode as **single-band grayscale**, not RGB. A small London window at z16 is about `(1024, 512)`, not `(256, 256)`.
- Attribute © OpenStreetMap contributors when displaying imagery.

## NASA GIBS XYZ

- Template order is `{z}/{y}/{x}` (row before column), unlike OSM `{z}/{x}/{y}`.
- Wrong order yields empty or scrambled mosaics — copy the template from {doc}`example-sources` / {doc}`guides/gibs-bluemarble`.

## WMS (EA Vegetation Object Model)

- Layer names are year-suffixed (`Vegetation_Object_Model_2022`, …). Confirm the year in GetCapabilities before hard-coding.
- Transparent PNG GetMap responses decode to **RGBA (4 bands)**.

## Failures are loud

After retries, a failed tile raises {py:exc}`tilearray.NetworkError`. Mosaics do **not** succeed with silent NaN holes; sibling in-flight tile fetches abort when the session fails hard.

## Disk cache

Pass `cache_dir=` to `create_array` / `load_array` to reuse tile bytes across runs. Warm reads sniff the body when response headers lie (e.g. multipart WCS). Clear the directory if you change decode expectations or suspect corrupt cache entries.

## Custom endpoints

Use `WCSConfig.from_url` / `WMSConfig.from_url` / `WMTSConfig.from_url` / `XYZConfig.from_url` and tune `FetchPolicy` / config knobs (`max_concurrent_requests`, `rate_limit_per_second`, AIMD fields). Presets exist only for known host quirks (EA DSP WCS, OSMF XYZ) — they are not a catalogue of every public layer.
