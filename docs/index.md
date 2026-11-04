# tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays — one `create_array` call for WCS, WMS, WMTS, or XYZ.

**Why it exists.** Assembling a large AOI from OGC or XYZ tiles by hand means wiring capabilities, planning a grid, polite concurrent HTTP, decode, and a correctly labelled mosaic. That glue (OWSLib + Pillow + Affine maths, or a GDAL/VRT stack) is brittle and easy to get wrong on CRS, extent, and host limits. tilearray owns **plan → fetch → decode → assemble** so the array you get is analysis-ready.

**Who it is for.** Geo data engineers who need a big window (or many small ones) from a public or private tile host into xarray/Dask — for plotting, analysis, or ML chips — without building a custom client per protocol.

**Fit.** tilearray is a **data-access** library. Use [rioxarray](https://corteva.github.io/rioxarray/) / GDAL / rasterio for local files, warping, and GIS processing; use tilearray when the source is still remote tiles. It is not a map SDK, STAC/COG cube builder, or general GIS suite — see {doc}`design-principles`.

```{admonition} Alpha
:class: warning

The API may change.
```

## Start here

1. {doc}`install` — install the package
2. {doc}`quickstart` — five-minute first run (OSM plot + CRS), then other protocols
3. {doc}`inspect-endpoint` — discover layers and build a config from an unknown WMS/WMTS/WCS URL
4. {doc}`how-it-works` — plan, fetch, lazy mosaic (short mental model)
5. {doc}`guides/index` — visual examples (OSM, NASA GIBS, EA Lidar, WMTS)
6. {doc}`troubleshooting` — when fetches fail or shapes look wrong
7. {doc}`api/index` — public API reference

## Also useful

- {doc}`example-sources` — public endpoints and presets
- {doc}`multi-aoi-ml-patches` — many sample bboxes / ML chips (one mosaic per bbox)
- {doc}`design-principles` — product ethos, CRS roles, scope
- {doc}`roadmap` — coverage today, direction, non-goals

```{toctree}
:hidden:
:maxdepth: 2

install
quickstart
inspect-endpoint
how-it-works
guides/index
troubleshooting
api/index
example-sources
multi-aoi-ml-patches
design-principles
roadmap
```
