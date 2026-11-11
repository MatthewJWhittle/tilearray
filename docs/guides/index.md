# Usage guides

Live hosts are where protocol quirks and CRS surprises show up. Each guide walks one host job to a plotted mosaic with a figure you can check against — better than reading API shapes alone.

Needs network access. Each guide has one job, one figure path, and a success check. Background: {doc}`../how-it-works`. First success without choosing a host: {doc}`../quickstart`.

```{toctree}
:maxdepth: 1

osm-london
gibs-bluemarble
ea-lidar
ea-vom-wms
wmts
```

| Guide | Host / area | Success check |
|-------|-------------|-----------------|
| {doc}`osm-london` | OpenStreetMap XYZ · central London | ~`(1024, 512, 3)`, mosaic `EPSG:3857`, street mosaic |
| {doc}`gibs-bluemarble` | NASA GIBS XYZ · mid-Atlantic | RGB JPEG, mosaic `EPSG:3857` |
| {doc}`ea-lidar` | EA Lidar WCS · OSGB window | Elevation map + finite mean, mosaic `EPSG:27700` |
| {doc}`ea-vom-wms` | EA Vegetation Object Model WMS · OSGB | RGBA mosaic, mosaic matches query CRS |
| {doc}`wmts` | Kartverket topo WMTS · Oslo | Mosaic `EPSG:25833`, ~10 m matrix |

Which guide for which job (how-to index): {doc}`worked-examples`.
