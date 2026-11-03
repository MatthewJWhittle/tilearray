# Troubleshooting

Short answers for the failures that show up first with public hosts. Background: {doc}`how-it-works`. Endpoints: {doc}`example-sources`.

## Prefer `compute_with_policy`

Lazy `create_array` only fetches when you compute. Plain `.compute()` uses Dask's default thread pool (often ~CPU count), which can leave fetch headroom unused. Prefer:

```python
from tilearray import compute_with_policy

result = compute_with_policy(da)          # or compute_with_policy(da.mean())
```

`create_array` stores fetch policy on `da.attrs`; `compute_with_policy` reads it automatically. You do not need to pass `max_concurrent` by hand.

## CRS / attrs / rioxarray handoff

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| “Wrong” CRS vs GeoTIFF tags | Mosaic CRS is the **request** `crs=`, not tags inside tiles | Pass the CRS you planned with; see {doc}`how-it-works` georef contract |
| Plot looks flipped | Expecting south-up rows | Arrays are **north-up** (row 0 = north); do not flip for display |
| Missing `nodata` on RGB | Imagery path omits elevation nodata | Expected for JPEG/PNG; elevation GeoTIFF sets `nodata`/`_FillValue` to NaN |
| Need rasterio/rioxarray write | Optional downstream | Use the rioxarray recipe in {doc}`how-it-works` § Downstream use (`write_crs` / `write_transform` / `write_nodata` from attrs) |

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

## Politeness soak evidence

County-scale / multi-tile politeness is checked on a **split lane** (see {doc}`how-it-works`):

- Nightly: `pytest -m soak` (also tagged `@net`/`@slow`) — modest cold mosaics under EA + OSM ceilings; deselected on PRs.
- Maintainer: `uv run python scripts/soak_large_aoi_presets.py` — larger AOIs for 0.3-scale evidence; not a PR job.

If a nightly soak fails with many 403s, check you did not raise concurrency past the preset. If a maintainer large soak fails mid-mosaic, prefer the preset ceiling and `compute_with_policy` before blaming the host.

## Failures are loud (by default)

After retries, a failed tile raises {py:exc}`tilearray.NetworkError`. Mosaics do **not** succeed with silent NaN holes; sibling in-flight tile fetches abort when the session fails hard. Capabilities / DescribeCoverage HTTP failures on the WCS/WMTS `requests` path also raise {py:exc}`tilearray.NetworkError`.

Bad inputs and setup mistakes raise other {py:exc}`tilearray.TileArrayError` subclasses (`ValidationError`, `ParseError`, `ConfigurationError`) rather than bare `ValueError` / `RuntimeError`. Catch the hierarchy (or a subclass), not bare builtins, when handling library failures.

Opt in to visible soft-fill with plan-time ``on_tile_error=soft_fail_exhausted_tiles`` (or ``soft_fail=True``). Soft-fill runs **only after** fetcher retries finish, never calls ``FetchProgress.abort``, and records failures in ``progress.errors`` / ``attrs["tilearray_tile_errors"]``. Auth (401), exhausted 429, and decode/library errors still hard-abort under the preset; a soft-fail fraction above ``soft_fail_max_fraction`` (default ``0.1``) also aborts.

## Disk cache

Pass `cache_dir=` to `create_array` / `load_array` to reuse tile bytes across runs. Warm reads sniff the body when response headers lie (e.g. multipart WCS). Clear the directory if you change decode expectations or suspect corrupt cache entries.

## Private / keyed hosts

Every `*Config` already accepts `params=` (extra query) and `headers=` (extra HTTP headers) — same path as public hosts. Put credentials there at plan time; there is no separate auth client.

| Need | Put it on |
|------|-----------|
| API key / token query (`key=`, `api_key=`, …) | `params={"key": …}` |
| Legacy username + password as query params | `params={"username": …, "password": …}` (vendor names vary) |
| Bearer / API-key header / HTTP Basic | `headers={"Authorization": "Bearer …"}` (or `X-API-Key`, `Basic …`) |

Prefer `params=` / `headers=` over baking secrets into XYZ URL templates (`?key=…` or `{key}` placeholders). Load values from the environment or a secret store; never commit keys. Soft-fail does **not** paper over bad credentials — auth **401** still hard-aborts (see above).

Config `params=` / `headers=` also reach WCS/WMTS **GetCapabilities** / **DescribeCoverage** (same shared httpx policy as tiles) so private WCS can plan without a second credential path (#57). Keyed XYZ recipe: {doc}`example-sources`.

## Custom endpoints

Use `WCSConfig.from_url` / `WMSConfig.from_url` / `WMTSConfig.from_url` / `XYZConfig.from_url` and tune `FetchPolicy` / config knobs (`max_concurrent_requests`, `rate_limit_per_second`, AIMD fields). Pass credentials via `params=` / `headers=` (see above). Presets exist only for known host quirks (EA DSP WCS, OSMF XYZ) — they are not a catalogue of every public layer.
