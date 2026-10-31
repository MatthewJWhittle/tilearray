# Quick start

These examples hit **live** public hosts. Prefer {py:func}`tilearray.compute_with_policy` so Dask workers match the fetch ceiling. For plotted walkthroughs, see {doc}`guides/index`. More endpoints: {doc}`example-sources`.

## XYZ — OpenStreetMap

```python
from tilearray import XYZConfig, compute_with_policy, create_array
from tilearray.types import CRS

config = XYZConfig.for_openstreetmap(zoom=16)

da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),  # small window near central London
    crs=CRS.EPSG_4326,
)
arr = compute_with_policy(da)
# Palette PNGs → single-band; this window is about (1024, 512)
print(arr.shape, da.attrs["service_type"])
```

The OSM preset sets an identifiable User-Agent and polite limits (max 2 in-flight, ~2 req/s). Include © OpenStreetMap contributors when displaying map imagery.

## WCS — Environment Agency Lidar

```python
from tilearray import WCSConfig, compute_with_policy, create_array
from tilearray.types import CRS, Format

wcs_url = (
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs"
)
coverage_id = (
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m"
)

config = WCSConfig.for_ea_dsp(
    wcs_url,
    coverage_id=coverage_id,
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(800, 800),
    grid_shape=(1, 1),
)

bbox = (431900.0, 382700.0, 432700.0, 383500.0)
da = create_array(config, bbox, CRS.EPSG_27700)
elevation = compute_with_policy(da)
print(elevation.shape, float(elevation.mean()))
```

The EA Lidar preset caps sustained concurrency at **10**. CoverageId comes from GetCapabilities — not the URL path slug.

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

## Large mosaics — `compute_with_policy`

For multi-tile lazy mosaics, `compute_with_policy` reads fetch policy from `da.attrs` automatically:

```python
from tilearray import WCSConfig, compute_with_policy, create_array
from tilearray.types import CRS, Format

config = WCSConfig.for_ea_dsp(
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs",
    coverage_id="13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m",
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(128, 128),
    resolution=(1.0, 1.0),
)

bbox = (418_000.0, 451_400.0, 419_024.0, 452_424.0)
da = create_array(config, bbox, CRS.EPSG_27700)
mean_elevation = compute_with_policy(da.mean())
print(float(mean_elevation))
```
