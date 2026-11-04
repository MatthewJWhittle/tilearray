# OpenStreetMap XYZ — central London

Load a small slippy-map window, compute it, plot the mosaic, and read CRS / transform / extent.

Uses `XYZConfig.from_url(OSM_TILE_TEMPLATE, …, **osm_fetch_defaults())` (identifiable User-Agent, max 2 in-flight, ~2 req/s). Include **© OpenStreetMap contributors** when displaying the imagery.

```{note}
OSM raster tiles are often **palette PNGs**, so the mosaic may be single-band index values unless you pass `expand_palette=True`. For a full-colour RGB XYZ example see {doc}`gibs-bluemarble`.
```

## Code

```python
from tilearray import XYZConfig, compute_with_policy, create_array
from tilearray.fetch_presets import OSM_TILE_TEMPLATE, osm_fetch_defaults
from tilearray.types import CRS, Format

config = XYZConfig.from_url(
    OSM_TILE_TEMPLATE,
    zoom=16,
    output_format=Format.PNG,
    chunk_size=(256, 256),
    **osm_fetch_defaults(),
)

bbox = (-0.005, 51.495, 0.005, 51.505)  # query lon/lat near central London
da = create_array(config, bbox, CRS.EPSG_4326)
arr = compute_with_policy(da)

print(arr.shape, arr.dims, da.attrs["service_type"])
print(da.attrs["crs"], da.attrs["transform"])
print(
    float(arr.x.min()),
    float(arr.y.min()),
    float(arr.x.max()),
    float(arr.y.max()),
)
# Query crs= is lon/lat; mosaic attrs["crs"] is EPSG:3857 (tile-union metres)
arr.plot.imshow(cmap="gray", add_colorbar=False)
```

## Result

```{image} ../_static/guides/osm-london.png
:alt: OpenStreetMap mosaic over a small central London bbox (Web Mercator axes)
:width: 480px
```

Map data © OpenStreetMap contributors.

Example shape for this window: about `(1024, 512)` (tile alignment can change that). Axis units on the figure are **EPSG:3857 metres**, not degrees. Stay within OSMF tile policy — prefer the preset; do not raise concurrency past its caps.
