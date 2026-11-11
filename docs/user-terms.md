# User terms

Docs and the API reuse a small set of words — AOI, query CRS, mosaic CRS, CoverageId, TileMatrixSet. Mixing them up (especially query vs mosaic CRS, or CoverageId vs URL slug) is the usual source of “wrong CRS” and empty fetches.

Short definitions below. Deeper CRS contract: {doc}`design-principles`. Agent-oriented domain notes live in the repository `GLOSSARY.md` (not this site).

## AOI

**Area of interest** — the geographic window you ask for, usually as a `bbox` `(minx, miny, maxx, maxy)` plus a query CRS. The returned mosaic may snap or expand to the tile grid (especially XYZ / WMTS); `bbox_requested` keeps your original window.

## Query CRS

How you **describe** the bbox. Pass it as keyword **`bbox_crs=`** on `create_array` / `preview_array` / `load_array`. Recorded on the array as `attrs["bbox_crs"]`. Any pyproj-valid CRS works (presets such as `CRS.EPSG_4326`, or strings like `"EPSG:25833"`).

## Mosaic CRS

The CRS of the **returned pixels and transform** — `attrs["mosaic_crs"]`. This is not always the same as the query CRS:

| Path | Mosaic CRS |
|------|------------|
| WCS / WMS | Matches the query CRS (`bbox_crs=`) |
| XYZ / slippy WMTS | `EPSG:3857` (Web Mercator tile-union extent) |
| WMTS with TileMatrixSet metadata | Matrix SupportedCRS (tile-aligned extent) |

tilearray does **not** warp pixels client-side. Service / request CRS (config `crs=`, GetMap CRS, matrix SupportedCRS) is how the library talks to the provider — separate from both query and mosaic labelling. See {doc}`how-it-works`.

## GetCapabilities and CoverageId

**GetCapabilities** is the OGC document that lists what a WMS / WMTS / WCS host advertises (layers, coverages, formats, CRS, matrices). {py:func}`~tilearray.inspect_endpoint` fetches and summarises it.

**CoverageId** is the WCS identifier for a numerical coverage. It often looks like `UUID__LayerName` and is **not** the URL path slug. Pick it from GetCapabilities (or the inspect summary), then pass it to `WCSConfig.from_url` / `build_config`. Wrong CoverageIds fail loudly before a useful mosaic exists.

## TileMatrixSet

A WMTS **tile pyramid definition**: native CRS (SupportedCRS), matrix levels, and tile layout. Name the product (for example Kartverket `utm33n`) when building a config; tilearray plans on that grid and labels the mosaic with the matrix SupportedCRS. Pick a level with `target_resolution=` (familiar spacing) or an explicit `tile_matrix=` id. Recipe: {doc}`guides/wmts`.
