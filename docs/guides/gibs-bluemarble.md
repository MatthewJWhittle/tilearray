# NASA GIBS XYZ — Blue Marble

Load a small mid-Atlantic window from NASA GIBS as JPEG RGB tiles. Uses `XYZConfig.from_url` with a template where the path is `{z}/{y}/{x}` (row before column) — not the OSM `{z}/{x}/{y}` order.

## Code

```python
from tilearray import XYZConfig, create_array
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

bbox = (-40.0, 20.0, -10.0, 45.0)
da = create_array(config, bbox, CRS.EPSG_4326, compute=True)
print(da.shape, da.dims)  # expect (y, x, band) for RGB JPEG
```

## Result

```{image} ../_static/guides/gibs-bluemarble.png
:alt: NASA GIBS Blue Marble RGB mosaic over a mid-Atlantic bbox
:width: 520px
```

Imagery: NASA GIBS / BlueMarble_NextGeneration. Keep crawls modest; coordinate with GIBS for very large jobs.
