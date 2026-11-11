# EA Vegetation Object Model — WMS

WMS layers are often year-suffixed and return transparent PNG GetMap responses as **RGBA** — easy to hard-code the wrong year or expect three bands. This guide loads a small British National Grid window and plots the mosaic.

Uses `WMSConfig.from_url` with a layer id from GetCapabilities (confirm the year before hard-coding). No WMS fetch preset — stay polite with modest `grid_shape` / concurrency.

```{note}
Plotting needs matplotlib (`pip install matplotlib` — not a core tilearray dependency); see {doc}`../install`.
```

## Code

```python
from tilearray import WMSConfig, compute_mosaic, create_array
from tilearray.types import CRS, Format

# Confirm the year-suffixed layer name via GetCapabilities before hard-coding
config = WMSConfig.from_url(
    "https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms",
    layers="Vegetation_Object_Model_2022",
    output_format=Format.PNG,
    chunk_size=(512, 512),
    grid_shape=(1, 1),
    max_concurrent_requests=2,
    rate_limit_per_second=2.0,
)

# ~512 m OSGB window (British National Grid)
bbox = (430000.0, 380000.0, 430512.0, 380512.0)
da = create_array(config, bbox, bbox_crs=CRS.EPSG_27700)
img = compute_mosaic(da)

print(img.shape, da.attrs["service_type"])  # often (y, x, band) with 4 bands
print(da.attrs["mosaic_crs"], da.attrs["bbox_crs"], da.attrs["transform"])
# PNG/JPEG mosaics default to uint8 (plot-ready for xarray rgb=)
img.plot.imshow(rgb="band", add_colorbar=False)
```

`bbox_crs=` is the query CRS. For WMS, mosaic `attrs["mosaic_crs"]` matches that query (no client-side warp).

## Result

```{image} ../_static/guides/ea-vom-wms.png
:alt: EA Vegetation Object Model WMS mosaic over a small OSGB window
:width: 480px
```

**Success check:** shape often `(y, x, band)` with 4 bands (RGBA), `attrs["mosaic_crs"]` matches the OSGB query CRS, and the plot shows the vegetation mosaic. Contains public sector information licensed under the Open Government Licence v3.0. Layer quirks and more hosts: {doc}`../example-sources`.
