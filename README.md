# tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays — one `create_array` call for WCS, WMS, WMTS, or XYZ. Shared fetch and decode keep mosaics polite to servers and analysis-ready on a laptop or a larger machine.

[![Tests](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/test.yml)
[![Docs](https://github.com/MatthewJWhittle/tilearray/actions/workflows/docs.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/docs.yml)
[![Build and Publish](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml/badge.svg)](https://github.com/MatthewJWhittle/tilearray/actions/workflows/build.yml)

> **Alpha.** The API may change. Today **WCS**, **WMS**, **WMTS**, and **XYZ** share one `create_array` entry point.

**Docs:** [matthewjwhittle.github.io/tilearray](https://matthewjwhittle.github.io/tilearray/) — [install](https://matthewjwhittle.github.io/tilearray/install.html), [how it works](https://matthewjwhittle.github.io/tilearray/how-it-works.html), [quick start](https://matthewjwhittle.github.io/tilearray/quickstart.html), [usage guides](https://matthewjwhittle.github.io/tilearray/guides/index.html), [example sources](https://matthewjwhittle.github.io/tilearray/example-sources.html), [troubleshooting](https://matthewjwhittle.github.io/tilearray/troubleshooting.html), and [API](https://matthewjwhittle.github.io/tilearray/api/index.html).

## Install

Python **3.9+**. Prefer [uv](https://github.com/astral-sh/uv):

```bash
uv add tilearray
```

or:

```bash
pip install tilearray
```

From a clone: `uv sync --dev`. Contributors: `make docs` (HTML), `make docs-artifacts` (regenerate figures).

## Quick start

Prefer **`compute_with_policy`** over plain `.compute()` so Dask workers match the fetch ceiling (attrs are read automatically).

### XYZ — OpenStreetMap

```python
from tilearray import XYZConfig, compute_with_policy, create_array
from tilearray.types import CRS

config = XYZConfig.for_openstreetmap(zoom=16)
da = create_array(
    config,
    bbox=(-0.005, 51.495, 0.005, 51.505),  # small window near central London
    crs=CRS.EPSG_4326,
)
arr = compute_with_policy(da)
# Palette PNGs decode as single-band; this window is about (1024, 512)
print(arr.shape, da.attrs["service_type"])
```

Include © OpenStreetMap contributors when displaying map imagery.

### WCS — Environment Agency Lidar

```python
from tilearray import WCSConfig, compute_with_policy, create_array
from tilearray.types import CRS, Format

config = WCSConfig.for_ea_dsp(
    "https://environment.data.gov.uk/spatialdata/"
    "lidar-composite-digital-terrain-model-dtm-1m/wcs",
    coverage_id="13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m",
    crs=CRS.EPSG_27700,
    output_format=Format.GEOTIFF,
    chunk_size=(800, 800),
    grid_shape=(1, 1),
)
bbox = (431900.0, 382700.0, 432700.0, 383500.0)  # British National Grid
da = create_array(config, bbox, CRS.EPSG_27700)
elevation = compute_with_policy(da)
print(elevation.shape, float(elevation.mean()))
```

The EA Lidar preset caps sustained concurrency at **10** (AIMD ceiling). More hosts, WMS/WMTS recipes, and polite-use notes live in the [docs](https://matthewjwhittle.github.io/tilearray/).

## Development

Clone, `uv sync --dev`, then `make test` / `make lint`. Full setup, docs builds, and project conventions: [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache License 2.0 — see [LICENSE](LICENSE).
