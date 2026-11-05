# EA Vegetation Object Model — WMS

Load a small British National Grid window from the Environment Agency Vegetation Object Model (WMS), compute it, and plot the mosaic.

Uses `WMSConfig.from_url` with a year-suffixed layer id from GetCapabilities (confirm the year before hard-coding). No WMS fetch preset — stay polite with modest `grid_shape` / concurrency. Transparent GetMap PNGs often decode as **RGBA** (4 bands).

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
da = create_array(config, bbox, CRS.EPSG_27700)
img = compute_mosaic(da)

print(img.shape, da.attrs["service_type"])  # often (y, x, band) with 4 bands
print(da.attrs["crs"], da.attrs["bbox_crs"], da.attrs["transform"])
# PNG/JPEG mosaics default to uint8 (plot-ready for xarray rgb=)
img.plot.imshow(rgb="band", add_colorbar=False)
```

`bbox_crs=` is the query CRS. For WMS, mosaic `attrs["crs"]` matches that query (no client-side warp).

## Result

```{image} ../_static/guides/ea-vom-wms.png
:alt: EA Vegetation Object Model WMS mosaic over a small OSGB window
:width: 480px
```

Contains public sector information licensed under the Open Government Licence v3.0. Layer quirks and more hosts: {doc}`../example-sources`. Short recipe: {doc}`../quickstart`.
