# NASA GIBS XYZ — Blue Marble

Load a small mid-Atlantic window from NASA GIBS as JPEG RGB tiles. Uses `XYZConfig.from_url` with a template where the path is `{z}/{y}/{x}` (row before column) — not the OSM `{z}/{x}/{y}` order.

Query the AOI in lon/lat; the mosaic is labelled **EPSG:3857** (native Web Mercator tile union), same contract as other XYZ / slippy sources.

## Code

```python
from tilearray import XYZConfig, compute_mosaic, create_array
from tilearray.types import CRS, Format

template = (
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
    "BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/"
    "{z}/{y}/{x}.jpeg"
)

config = XYZConfig.from_url(
    template,
    zoom=3,
    output_format=Format.JPEG,
    chunk_size=(256, 256),
    max_concurrent_requests=2,
    rate_limit_per_second=2.0,
    headers={
        "User-Agent": "tilearray/0.1 (+https://github.com/MatthewJWhittle/tilearray/issues)",
    },
)

bbox = (-40.0, 20.0, -10.0, 45.0)  # query lon/lat
da = create_array(config, bbox, CRS.EPSG_4326)
arr = compute_mosaic(da)
print(arr.shape, arr.dims)  # expect (y, x, band) for RGB JPEG
print(da.attrs["crs"], da.attrs["bbox_crs"], da.attrs["transform"])  # EPSG:3857 mosaic
# Query bbox_crs= is lon/lat; mosaic attrs["crs"] is EPSG:3857 (tile-union metres)
# PNG/JPEG mosaics default to uint8 (plot-ready for xarray rgb=)
arr.plot.imshow(rgb="band", add_colorbar=False)
```

```{note}
Plotting needs matplotlib (`pip install matplotlib` — not a core tilearray dependency); see {doc}`../install`.
```

## Result

```{image} ../_static/guides/gibs-bluemarble.png
:alt: NASA GIBS Blue Marble RGB mosaic over a mid-Atlantic bbox (Web Mercator axes)
:width: 520px
```

Imagery: NASA GIBS / BlueMarble_NextGeneration. Keep crawls modest; coordinate with GIBS for very large jobs.
