# WMTS — GIBS glance and Kartverket UTM33

WMTS is the same habit as XYZ/WCS: config → {py:func}`~tilearray.create_array` → {py:func}`~tilearray.compute_with_policy`. You name the layer, TileMatrixSet, and TileMatrix from GetCapabilities; tilearray plans tiles on that grid and returns a georeferenced lazy mosaic. You do **not** hand-compute tile rows/columns or reproject the downloaded rasters.

Two copy-paste paths below: a simple Web Mercator host, then a projected EPSG:25833 matrix (the awkward case that usually breaks “download tiles, hope for georef” workflows).

## Simple — NASA GIBS Blue Marble (Web Mercator)

GIBS publishes a REST GetTile template. For common GoogleMapsCompatible matrix names, planning works from the numeric TileMatrix without fetching the large GetCapabilities document.

```python
from tilearray import WMTSConfig, compute_with_policy, create_array
from tilearray.types import CRS, Format

config = WMTSConfig.from_url(
    "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/wmts.cgi",
    layer="BlueMarble_NextGeneration",
    tile_matrix_set="GoogleMapsCompatible_Level8",
    tile_matrix=3,
    style="default",
    url_template=(
        "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
        "BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/"
        "{TileMatrix}/{TileRow}/{TileCol}.jpeg"
    ),
    output_format=Format.JPEG,
    chunk_size=(256, 256),
    max_concurrent_requests=2,
    rate_limit_per_second=2.0,
    headers={
        "User-Agent": "tilearray/0.2 (+https://github.com/MatthewJWhittle/tilearray/issues)",
    },
)

bbox = (-40.0, 20.0, -10.0, 45.0)  # mid-Atlantic glance
da = create_array(config, bbox, CRS.EPSG_4326)
arr = compute_with_policy(da)
print(arr.shape, arr.dims, da.attrs["crs"])
```

```{image} ../_static/guides/gibs-bluemarble.png
:alt: NASA GIBS Blue Marble RGB mosaic over a mid-Atlantic bbox
:width: 520px
```

Imagery: NASA GIBS / BlueMarble_NextGeneration. The XYZ guide {doc}`gibs-bluemarble` uses the same host via an `{z}/{y}/{x}` template — this path uses {py:class}`~tilearray.WMTSConfig` instead.

## Projected — Kartverket topo (EPSG:25833)

Norwegian Mapping Authority cache: layer `topo`, TileMatrixSet `utm33n` (EPSG:25833). Pass a lon/lat bbox; the mosaic comes back on the **native** UTM33 grid with tile-aligned extent (often slightly larger than the query window).

```python
from tilearray import WMTSConfig, compute_with_policy, create_array
from tilearray.types import Format

config = WMTSConfig.from_url(
    "https://cache.kartverket.no/v1/service",
    layer="topo",
    tile_matrix_set="utm33n",
    tile_matrix=8,  # capabilities id is "08"; numeric 8 is fine
    style="default",
    output_format=Format.PNG,
    chunk_size=(256, 256),
    max_concurrent_requests=2,
    rate_limit_per_second=2.0,
    headers={
        "User-Agent": "tilearray/0.2 (+https://github.com/MatthewJWhittle/tilearray/issues)",
    },
)

# Small Oslo-area window in lon/lat — mosaic CRS becomes EPSG:25833
bbox = (10.6, 59.85, 10.8, 59.95)
da = create_array(config, bbox, "EPSG:4326", show_progress=False)
arr = compute_with_policy(da)
print(arr.shape, da.attrs["crs"], da.attrs.get("bbox_requested"))
print(float(arr.x.min()), float(arr.x.max()), float(arr.y.min()), float(arr.y.max()))
```

```{image} ../_static/guides/wmts-kartverket-oslo.png
:alt: Kartverket topo WMTS mosaic over a small Oslo-area bbox in EPSG:25833
:width: 480px
```

Map data © Kartverket (CC BY 4.0). Keep crawls modest. This host often returns RGBA PNGs — expect a band dimension.

What to notice:

- Output `attrs["crs"]` is **EPSG:25833** (matrix SupportedCRS), not the query CRS.
- Coordinates are pixel centres on the tile grid; the returned extent may expand to whole tiles.
- GetCapabilities is loaded automatically for this TileMatrixSet — no separate discovery step required for the copy-paste path.

## What this documents vs what is untested

| Path | Evidence |
|------|----------|
| GIBS GoogleMapsCompatible via `WMTSConfig` + REST template | Live figure; same host as the XYZ guide |
| Kartverket `topo` / `utm33n` / EPSG:25833 | Offline caps + planning contract with independent bounds; live smoke optional |
| Other Kartverket matrices (`utm32n`, `utm35n`, `webmercator`) | Same adapter; not separately cassette-locked here |
| Arbitrary WMTS hosts / exotic matrix axis quirks | Supported when GetCapabilities supplies SupportedCRS, ScaleDenominator, TopLeftCorner; verify against the host before relying on it |

Discovery helpers that list layers/matrices from an unknown URL (when available) still leave **you** to choose identifiers — tilearray will not silently pick a layer or CRS among many. Wrong ids raise {py:exc}`~tilearray.ValidationError`.

## Related

- {doc}`../example-sources` — Kartverket + GIBS endpoints
- {doc}`../quickstart` — short WMTS recipe
- {doc}`../how-it-works` — native-grid CRS / snapped extent
- {doc}`gibs-bluemarble` — same GIBS imagery via XYZ
