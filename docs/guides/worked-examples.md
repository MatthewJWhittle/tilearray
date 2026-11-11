# Which guide for which job

You should not have to read every tutorial to find the one that matches your job (XYZ street mosaic vs WCS elevation vs native-grid WMTS). Use this index to jump straight there.

Each guide is a visual tutorial with a figure and a success check. Interactive notebook (optional): `notebooks/community_worked_examples.ipynb` in the repository. Stay polite: modest bboxes, fetch presets where they exist, no bulk OSM scrape. Attribution where the host requires it.

| Job | Go to |
|-----|-------|
| Georeferenced XYZ street mosaic | {doc}`osm-london` |
| Truecolor RGB XYZ (template `{z}/{y}/{x}`) | {doc}`gibs-bluemarble` |
| Numerical WCS elevation under size / edge limits | {doc}`ea-lidar` |
| WMS rendered imagery (RGBA) | {doc}`ea-vom-wms` |
| Native-grid WMTS (query CRS ≠ mosaic CRS) | {doc}`wmts` |
| Unknown URL → layer / CoverageId / matrix | {doc}`../inspect-endpoint` |
| Many noncontiguous sample boxes / ML chips | {doc}`../multi-aoi-ml-patches` |
| Host ceilings, soft-fail, multi-process | {doc}`../polite-fetch` |
| Config / CRS mistakes and loud failures | {doc}`../troubleshooting` |
| Copy-paste public endpoints | {doc}`../example-sources` |

Five-minute first success (OSM only): {doc}`../quickstart`.
