# WMTS — Kartverket topo (EPSG:25833)

Load a small Oslo window from the Norwegian Mapping Authority WMTS cache: name the **service**, **layer**, **TileMatrixSet** (product / native CRS), and a familiar **ground spacing**. tilearray inspects GetCapabilities, picks a TileMatrix for that resolution, attaches any REST template the host advertises, and returns a georeferenced lazy mosaic on the **native** matrix grid. You do **not** hand-write URL templates, ScaleDenominator, or tile row/column maths — and tiles are not client-side reprojected.

```{note}
Plotting needs matplotlib (`pip install matplotlib` — not a core tilearray dependency); see {doc}`../install`.
```

## Code

```python
from tilearray import compute_mosaic, create_array, inspect_endpoint, preview_array

# Service URL from the host docs (KVP / GetCapabilities entry point)
inspection = inspect_endpoint("https://cache.kartverket.no/v1/service")
print(inspection)  # layers, TileMatrixSets, CRS — confirm ids before hard-coding

config = inspection.build_config(
    layer="topo",
    tile_matrix_set="utm33n",  # native EPSG:25833 product
    target_resolution=10.0,  # metres / pixel in the matrix CRS
    resolution_policy="coarser",  # never oversample; or "nearest" / "finer"
    style="default",  # this host requires a named style
)

bbox = (10.6, 59.85, 10.8, 59.95)  # query lon/lat near Oslo
preview = preview_array(config, bbox, bbox_crs="EPSG:4326")
print(preview.matrix_id, preview.planned_pixel_size, preview.mosaic_crs, preview.bbox_crs)

da = create_array(config, bbox, bbox_crs="EPSG:4326", show_progress=False)
arr = compute_mosaic(da)
print(arr.shape, da.attrs["mosaic_crs"], da.attrs.get("bbox_requested"))
print(float(arr.x.min()), float(arr.x.max()), float(arr.y.min()), float(arr.y.max()))
# PNG/JPEG mosaics default to uint8 (plot-ready for xarray rgb=)
arr.plot.imshow(rgb="band", add_colorbar=False)
```

## Result

```{image} ../_static/guides/wmts-kartverket-oslo.png
:alt: Kartverket topo WMTS mosaic over a small Oslo-area bbox in EPSG:25833
:width: 480px
```

Map data © Kartverket (CC BY 4.0). Keep crawls modest. This host often returns RGBA PNGs — expect a band dimension.

What to notice:

- You stated **job terms**: service, layer, matrix set, ~10 m spacing, AOI + `bbox_crs=`. Wire details (REST template, ScaleDenominator, matrix id `08`) stay inside inspect / `build_config`.
- Output `attrs["mosaic_crs"]` is **EPSG:25833** (matrix SupportedCRS), not the query CRS. `attrs["bbox_crs"]` keeps the lon/lat query.
- Coordinates are pixel centres on the tile grid; the returned extent may expand to whole tiles.
- `target_resolution` selects a native TileMatrix — tilearray does **not** resample pixels to that spacing. Same-grid trim is ordinary xarray slicing.

## Advanced — explicit matrix id or custom template

When you already know the TileMatrix id (or the host has no usable resolution metadata), pass `tile_matrix=` instead of `target_resolution=`:

```python
config = inspection.build_config(
    layer="topo",
    tile_matrix_set="utm33n",
    tile_matrix=8,  # capabilities id "08"; numeric 8 is fine
    style="default",
)
```

Or skip inspect and construct {py:class}`~tilearray.WMTSConfig` directly (`from_url` still auto-loads GetCapabilities for non–Web-Mercator matrices). Pass `url_template=` only when you must override the ResourceURL from capabilities (unusual). For a Web Mercator glance at the same GIBS imagery without WMTS discovery, prefer the XYZ guide {doc}`gibs-bluemarble`.

Low-level helper {py:func}`~tilearray.choose_matrix_for_resolution` still works on `MatrixSetSummary.levels` if you want the choice object without `build_config`. Fetch politeness (`max_concurrent_requests`, User-Agent, …) is progressive disclosure on the config — compose presets when a host needs them.

## What this documents vs what is untested

| Path | Evidence |
|------|----------|
| Kartverket `topo` / `utm33n` via inspect → `target_resolution` → mosaic | Live figure; offline caps + planning contract |
| Explicit `tile_matrix=` / optional `url_template=` | Same adapter; advanced path above |
| Other Kartverket matrices (`utm32n`, `utm35n`, `webmercator`) | Same adapter; not separately cassette-locked here |
| Arbitrary WMTS hosts / exotic matrix axis quirks | Supported when GetCapabilities supplies SupportedCRS, ScaleDenominator, TopLeftCorner; verify against the host before relying on it |

{doc}`../inspect-endpoint` lists layers / matrix sets from an unknown URL. tilearray will not silently pick a layer or TileMatrixSet among many — wrong ids raise {py:exc}`~tilearray.ValidationError`.

## Related

- {doc}`../example-sources` — Kartverket + GIBS endpoints
- {doc}`../quickstart` — short WMTS recipe
- {doc}`../inspect-endpoint` — discover layers / matrices before hard-coding
- {doc}`../how-it-works` — native-grid CRS / snapped extent
- {doc}`gibs-bluemarble` — same GIBS imagery via XYZ (no WMTS discovery)
