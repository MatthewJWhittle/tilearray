# Quick start

These examples hit **live** public hosts. Prefer {py:func}`tilearray.compute_with_policy` so Dask workers match the fetch ceiling. For plotted walkthroughs, see {doc}`guides/index`. More endpoints: {doc}`example-sources`.

## XYZ — OpenStreetMap

```python
from tilearray import XYZConfig, compute_with_policy, create_array
from tilearray.fetch_presets import OSM_TILE_TEMPLATE, osm_fetch_defaults
from tilearray.types import CRS

config = XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=16, **osm_fetch_defaults())

da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),  # small window near central London
    crs=CRS.EPSG_4326,
)
arr = compute_with_policy(da)
# Palette PNGs → single-band; this window is about (1024, 512)
print(arr.shape, da.attrs["service_type"])
```

`osm_fetch_defaults()` sets an identifiable User-Agent and polite limits (max 2 in-flight, ~2 req/s). Include © OpenStreetMap contributors when displaying map imagery.

## WCS — Environment Agency Lidar

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

The EA Lidar preset caps sustained concurrency at **10**. CoverageId comes from GetCapabilities — not the URL path slug. Mosaic attrs carry CRS / Affine `transform` / NaN `nodata` for a simple rioxarray handoff ({doc}`how-it-works`).

## WMS — Environment Agency Vegetation Object Model

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

## WMTS — Kartverket topo (EPSG:25833)

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
        "User-Agent": "tilearray/0.2 (+https://github.com/MatthewJWhittle/tilearray/issues)",
    },
)

# Lon/lat AOI → native UTM33 mosaic (tile-aligned extent may expand)
da = create_array(config, bbox=(10.6, 59.85, 10.8, 59.95), crs="EPSG:4326")
arr = compute_with_policy(da)
print(arr.shape, da.attrs["crs"])  # CRS is EPSG:25833
```

Non–Web-Mercator TileMatrixSets load GetCapabilities automatically (or accept an explicit `matrix=`). The mosaic is labelled with the matrix SupportedCRS and tile-aligned extent. Visual walkthrough + simpler GIBS WMTS recipe: {doc}`guides/wmts`. To discover layer / TileMatrixSet / matrix ids on an unfamiliar host, use {doc}`inspect-endpoint`.

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
    crs=CRS.EPSG_27700,
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
