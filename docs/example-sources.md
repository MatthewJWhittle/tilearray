# Example sources

You need real host URLs, CoverageIds, and polite presets to exercise quirks (template order, EA ceilings, keyed XYZ) without inventing a provider catalogue. Copy-paste the endpoints below when a guide or troubleshooting recipe names a host.

Prefer `from_url` + `tilearray.fetch_presets` helpers; use bare `from_url` when no preset exists. Stay polite: identifiable User-Agent where required, and do not raise concurrency past a host's documented ceiling. First success: {doc}`quickstart`. Visual walkthroughs: {doc}`guides/index`.

## OpenStreetMap raster tiles (XYZ)

- Template: `https://tile.openstreetmap.org/{z}/{x}/{y}.png` (`OSM_TILE_TEMPLATE`)
- Prefer `XYZConfig.from_url(OSM_TILE_TEMPLATE, zoom=..., **osm_fetch_defaults())`.
- OSMF wants an identifiable User-Agent and polite use — the preset applies fixed limits (max 2 in-flight, ~2 req/s, no AIMD).
- Open data; include © OpenStreetMap contributors when displaying.

## Environment Agency Lidar DTM (WCS)

- URL: `https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`
- CoverageId: `13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m`
- Prefer `WCSConfig.from_url(url, coverage_id, **ea_dsp_fetch_defaults())` with this coverage id.
- The CoverageId comes from GetCapabilities (often `UUID__LayerName`) — never assume the URL path slug is the id.
- Preset AIMD: start 8, ceiling **10**, floor 4, ×0.75 pressure, sustained-403 circuit breaker. For county-scale cold mosaics, keep `max_concurrent_requests` ≤ **10**.

## NASA GIBS Blue Marble (XYZ or WMTS)

- XYZ template: `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`
- Path is `{z}/{y}/{x}` (row before col), not OSM `{z}/{x}/{y}`.
- Prefer `XYZConfig.from_url` with that template — see {doc}`guides/gibs-bluemarble`. WMTS discovery is available but XYZ is the simpler GIBS glance.
- JPEG colour mosaics keep RGB bands `(y, x, band)`.
- Public; coordinate if you exceed roughly 1M tiles / 24h. Layer is hardcoded in the path — another GIBS product needs its own template from GetCapabilities.

## Kartverket topo (WMTS, EPSG:25833)

- KVP endpoint: `https://cache.kartverket.no/v1/service`
- GetCapabilities: `https://cache.kartverket.no/v1/wmts/1.0.0/WMTSCapabilities.xml`
- Layer `topo`, TileMatrixSet `utm33n` (SupportedCRS EPSG:25833); also `utm32n` / `utm35n` / `webmercator`.
- Happy path: `inspect_endpoint` → `build_config(layer="topo", tile_matrix_set="utm33n", target_resolution=10.0, style="default")`. Explicit `tile_matrix=8` (caps id `08`) still works when you already know the id.
- Query bbox may be lon/lat; the mosaic is labelled EPSG:25833 with tile-aligned extent. Attribution: © Kartverket (CC BY 4.0). Stay polite — small windows, identifiable User-Agent. Guide: {doc}`guides/wmts`.

## USGS 3DEP elevation (WCS)

- URL: `https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WCSServer`
- CoverageId: `DEP3Elevation`
- Use `WCSConfig.from_url(..., coverage_id="DEP3Elevation", crs=EPSG:3857)` — no fetch preset.
- DescribeCoverage supplies native CRS and subset axes (ArcGIS `x`/`y` in EPSG:3857). GetCoverage is often `multipart/related`; decode unwraps the GeoTIFF part.

## Environment Agency Vegetation Object Model (WMS)

- URL: `https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms`
- Use `WMSConfig.from_url(..., layers=...)` — no fetch preset; thin GetMap adapter.
- Layer id is year-suffixed (e.g. `Vegetation_Object_Model_2022` from GetCapabilities — confirm the year).
- Transparent GetMap PNG decodes to 4 bands (RGBA), not 3.
- Visual walkthrough: {doc}`guides/ea-vom-wms`.

## OS Maps API ZXY (needs key)

- Template (no secret in the URL): `https://api.os.uk/maps/raster/v1/zxy/Road_3857/{z}/{x}/{y}.png`
- Pass the key via config `params` from the environment — prefer this over `?key=…` in the template:

```python
import os
from tilearray import XYZConfig, compute_mosaic, create_array
from tilearray.types import CRS

config = XYZConfig.from_url(
    "https://api.os.uk/maps/raster/v1/zxy/Road_3857/{z}/{x}/{y}.png",
    zoom=10,
    params={"key": os.environ["OS_MAPS_API_KEY"]},
)
da = create_array(config, bbox=(-0.1, 51.4, 0.0, 51.5), bbox_crs=CRS.EPSG_4326)
arr = compute_mosaic(da)
```

- Useful for real HTTP 429 throttle behaviour. Attribution required. Do not commit keys. Same `params=` / `headers=` pattern covers other keyed or private tile hosts — see {doc}`troubleshooting`.

## Other XYZ (restricted)

Google Satellite (`http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}`) can exercise XYZ wiring, but usage is restricted — prefer the open sources above for docs, demos, and production.
