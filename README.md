# <img src="https://matthewjwhittle.github.io/tilearray/_static/logo/tilearray-mark-128.png" alt="" width="36" height="36"> tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays — one `create_array` call for WCS, WMS, WMTS, or XYZ.

**Why.** Pulling a large AOI from OGC or XYZ tiles usually means hand-rolling capabilities parsing, tile planning, polite concurrent HTTP, decode, and mosaic georef. That glue (OWSLib + Pillow + Affine maths, or a GDAL/VRT path) is brittle — wrong CRS labels, stretched transforms, and host 403s are common. tilearray owns **plan → fetch → decode → assemble** so you get an analysis-ready array without a custom client per protocol.

**Who.** Geo data engineers who need remote tiles into xarray/Dask for plotting, analysis, or ML chips — on a laptop or a larger machine — with host-aware concurrency.

**Fit.** Data **access**, not GIS processing. Use [rioxarray](https://corteva.github.io/rioxarray/) / GDAL / rasterio for local rasters, warping, and analysis; use tilearray when the source is still a tile service. Non-goals: map SDKs, STAC/COG cube builders, silent client-side warp, provider catalogues — see [design principles](https://matthewjwhittle.github.io/tilearray/design-principles.html).

[![Tests](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml)
[![Docs](https://github.com/MatthewJWhittle/tilearray/actions/workflows/docs.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/docs.yml)
[![Build and Publish](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml)

> **Alpha.** The API may change. Today **WCS**, **WMS**, **WMTS**, and **XYZ** share one `create_array` entry point.

**Docs:** [matthewjwhittle.github.io/tilearray](https://matthewjwhittle.github.io/tilearray/) — [install](https://matthewjwhittle.github.io/tilearray/install.html), [quick start](https://matthewjwhittle.github.io/tilearray/quickstart.html) (five-minute first run), [how it works](https://matthewjwhittle.github.io/tilearray/how-it-works.html), [usage guides](https://matthewjwhittle.github.io/tilearray/guides/index.html), [example sources](https://matthewjwhittle.github.io/tilearray/example-sources.html), [troubleshooting](https://matthewjwhittle.github.io/tilearray/troubleshooting.html), [design principles](https://matthewjwhittle.github.io/tilearray/design-principles.html), and [API](https://matthewjwhittle.github.io/tilearray/api/index.html).

## Install

Python **3.9+**. Prefer [uv](https://github.com/astral-sh/uv):

```bash
uv add tilearray
```

or:

```bash
pip install tilearray
```

Plotting (`arr.plot.imshow`) needs **matplotlib**, which is not a core dependency:

```bash
pip install matplotlib
```

(or `conda install matplotlib` / `uv add matplotlib`). From a clone: `uv sync --dev` (includes matplotlib via `dev`). Contributors: `make docs` (HTML), `make docs-artifacts` (regenerate figures).

## Quick start

Prefer **`compute_with_policy`** over plain `.compute()` so Dask workers match the fetch ceiling (attrs are read automatically).

### First run — OpenStreetMap XYZ

```python
from tilearray import XYZConfig, compute_with_policy, create_array
from tilearray.fetch_presets import OSM_TILE_TEMPLATE, osm_fetch_defaults
from tilearray.types import CRS

config = XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=16, **osm_fetch_defaults())
da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),  # query lon/lat
    bbox_crs=CRS.EPSG_4326,
    expand_palette=True,  # full-colour RGB; omit for single-band palette indices
)
arr = compute_with_policy(da)
print(arr.shape, da.attrs["crs"], da.attrs["transform"])
# Mosaic is EPSG:3857 (Web Mercator tile union), not lon/lat — crs= is query only
arr.plot.imshow(rgb="band", add_colorbar=False)
```

OSM tiles are often palette PNGs: `expand_palette=True` expands to RGB; the default keeps indices. Include © OpenStreetMap contributors when displaying map imagery. Full plotted walkthrough: [quick start](https://matthewjwhittle.github.io/tilearray/quickstart.html) / [OSM guide](https://matthewjwhittle.github.io/tilearray/guides/osm-london.html).

### Also — WCS (Environment Agency Lidar)

```python
from tilearray import WCSConfig, compute_with_policy, create_array
from tilearray.fetch_presets import ea_dsp_fetch_defaults
from tilearray.types import CRS, Format

config = WCSConfig.from_url(
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs",
    "13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m",
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(800, 800),
    grid_shape=(1, 1),
    **ea_dsp_fetch_defaults(),
)
bbox = (431900.0, 382700.0, 432700.0, 383500.0)  # British National Grid
da = create_array(config, bbox, CRS.EPSG_27700)
elevation = compute_with_policy(da)
print(elevation.shape, float(elevation.mean()), elevation.attrs["crs"])
```

EA Lidar fetch defaults (`tilearray.fetch_presets.ea_dsp_fetch_defaults`) cap sustained concurrency at **10**. WMTS: [host guide](https://matthewjwhittle.github.io/tilearray/guides/wmts.html) (GIBS + Kartverket — mosaic CRS follows the TileMatrixSet, not necessarily your query `crs=`). Unknown endpoints: [inspect](https://matthewjwhittle.github.io/tilearray/inspect-endpoint.html) → choose ids → `build_config`.

## Development

Clone, `uv sync --dev`, then `make test` / `make lint`. Full setup, docs builds, and project conventions: [CONTRIBUTING.md](CONTRIBUTING.md). Agents: [AGENTS.md](AGENTS.md) → [docs/design-principles.md](docs/design-principles.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
