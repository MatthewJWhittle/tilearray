# tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays — one `create_array` call for WCS, WMS, WMTS, or XYZ.

**Why it exists.** Assembling a large area of interest (AOI) — the window you ask for — from OGC or XYZ tiles by hand means wiring capabilities, planning a grid, polite concurrent HTTP, decode, and a correctly labelled mosaic. That glue is brittle and easy to get wrong on CRS, extent, and host limits. tilearray owns **plan → fetch → decode → assemble** so the array you get is analysis-ready.

**Who it is for.** Geo data engineers who need a big window (or many small ones) from a public or private tile host into xarray/Dask — for plotting, analysis, or ML chips — without building a custom client per protocol.

**Fit.** tilearray is a **data-access** library. Use [rioxarray](https://corteva.github.io/rioxarray/) / GDAL / rasterio for local files, warping, and GIS processing; use tilearray when the source is still remote tiles. It is not a map SDK, STAC/COG cube builder, or general GIS suite — see {doc}`design-principles`.

```{admonition} Alpha — documented against 0.3.2
:class: warning

Examples use the **0.3.2** public names: `compute_mosaic`, `preview_array`, `bbox_crs=`, and mosaic attrs `mosaic_crs` / `bbox_crs`. The API may still change in later alphas.
```

## Start here

1. {doc}`install` — install the package (live-host examples need network)
2. {doc}`quickstart` — five-minute OSM first success + CRS cheat sheet
3. {doc}`guides/index` — visual host tutorials (OSM, GIBS, EA Lidar / WMS, WMTS)

## How-to

- {doc}`inspect-endpoint` — discover layers and build a config from an unknown URL
- {doc}`polite-fetch` — host ceilings, soft-fail, ``**service_options``, multi-process scaling
- {doc}`troubleshooting` — when fetches fail or shapes look wrong
- {doc}`multi-aoi-ml-patches` — many sample bboxes / ML chips (one mosaic per bbox)
- {doc}`example-sources` — public endpoints and presets
- {doc}`guides/worked-examples` — which guide for which job

## Explanation

- {doc}`how-it-works` — plan → fetch → decode → mosaic → lazy/`compute_mosaic`
- {doc}`user-terms` — AOI, query CRS, mosaic CRS, CoverageId, TileMatrixSet

## Reference

- {doc}`api/index` — public API

## Also useful

- {doc}`design-principles` — product ethos, CRS roles, scope
- {doc}`roadmap` — coverage today, direction, non-goals

```{toctree}
:caption: Start here
:hidden:
:maxdepth: 1

install
quickstart
guides/index
```

```{toctree}
:caption: How-to
:hidden:
:maxdepth: 1

inspect-endpoint
polite-fetch
troubleshooting
multi-aoi-ml-patches
example-sources
guides/worked-examples
```

```{toctree}
:caption: Explanation
:hidden:
:maxdepth: 1

how-it-works
user-terms
```

```{toctree}
:caption: Reference
:hidden:
:maxdepth: 1

api/index
```

```{toctree}
:caption: Also useful
:hidden:
:maxdepth: 1

design-principles
roadmap
```
