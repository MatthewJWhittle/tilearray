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

## WMTS

WMTS is supported via {py:class}`tilearray.WMTSConfig` (REST or KVP GetTile, same `create_array` → `compute_with_policy` path as WCS/WMS/XYZ). A copy-paste host guide is not published yet — configure with `WMTSConfig.from_url(...)` and follow the XYZ/WMS recipes above for compute habit and politeness.

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
