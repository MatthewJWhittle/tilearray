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
        "User-Agent": "tilearray/0.3 (+https://github.com/MatthewJWhittle/tilearray/issues)",
    },
)

bbox = (-40.0, 20.0, -10.0, 45.0)  # query lon/lat
da = create_array(config, bbox, CRS.EPSG_4326)
arr = compute_with_policy(da)
print(arr.shape, arr.dims, da.attrs["crs"])  # mosaic CRS is EPSG:3857
```

```{image} ../_static/guides/gibs-bluemarble.png
:alt: NASA GIBS Blue Marble RGB mosaic over a mid-Atlantic bbox (Web Mercator axes)
:width: 520px
```

Imagery: NASA GIBS / BlueMarble_NextGeneration. Slippy / GoogleMapsCompatible mosaics are labelled **EPSG:3857** over the tile union — `crs=` is query-only. The XYZ guide {doc}`gibs-bluemarble` uses the same host via an `{z}/{y}/{x}` template — this path uses {py:class}`~tilearray.WMTSConfig` instead.

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
        "User-Agent": "tilearray/0.3 (+https://github.com/MatthewJWhittle/tilearray/issues)",
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

## Choose a TileMatrix from a target resolution

Callers often know a familiar spacing (for example ~10 m in a metre CRS) as **CRS units per pixel**, not the server's TileMatrix id or OGC ScaleDenominator. Use {py:func}`~tilearray.choose_matrix_for_resolution` on levels from {py:func}`~tilearray.inspect_endpoint` (or `MatrixSetSummary.levels`), then pass the chosen id into `WMTSConfig`. Comparison is on resolution; ScaleDenominator is best-effort when present (levels with only `resolution` are accepted). Web Mercator uses Mercator CRS units — not ground metres. tilearray does **not** pick a matrix inside `create_array`, and it does **not** resample pixels to the target resolution — related: native-matrix planning (#93), plan-before-fetch (#61).

```python
from tilearray import (
    WMTSConfig,
    choose_matrix_for_resolution,
    create_array,
    inspect_endpoint,
    plan_array,
)
from tilearray.types import Format

inspection = inspect_endpoint("https://cache.kartverket.no/v1/service")
matrix_set = next(m for m in inspection.matrix_sets if m.identifier == "utm33n")
choice = choose_matrix_for_resolution(
    matrix_set.levels,
    target_resolution=10.0,  # CRS units per pixel in the matrix CRS (metres here)
    policy="coarser",  # or "nearest" / "finer"
    crs=matrix_set.crs,
)

config = WMTSConfig.from_url(
    "https://cache.kartverket.no/v1/service",
    layer="topo",
    tile_matrix_set="utm33n",
    tile_matrix=choice.identifier,
    output_format=Format.PNG,
    chunk_size=(256, 256),
)
plan = plan_array(
    config,
    bbox=(10.6, 59.85, 10.8, 59.95),
    bbox_crs="EPSG:4326",
)
print(choice.identifier, choice.resolution)
print(plan.matrix_id, plan.planned_pixel_size, plan.crs, plan.bbox_crs)
# Then: create_array → compute_with_policy as usual. Same-grid trim (no
# interpolation) is ordinary xarray slicing on the native mosaic — not a warp.
```

Levels need a usable `resolution` (or a ScaleDenominator plus `crs=` to derive one). Levels with neither are skipped; if none remain, {py:exc}`~tilearray.ValidationError` is raised.

## What this documents vs what is untested

| Path | Evidence |
|------|----------|
| GIBS GoogleMapsCompatible via `WMTSConfig` + REST template | Live figure; same host as the XYZ guide |
| Kartverket `topo` / `utm33n` / EPSG:25833 | Offline caps + planning contract with independent bounds; live smoke optional |
| Other Kartverket matrices (`utm32n`, `utm35n`, `webmercator`) | Same adapter; not separately cassette-locked here |
| Arbitrary WMTS hosts / exotic matrix axis quirks | Supported when GetCapabilities supplies SupportedCRS, ScaleDenominator, TopLeftCorner; verify against the host before relying on it |

{doc}`../inspect-endpoint` lists layers/matrices from an unknown URL, but still leaves **you** to choose identifiers — tilearray will not silently pick a layer or CRS among many. Wrong ids raise {py:exc}`~tilearray.ValidationError`.

## Related

- {doc}`../example-sources` — Kartverket + GIBS endpoints
- {doc}`../quickstart` — short WMTS recipe
- {doc}`../inspect-endpoint` — discover layers / matrices before hard-coding
- {doc}`../how-it-works` — native-grid CRS / snapped extent
- {doc}`gibs-bluemarble` — same GIBS imagery via XYZ
