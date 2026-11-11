# EA Lidar WCS — elevation + analysis

Rendered map tiles are pictures; WCS Lidar is a **numerical** elevation coverage (GeoTIFF, nodata → NaN). This guide fetches a small British National Grid window, plots elevation, and summarises the distribution.

Uses `WCSConfig.from_url(..., **ea_dsp_fetch_defaults())` (AIMD under a hard ceiling, pressure circuit breaker). Keep large cold mosaics at or below the preset ceiling — higher limits can trip gateway pressure.

## Code

```python
from tilearray import WCSConfig, compute_mosaic, create_array
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
    chunk_size=(64, 64),
    resolution=(1.0, 1.0),
    **ea_dsp_fetch_defaults(),
)

# ~256 m OSGB window (British National Grid — not Web Mercator)
bbox = (431900.0, 382700.0, 432156.0, 382956.0)

# Presets or any pyproj-valid EPSG string work the same way
da = create_array(config, bbox, bbox_crs=CRS.EPSG_27700)  # or bbox_crs="EPSG:27700"
elevation = compute_mosaic(da)  # match Dask workers to fetch ceiling
print(elevation.shape, float(elevation.mean()))
print(da.attrs["mosaic_crs"], da.attrs["bbox_crs"], da.attrs["transform"])
elevation.plot.imshow(cmap="terrain", add_colorbar=True)
```

`create_array` stays lazy until compute. For multi-tile mosaics, prefer {py:func}`tilearray.compute_mosaic` over plain `.compute()`. Plotting needs matplotlib (`pip install matplotlib` — not a core tilearray dependency); see {doc}`../install`.

## Result

```{image} ../_static/guides/ea-lidar-elevation.png
:alt: EA Lidar elevation map and histogram for a small OSGB window
:width: 720px
```

**Success check:** shape `(256, 256)`, finite mean elevation (about 176 m for this window — see histogram), `attrs["mosaic_crs"]` is `EPSG:27700`. More public endpoints: {doc}`../example-sources`.
