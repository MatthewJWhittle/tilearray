Public example endpoints and thin fetch presets for testing host quirks — not first-class product integrations. Prefer the presets in README quick start where they apply; use `from_url` for everything else.


Google Satellite Imagery (X,Y,Z)
http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}
Note: Google tiles are useful as a technical example but have usage restrictions; prefer open sources below for production.


OpenStreetMap raster tiles (XYZ)
https://tile.openstreetmap.org/{z}/{x}/{y}.png
Prefer `XYZConfig.for_openstreetmap(zoom=...)`. The OpenStreetMap Foundation (OSMF) wants an identifiable User-Agent and polite use — the preset applies fixed limits (max 2 in-flight, ~2 req/s, no AIMD). Open data; include © OpenStreetMap contributors attribution when displaying.


EA Lidar Digital Terrain Model (WCS)
URL:
https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs
Layer ID (CoverageId):
13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m
Prefer `WCSConfig.for_ea_dsp(...)` with this coverage id. The CoverageId comes from GetCapabilities (often `UUID__LayerName`) — never assume the URL path slug is the id. The preset uses AIMD concurrency (starts at 8 in-flight, ceiling 32, remembers last good limit in-process, no fixed req/s cap) rather than a fixed 1 req/s throttle.


VOM (WMS)

https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms?request=GetCapabilities&service=WMS&version=1.3.0
