# Quick start

Five minutes from install to a plotted, georeferenced mosaic. Prefer {py:func}`tilearray.compute_with_policy` so Dask workers match the fetch ceiling. Visual walkthroughs: {doc}`guides/index`. More endpoints: {doc}`example-sources`.

## First run — OpenStreetMap XYZ

Install ({doc}`install`), including **matplotlib** for the plot step (`pip install matplotlib` or conda — not part of core `tilearray`), then fetch a tiny central-London window:

```python
from tilearray import XYZConfig, compute_with_policy, create_array
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
arr = compute_with_policy(da)

print(arr.shape)  # ~ (1024, 512, 3) for this window
print(da.attrs["crs"], da.attrs["bbox_crs"], da.attrs["transform"])
print(
    float(arr.x.min()),
    float(arr.y.min()),
    float(arr.x.max()),
    float(arr.y.max()),
)  # mosaic extent in metres
arr.plot.imshow(rgb="band", add_colorbar=False)
```

`bbox_crs=` is the **query** CRS (how you describe the bbox). XYZ / slippy mosaics are labelled **EPSG:3857** over the tile-union footprint — output CRS is Web Mercator, not lon/lat (`attrs["bbox_crs"]` keeps the query). Pixels are not client-warped. OSM tiles are often palette PNGs: without `expand_palette=True` the mosaic stays single-band indices (plot with `cmap="gray"`).

```{image} _static/guides/osm-london.png
:alt: OpenStreetMap mosaic over a small central London bbox
:width: 420px
```

Map data © OpenStreetMap contributors. `osm_fetch_defaults()` sets an identifiable User-Agent and polite limits (max 2 in-flight, ~2 req/s). Full guide: {doc}`guides/osm-london`.

**Next:** discover layers on an unfamiliar URL ({doc}`inspect-endpoint`), understand plan → fetch → mosaic ({doc}`how-it-works`), or try other hosts below.

## Also — other protocols

### WCS — Environment Agency Lidar

```python
from tilearray import WCSConfig, compute_with_policy, create_array
from tilearray.fetch_presets import ea_dsp_fetch_defaults
from tilearray.types import CRS, Format

wcs_url = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)
coverage_id = (
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
)

config = WCSConfig.from_url(
    wcs_url,
    coverage_id,
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(800, 800),
    grid_shape=(1, 1),
    **ea_dsp_fetch_defaults(),
)

bbox = (431900.0, 382700.0, 432700.0, 383500.0)
da = create_array(config, bbox, CRS.EPSG_27700)
elevation = compute_with_policy(da)
print(elevation.shape, float(elevation.mean()))
print(elevation.attrs["crs"], elevation.attrs["transform"][:2], elevation.attrs["nodata"])
```

The EA Lidar preset caps sustained concurrency at **10**. CoverageId comes from GetCapabilities — not the URL path slug. For WCS / WMS, `attrs["crs"]` matches `bbox_crs=`. Visual walkthrough: {doc}`guides/ea-lidar`.

### WMS — Environment Agency Vegetation Object Model

```python
from tilearray import WMSConfig, compute_with_policy, create_array
from tilearray.types import CRS, Format

# Confirm the year-suffixed layer name via GetCapabilities before hard-coding
config = WMSConfig.from_url(
    "https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms",
    layers="Vegetation_Object_Model_2022",
    output_format=Format.PNG,
    chunk_size=(512, 512),
    grid_shape=(1, 1),
)

# Approximate OSGB window (British National Grid)
bbox = (430000.0, 380000.0, 431000.0, 381000.0)
da = create_array(config, bbox, CRS.EPSG_27700)
img = compute_with_policy(da)
# Transparent GetMap PNG → RGBA (4 bands)
print(img.shape, da.attrs["service_type"])
```

No WMS fetch preset — stay polite with modest `grid_shape` / concurrency. See {doc}`example-sources` for layer quirks.

### WMTS — Kartverket topo (EPSG:25833)

```python
from tilearray import WMTSConfig, compute_with_policy, create_array
from tilearray.types import Format

config = WMTSConfig.from_url(
    "https://cache.kartverket.no/v1/service",
    layer="topo",
    tile_matrix_set="utm33n",
    tile_matrix=8,
    style="default",
    output_format=Format.PNG,
    chunk_size=(256, 256),
    max_concurrent_requests=2,
    rate_limit_per_second=2.0,
    headers={
        "User-Agent": "tilearray/0.3 (+https://github.com/MatthewJWhittle/tilearray/issues)",
    },
)

# Lon/lat AOI → native UTM33 mosaic (tile-aligned extent may expand)
da = create_array(config, bbox=(10.6, 59.85, 10.8, 59.95), bbox_bbox_crs="EPSG:4326")
arr = compute_with_policy(da)
print(arr.shape, da.attrs["crs"])  # mosaic CRS is EPSG:25833, not the query CRS
```

Non–Web-Mercator TileMatrixSets load GetCapabilities automatically (or accept an explicit `matrix=`). The mosaic is labelled with the matrix SupportedCRS and tile-aligned extent. Visual walkthrough: {doc}`guides/wmts`. Unknown hosts: {doc}`inspect-endpoint`.

Many noncontiguous sample boxes (ML chips): {doc}`multi-aoi-ml-patches` — one `create_array` per bbox, not one mosaic over the union of all boxes.

## Private or keyed hosts

Same habit: config → `create_array` → `compute_with_policy`. Pass static credentials on the config with `params=` (query API keys, legacy username/password query params) or `headers=` (Bearer, API-key headers, Basic). Prefer env-backed `params=` over secrets in XYZ URL templates. Details and the WCS/WMTS capabilities caveat: {doc}`troubleshooting`. Keyed OS Maps XYZ: {doc}`example-sources`.

## Large mosaics — `compute_with_policy`

For multi-tile lazy mosaics, `compute_with_policy` reads fetch policy from `da.attrs` automatically:

```python
from tilearray import WCSConfig, compute_with_policy, create_array
from tilearray.fetch_presets import ea_dsp_fetch_defaults
from tilearray.types import CRS, Format

config = WCSConfig.from_url(
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs",
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m",
    bbox_crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(128, 128),
    resolution=(1.0, 1.0),
    **ea_dsp_fetch_defaults(),
)

bbox = (418_000.0, 451_400.0, 419_024.0, 452_424.0)
da = create_array(config, bbox, CRS.EPSG_27700)
mean_elevation = compute_with_policy(da.mean())
print(float(mean_elevation))
```
