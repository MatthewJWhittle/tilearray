# Quick start

These snippets match the README. They talk to **live** public hosts — run them locally when you intend to hit the network. Docs vignettes stay offline; see {doc}`vignettes/index`.

## XYZ slippy-map tiles

```python
from tilearray import XYZConfig, create_array
from tilearray.types import CRS

config = XYZConfig.for_openstreetmap(zoom=16)

da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),  # small bbox near central London
    crs=CRS.EPSG_4326,
)
print(da.shape, da.attrs["service_type"])
# Still lazy until you compute — prefer compute_with_policy for multi-tile mosaics
```

`XYZConfig.for_openstreetmap()` sets an identifiable User-Agent and polite fixed limits (max 2 in-flight, ~2 req/s). Include © OpenStreetMap contributors attribution when displaying map imagery.

## WCS coverage (Environment Agency Lidar)

```python
from tilearray import create_array
from tilearray.service import WCSConfig
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
elevation = da.compute()
print(elevation.shape, float(elevation.mean()))
```

More endpoints live in [`example-sources.md`](https://github.com/MatthewJWhittle/tilearray/blob/main/example-sources.md) in the repository.

## Large mosaics — `compute_with_policy`

For multi-tile lazy mosaics, prefer {py:func}`tilearray.compute_with_policy` over plain `.compute()` so Dask worker count matches the AIMD fetch ceiling:

```python
from tilearray import compute_with_policy, create_array
from tilearray.service import WCSConfig
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
