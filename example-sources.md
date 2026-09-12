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
lidar-composite-digital-terrain-model-dtm-1m
Note: For other WCS endpoints, list coverages with GetCapabilities on the service URL and use the `CoverageId` from the response (often the final path segment of the service URL).


VOM (WMS)

https://environment.data.gov.uk/spatialdata/vegetation-object-model/wms?request=GetCapabilities&service=WMS&version=1.3.0
