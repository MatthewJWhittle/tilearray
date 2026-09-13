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
Prefer `WCSConfig.for_ea_dsp(...)` with this coverage id. The CoverageId comes from GetCapabilities (often `UUID__LayerName`) — never assume the URL path slug is the id. The preset uses AIMD concurrency (start 8, ceiling 32, floor 4, ×0.75 pressure, remembers last good limit in-process, no fixed req/s cap) rather than a fixed 1 req/s throttle.


Peer stress targets

Thin configs / docs only — not a product catalogue.

NASA GIBS XYZ (happy-path peer, no key)
https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8/{z}/{y}/{x}.jpeg
Quirk: path is `{z}/{y}/{x}` (row before col), not OSM `{z}/{x}/{y}`. Use `XYZConfig.from_url` with that template. JPEG colour mosaics preserve RGB bands `(y, x, band)` via the shared decode pipeline. Public; coordinate if >~1M tiles/24h.
Quirk: URL template hardcodes `BlueMarble_NextGeneration/default/GoogleMapsCompatible_Level8` — layer-specific; another GIBS layer needs a new template from GetCapabilities, not placeholder swaps alone.

OS Maps API ZXY (UK + clean 429 — needs free Data Hub key)
https://api.os.uk/maps/raster/v1/zxy/Road_3857/{z}/{x}/{y}.png?key=…
Test: real HTTP 429 throttle (50/min dev or 600/min live). Attribution required. Don't commit keys.

USGS 3DEP WCS (second WCS shape)
https://elevation.nationalmap.gov/arcgis/services/3DEPElevation/ImageServer/WCSServer
Test: ArcGIS WCS 2.0.1 vs EA DSP CoverageId quirks. Public USGS elevation.


EA Vegetation Object Model (WMS)
https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms
Use `WMSConfig.from_url(..., layers=…)` — no fetch preset; thin GetMap adapter only.
Quirk: layer id is year-suffixed (e.g. `Vegetation_Object_Model_2022` from GetCapabilities — don't copy a stale year).
Quirk: transparent GetMap PNG decodes to 4 bands (RGBA), not 3.
