# Multi-AOI / ML sample patches

Fetch **many small, noncontiguous bboxes** (training chips, sample sites) without building one country-wide mosaic.

**Job:** loop `create_array` per bbox → `compute_with_policy` → save → release. Stay polite under the shared host ceiling. Do **not** union distant boxes into one `bbox=`.

Background: {doc}`how-it-works`. Copy-paste hosts: {doc}`quickstart`.

---

## One bbox → one mosaic

Each `create_array` call plans and assembles **one** contiguous mosaic for its bbox. Distant UK (or other) sample sites are separate arrays — not one empty-heavy megagrid.

```text
boxes → [create_array(config, b, crs, ...) for b in boxes]   # lazy list is fine
for da in lazy:
    arr = compute_with_policy(da)
    save_patch(arr)   # GeoTIFF / zarr / your training record
    del arr
```

Planning twenty graphs is cheap. HTTP cost appears at compute. Prefer **compute one (or a small batch) at a time**, write to disk, and drop the array so RAM stays bounded.

---

## Recipe — noncontiguous WMTS patches

Same habit as XYZ/WMS: shared config + per-bbox `create_array` → `compute_with_policy`. Below uses three separated Web Mercator windows at one matrix level (swap host / layer / matrix for your service).

```python
from pathlib import Path

from tilearray import WMTSConfig, compute_with_policy, create_array
from tilearray.types import CRS, Format

# Shared host identity + politeness (same FetchPolicy / process gate)
config = WMTSConfig.from_url(
    "https://example.com/wmts",
    layer="example_layer",
    tile_matrix_set="EPSG:3857",
    tile_matrix=12,
    output_format=Format.PNG,
    chunk_size=(256, 256),
    max_concurrent_requests=2,  # host ceiling — do not raise “because 20 jobs”
    rate_limit_per_second=2.0,
    cache_dir=Path("tile-cache"),  # optional: reuse tiles across overlapping revisits
)

# Noncontiguous sample boxes (EPSG:4326). Do NOT replace with their union bbox.
boxes = [
    (-0.14, 51.50, -0.12, 51.51),  # London patch
    (-1.55, 53.38, -1.53, 53.39),  # Sheffield patch
    (-3.20, 55.94, -3.18, 55.95),  # Edinburgh patch
]

out_dir = Path("patches")
out_dir.mkdir(exist_ok=True)

lazy = [
    create_array(config, bbox, CRS.EPSG_4326, show_progress=False)
    for bbox in boxes
]

for i, da in enumerate(lazy):
    arr = compute_with_policy(da)
    # Predictable handoff: request crs=, mosaic Affine, pixel centres, service provenance
    assert arr.attrs["crs"] == CRS.EPSG_4326.value
    assert arr.attrs["transform_source"] == "mosaic_bbox"
    assert arr.attrs["service_type"] == "wmts"
    # Optional: arr.rio.write_crs / write_transform — see how-it-works
    arr.to_netcdf(out_dir / f"patch_{i:02d}.nc")  # or your ML writer
    del arr
```

For a live XYZ walkthrough of the same compute habit, see {doc}`guides/osm-london`. WCS elevation patches use the same loop with `WCSConfig` + a numerical coverage (NaN `nodata` on elevation GeoTIFF — imagery JPEG/PNG usually omits nodata).

---

## Politeness — shared host gate

Same host preset / same `FetchPolicy.cache_key` → **one process-wide concurrency gate**.

| How you compute | What happens |
|-----------------|--------------|
| **Sequential** `compute_with_policy` per patch | Each mosaic can use the full ceiling when idle — polite and simple. |
| **Overlapping** computes (thread pool of mosaics, or `dask.compute` on several) | Mosaics **contend** on the shared semaphore — still polite to the host, slower per patch. |

Do **not**:

- Raise the shared ceiling because you have “20 jobs”
- Set `FetchPolicy(max_concurrent=1)` (or a raised-then-shared override) to “be polite” on one call — that serialises siblings. Prefer a lower `max_concurrent_requests=` on that `create_array` (call-local) or `call_max_concurrent` — see the name map in {doc}`how-it-works`
- Union every sample into one megabbox “for convenience”

---

## Dask-friendly without an orchestrator

A **list of lazy mosaics** is already a set of ordinary Dask graphs. You can:

1. Build the list (plan only)
2. Compute / save as patches become ready (sequential loop above, or a small concurrent batch)
3. Keep IO vs CPU balance in *your* scheduler — tilearray does not ship a multi-AOI job runner

Overlapping computes stay under the shared gate automatically. Future multi-machine work must still respect host politeness (no stampede); that is separate from this recipe.

There is **no** `map_aois` helper today. Prefer this loop until a documented friction needs a thin sugar API (design note first).

---

## Snapping, shape, and provenance

| Expectation | Contract today |
|-------------|----------------|
| **CRS** | Mosaic `attrs["crs"]` is the `create_array(..., crs=)` you passed — not a silent retag from per-tile tags |
| **Extent / snap** | Returned mosaic covers the planned / snapped grid for that bbox; it need not equal the input box pixel-for-pixel on fixed WMTS/XYZ grids |
| **Coords** | `y`/`x` are **pixel centres** consistent with `attrs["transform"]` (`transform_source="mosaic_bbox"`, north-up) |
| **Shape** | Known at plan time from chunk/grid (or inferred WMTS/XYZ tile coverage); distant patches do **not** share one pixel grid unless you align them yourself |
| **Provenance** | `attrs["service_type"]`, fetch-policy summary, optional coverage/layer identity — use these when writing training metadata |

Alpha still uses one `crs=` for bbox + mosaic label. Separate query vs output CRS is **target** design — {doc}`design-principles`. Do not assume client-side warp to an arbitrary patch size.

---

## Cache, soft-fail, and nodata

- **`cache_dir=`** on the shared config warms tiles across overlapping revisits and re-runs. Non-overlapping sites still pay cold fetches; cache does not turn twenty sites into one mosaic.
- **Default** remains hard-fail + abort after retries. Opt in with `soft_fail=True` / `on_tile_error=` only when training can tolerate NaN holes — and keep auth failures hard. Soft-fail attrs are **per mosaic** (`tilearray_tile_errors`).
- **Nodata:** elevation GeoTIFF → NaN `nodata` / `_FillValue`. Rendered WMTS/XYZ JPEG/PNG usually **omit** nodata. Soft-fill holes are not the same as coverage nodata.

Symptom table: {doc}`troubleshooting`.

---

## Related

- {doc}`how-it-works` — plan → polite fetch → lazy mosaic; concurrency name map
- {doc}`troubleshooting` — hung compute, politeness mix-ups, CRS handoff
- {doc}`guides/index` — live-host visual guides (single AOI)
- Roadmap scale themes (AOI preflight / distributed don’t-stampede) stay separate; this page is the near-term multi-site recipe only
