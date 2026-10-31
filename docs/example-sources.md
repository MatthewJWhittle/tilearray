# Example sources

Public endpoints and thin fetch presets for testing host quirks — **not** a product catalogue. Prefer the presets in {doc}`quickstart` where they apply; use `from_url` for everything else. Stay polite: identifiable User-Agent where required, and do not raise concurrency past a host's documented ceiling.

## OpenStreetMap raster tiles (XYZ)

- Template: `https://tile.openstreetmap.org/{z}/{x}/{y}.png`
- Prefer `XYZConfig.for_openstreetmap(zoom=...)`.
- OSMF wants an identifiable User-Agent and polite use — the preset applies fixed limits (max 2 in-flight, ~2 req/s, no AIMD).
- Open data; include © OpenStreetMap contributors when displaying.

## Environment Agency Lidar DTM (WCS)

- URL: `https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs`
- CoverageId: `13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m`
- Prefer `WCSConfig.for_ea_dsp(...)` with this coverage id.
- The CoverageId comes from GetCapabilities (often `UUID__LayerName`) — never assume the URL path slug is the id.
- Preset AIMD: start 8, ceiling **10**, floor 4, ×0.75 pressure, sustained-403 circuit breaker. For county-scale cold mosaics, keep `max_concurrent_requests` ≤ **10**.

## NASA GIBS Blue Marble (XYZ)

- Template: `https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg`
- Path is `{z}/{y}/{x}` (row before col), not OSM `{z}/{x}/{y}`.
- Use `XYZConfig.from_url` with that template. JPEG colour mosaics keep RGB bands `(y, x, band)`.
- Public; coordinate if you exceed roughly 1M tiles / 24h. Layer is hardcoded in the path — another GIBS product needs its own template from GetCapabilities.

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

## OS Maps API ZXY (needs key)

- Template: `https://api.os.uk/maps/raster/v1/zxy/Road_3857/{z}/{x}/{y}.png?key=…`
- Useful for real HTTP 429 throttle behaviour. Attribution required. Do not commit keys.

## Other XYZ (restricted)

Google Satellite (`http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}`) can exercise XYZ wiring, but usage is restricted — prefer the open sources above for docs, demos, and production.
