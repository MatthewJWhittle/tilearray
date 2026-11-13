# <img src="https://matthewjwhittle.github.io/tilearray/_static/logo/tilearray-mark-128.png" alt="" width="36" height="36"> tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays — one `create_array` call for WCS, WMS, WMTS, or XYZ.

**Why.** Pulling a large area of interest (AOI) — the window you ask for — from OGC or XYZ tiles usually means hand-rolling capabilities parsing, tile planning, polite concurrent HTTP, decode, and mosaic georef. That glue is brittle — wrong CRS labels, stretched transforms, and host 403s are common. tilearray owns **plan → fetch → decode → assemble** so you get an analysis-ready array without a custom client per protocol.

**Who.** Geo data engineers who need remote tiles into xarray/Dask for plotting, analysis, or ML chips — on a laptop or a larger machine — with host-aware concurrency.

**Fit.** Data **access**, not GIS processing. Use [rioxarray](https://corteva.github.io/rioxarray/) / GDAL / rasterio for local rasters, warping, and analysis; use tilearray when the source is still a tile service. Non-goals: map SDKs, STAC/COG cube builders, silent client-side warp, provider catalogues — see [design principles](https://matthewjwhittle.github.io/tilearray/design-principles.html).

[![Tests](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml)
[![Docs](https://github.com/MatthewJWhittle/tilearray/actions/workflows/docs.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/docs.yml)
[![Build and Publish](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml)

> **Alpha — documented against 0.3.2.** Examples use `compute_mosaic`, `preview_array`, `bbox_crs=`, and mosaic attrs `mosaic_crs` / `bbox_crs`. The API may still change. Today **WCS**, **WMS**, **WMTS**, and **XYZ** share one `create_array` entry point.

**Docs:** [matthewjwhittle.github.io/tilearray](https://matthewjwhittle.github.io/tilearray/) — [install](https://matthewjwhittle.github.io/tilearray/install.html), [quick start](https://matthewjwhittle.github.io/tilearray/quickstart.html) (five-minute OSM first run), [usage guides](https://matthewjwhittle.github.io/tilearray/guides/index.html), [how it works](https://matthewjwhittle.github.io/tilearray/how-it-works.html), [troubleshooting](https://matthewjwhittle.github.io/tilearray/troubleshooting.html), and [API](https://matthewjwhittle.github.io/tilearray/api/index.html).

## Install

Python **3.9+**. Prefer [uv](https://github.com/astral-sh/uv):

```bash
uv add tilearray
```

or:

```bash
pip install tilearray
```

Live-host examples need network access. Plotting (`arr.plot.imshow`) needs **matplotlib**, which is not a core dependency:

```bash
pip install matplotlib
```

(or `conda install matplotlib` / `uv add matplotlib`). From a clone: `uv sync --dev` (includes matplotlib via `dev`). Contributors: `make docs` (HTML), `make docs-artifacts` (regenerate figures).

## Quick start — OpenStreetMap XYZ

Prefer **`compute_mosaic`** over plain `.compute()` so Dask workers match the fetch ceiling (attrs are read automatically).

```python
from tilearray import XYZConfig, compute_mosaic, create_array
from tilearray.fetch_presets import OSM_TILE_TEMPLATE, osm_fetch_defaults
from tilearray.types import CRS

config = XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=16, **osm_fetch_defaults())
da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),  # query lon/lat
    bbox_crs=CRS.EPSG_4326,
    expand_palette=True,  # full-colour RGB; omit for single-band palette indices
)
arr = compute_mosaic(da)
print(arr.shape, da.attrs["mosaic_crs"], da.attrs["transform"])
# Mosaic is EPSG:3857 (Web Mercator tile union), not lon/lat — bbox_crs= is query only
# PNG/JPEG mosaics default to uint8 (plot-ready for xarray rgb=)
arr.plot.imshow(rgb="band", add_colorbar=False)
```

**Success:** shape about `(1024, 512, 3)`, mosaic CRS `EPSG:3857`, London street mosaic on the plot. OSM tiles are often palette PNGs: `expand_palette=True` expands to RGB. Include © OpenStreetMap contributors when displaying map imagery.

Full plotted walkthrough + CRS cheat sheet: [quick start](https://matthewjwhittle.github.io/tilearray/quickstart.html). Other hosts (WCS / WMS / WMTS / GIBS): [usage guides](https://matthewjwhittle.github.io/tilearray/guides/index.html). Unknown endpoints: [inspect](https://matthewjwhittle.github.io/tilearray/inspect-endpoint.html).

## Development

Clone, `uv sync --dev`, then `make test` / `make lint`. Full setup, docs builds, and project conventions: [CONTRIBUTING.md](CONTRIBUTING.md). Agents: [AGENTS.md](AGENTS.md) → [docs/design-principles.md](docs/design-principles.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
