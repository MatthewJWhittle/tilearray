# tilearray

tilearray turns remote tile services into **Dask**-backed **xarray** arrays for GIS and machine-learning work — one `create_array` call, polite to servers, fast when they're healthy.

```{admonition} Alpha
:class: warning

The API may change. Today **WCS**, **WMS**, **WMTS**, and **XYZ** share one request-and-decode base behind `create_array`.
```

## Start here

- {doc}`install` — Python 3.9+, `uv` or `pip`
- {doc}`quickstart` — WCS elevation and XYZ slippy tiles (live network)
- {doc}`vignettes/index` — offline-safe guides that build in CI
- {doc}`api/index` — public exports and configuration helpers

```{toctree}
:hidden:
:maxdepth: 2

install
quickstart
vignettes/index
api/index
```
