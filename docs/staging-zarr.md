# Persist a mosaic to Zarr safely

Interrupted large writes can leave a Zarr store that looks usable but is only partially filled. Use {py:func}`~tilearray.staging` so the **final path appears only after a clean write** — callers still use xarray's `to_zarr`; tilearray owns the incomplete→complete close.

Assumes a working `create_array` → `compute_mosaic` path ({doc}`quickstart`). Tile-byte resume across runs is still {doc}`troubleshooting` (`cache_dir=`); that is separate from persist publish.

## Whole-store rewrite

```python
from tilearray import create_array, compute_mosaic, staging

da = create_array(config, bbox, bbox_crs=..., cache_dir="./tiles")
da = compute_mosaic(da)  # or write lazily inside staging

with staging("out.zarr") as path:
    da.to_dataset(name="elevation").to_zarr(path, mode="w")
# clean exit → atomically publish to out.zarr
# exception / kill → out.zarr absent
```

Or keep the mosaic lazy and compute as part of the write:

```python
with staging("out.zarr") as path:
    delayed = da.to_dataset(name="elevation").to_zarr(
        path, mode="w", compute=False
    )
    compute_mosaic(delayed)
```

**Reader check for this MVP:** if `out.zarr` exists, the write finished; if it is missing, do not treat a sibling `out.zarr.partial` (if present) as complete.

## What staging is not

- Not a first-party Zarr / COG writer — you still call `Dataset.to_zarr` (or, later, `rio.to_raster`).
- Not region / append resume — whole-store `mode="w"` only for now.
- Not a substitute for `cache_dir=` when you want to reuse **tile fetches** after a crash.

Raster / COG (`kind="raster"`) is not implemented yet; use the Zarr path above.

## Soft-fail vs incomplete write

Under soft-fail, a **finished** store may still contain intentional NaN holes. That is content semantics, not an incomplete write. Path presence answers write-job completion; soft-fail counters on the array describe mosaic holes after compute.

Next: {doc}`how-it-works` · {doc}`troubleshooting` · {doc}`api/index`
