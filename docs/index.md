# tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays — one `create_array` call for WCS, WMS, WMTS, or XYZ.

```{admonition} Alpha
:class: warning

The API may change.
```

## Start here

1. {doc}`install` — install the package
2. {doc}`quickstart` — copy-paste recipes against public hosts
3. {doc}`inspect-endpoint` — unknown WMS/WMTS/WCS URL → valid config
4. {doc}`how-it-works` — what happens for a large AOI (plan, fetch, lazy mosaic)
5. {doc}`multi-aoi-ml-patches` — many sample bboxes / ML chips (one mosaic per bbox)
6. {doc}`guides/index` — visual examples (OSM, NASA GIBS, EA Lidar, WMTS)
7. {doc}`example-sources` — public endpoints and presets
8. {doc}`troubleshooting` — when fetches fail or shapes look wrong
9. {doc}`api/index` — public API reference

## Also useful

- {doc}`design-principles` — product ethos, CRS roles, scope
- {doc}`roadmap` — coverage today, direction, non-goals

```{toctree}
:hidden:
:maxdepth: 2

install
quickstart
inspect-endpoint
how-it-works
multi-aoi-ml-patches
guides/index
example-sources
troubleshooting
api/index
design-principles
roadmap
```


```{toctree}
:caption: Contributing
:hidden:
:maxdepth: 1

building-docs
docs-writing-principles
api-freeze-checklist
```
