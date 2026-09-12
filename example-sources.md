These are public example endpoints and thin config examples for testing service quirks — not a catalogue of first-class product integrations.

Google Satellite Imagery (X,Y,Z)
http://mt0.google.com/vt/lyrs=s&hl=en&x={x}&y={y}&z={z}
Note: Google tiles are useful as a technical example but have usage restrictions; prefer open sources below for production.


OpenStreetMap raster tiles (XYZ)
https://tile.openstreetmap.org/{z}/{x}/{y}.png
Open data; include © OpenStreetMap contributors attribution when displaying.


EA Lidar Digital Terrain Model (WCS)
URL:
https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-1m/wcs
Layer ID (CoverageId):
13787b9a-26a4-4775-8523-806d13af58fc__Lidar_Composite_Elevation_DTM_1m
Note: For EA and many ArcGIS-style WCS endpoints, CoverageId comes from GetCapabilities (often UUID__LayerName); do not assume the URL path slug. List coverages with GetCapabilities on the service URL.


VOM (WMS)

https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms?request=GetCapabilities&service=WMS&version=1.3.0
