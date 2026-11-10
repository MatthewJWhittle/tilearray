# Quick start

Five minutes from install to a plotted, georeferenced OpenStreetMap mosaic. Prefer {py:func}`tilearray.compute_mosaic` so Dask thread workers match the fetch ceiling. Visual walkthrough: {doc}`guides/osm-london`.

```{note}
This path needs **network** access to the OSM tile host. If the fetch fails (403, timeout, offline), see {doc}`troubleshooting` or try another public host from {doc}`example-sources`.
```

## First run — OpenStreetMap XYZ

Install ({doc}`install`), including **matplotlib** for the plot step (`pip install matplotlib` or conda — not part of core `tilearray`), then fetch a tiny central-London window:

```python
from tilearray import XYZConfig, compute_mosaic, create_array
from tilearray.fetch_presets import OSM_TILE_TEMPLATE, osm_fetch_defaults
from tilearray.types import CRS

config = XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=16, **osm_fetch_defaults())

# Query bbox in lon/lat
da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),
    bbox_crs=CRS.EPSG_4326,
    expand_palette=True,  # full-colour RGB; omit for single-band palette indices
)
arr = compute_mosaic(da)

print(arr.shape)  # ~ (1024, 512, 3) for this window
print(da.attrs["mosaic_crs"], da.attrs["bbox_crs"], da.attrs["transform"])
print(
    float(arr.x.min()),
    float(arr.y.min()),
    float(arr.x.max()),
    float(arr.y.max()),
)  # mosaic extent in metres
# PNG/JPEG mosaics default to uint8 (plot-ready for xarray rgb=)
arr.plot.imshow(rgb="band", add_colorbar=False)
```

**Success check:** shape about `(1024, 512, 3)`, `attrs["mosaic_crs"]` is `EPSG:3857`, and the plot shows a London street mosaic. Axis units are metres, not degrees.

```{image} _static/guides/osm-london.png
:alt: OpenStreetMap mosaic over a small central London bbox
:width: 420px
```

Map data © OpenStreetMap contributors. `osm_fetch_defaults()` sets an identifiable User-Agent and polite limits (max 2 in-flight, ~2 req/s). OSM tiles are often palette PNGs: without `expand_palette=True` the mosaic stays single-band indices (plot with `cmap="gray"`).

## CRS cheat sheet (three roles)

| Role | What it means here | Where you see it |
|------|--------------------|------------------|
| **Query CRS** | How you describe the bbox | `bbox_crs=` on `create_array` → `attrs["bbox_crs"]` |
| **Service / request CRS** | CRS the host expects on the wire | Config `crs=` / matrix SupportedCRS (provider-side) |
| **Mosaic CRS** | CRS of the returned pixels + transform | `attrs["mosaic_crs"]` |

For this XYZ example the query is lon/lat (`EPSG:4326`) and the mosaic is **Web Mercator** (`EPSG:3857`) over the tile-union footprint. Pixels are not client-warped. Always pass the query CRS as keyword **`bbox_crs=`**. More detail: {doc}`user-terms`, {doc}`design-principles`.

## Next steps

| Job | Go to |
|-----|-------|
| Same OSM path with more commentary | {doc}`guides/osm-london` |
| Other hosts (WCS / WMS / WMTS / GIBS) | {doc}`guides/index` |
| Unknown URL → pick layer / CoverageId | {doc}`inspect-endpoint` |
| What happens on `create_array` | {doc}`how-it-works` |
| Host ceilings / soft-fail / scaling | {doc}`polite-fetch` |
| Fetch failed or CRS looks wrong | {doc}`troubleshooting` |
| Many noncontiguous sample boxes | {doc}`multi-aoi-ml-patches` |
| Private or keyed hosts | {doc}`troubleshooting` · {doc}`example-sources` |
